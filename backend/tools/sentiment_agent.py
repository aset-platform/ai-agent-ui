"""Sentiment agent tools for LangGraph supervisor.

Three ``@tool``-decorated functions that the Sentiment
Agent uses to answer chat queries about market and stock
sentiment.

Hybrid UX: cached score returned instantly; live refresh
offered when data is stale (>24 h).
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from langchain_core.tools import tool

_logger = logging.getLogger(__name__)

# Broad market indices scored alongside portfolio.
_BROAD_INDICES = [
    "SPY",
    "^GSPC",
    "^DJI",
    "^IXIC",
]


def _get_repo():
    """Lazy import to avoid circular deps at import time."""
    from tools._stock_shared import _get_repo as gr

    return gr()


def _get_llm():
    """Build a FallbackLLM for sentiment scoring.

    Uses the same factory pattern as the graph builder
    so that calls are traced via LangSmith and counted
    against the shared token budget.
    """
    try:
        from config import get_settings
        from llm_fallback import FallbackLLM
        from message_compressor import (
            MessageCompressor,
        )
        from token_budget import get_token_budget

        settings = get_settings()

        def _parse(csv: str) -> list[str]:
            return [t.strip() for t in csv.split(",") if t.strip()]

        env = settings.ai_agent_ui_env
        if env == "test":
            tiers = _parse(settings.test_model_tiers)
            anthropic = None
        else:
            tiers = _parse(settings.groq_model_tiers)
            anthropic = "claude-sonnet-4-6"

        ollama = (
            settings.ollama_model
            if settings.ollama_enabled
            else None
        )

        return FallbackLLM(
            groq_models=tiers,
            anthropic_model=anthropic,
            temperature=0,
            agent_id="sentiment",
            token_budget=get_token_budget(),
            compressor=MessageCompressor(),
            cascade_profile="tool",
            ollama_model=ollama,
            ollama_first=True,
        )
    except Exception:
        _logger.debug(
            "FallbackLLM init failed for sentiment",
            exc_info=True,
        )
        return None


def _format_score(score: float) -> str:
    """Convert numeric score to a human label."""
    if score >= 0.3:
        return f"Bullish ({score:+.2f})"
    if score <= -0.3:
        return f"Bearish ({score:+.2f})"
    return f"Neutral ({score:+.2f})"


def _staleness_note(score_date) -> str:
    """Return a staleness warning if >24 h old."""
    if score_date is None:
        return ""
    try:
        sd = pd.Timestamp(score_date).date()
        age = (date.today() - sd).days
        if age > 1:
            return f" (scored {age} days ago — consider " "refreshing)"
        if age == 1:
            return " (scored yesterday)"
        return " (scored today)"
    except Exception:
        return ""


# ------------------------------------------------------------------
# Agent tools
# ------------------------------------------------------------------


@tool
def score_ticker_sentiment(
    ticker: str,
    days_back: int = 7,
) -> str:
    """Score live sentiment for a stock ticker.

    Fetches headlines from the last ``days_back`` days
    from yfinance, Yahoo RSS, and Google RSS.  Scores
    via FallbackLLM with time-decay weighting (recent
    headlines count more) and persists to Iceberg.

    Args:
        ticker: Stock ticker symbol (e.g. AAPL,
            RELIANCE.NS).
        days_back: Number of days of headlines to score.
            Defaults to 7.  Use 30 for "last month",
            90 for "last quarter".

    Returns:
        Natural language summary of the sentiment score.
    """
    from tools._sentiment_scorer import (
        refresh_ticker_sentiment,
    )

    llm = _get_llm()
    avg = refresh_ticker_sentiment(
        ticker, llm=llm, max_age_days=days_back,
    )

    if avg is None:
        return (
            f"Could not score sentiment for {ticker} — "
            "no headlines available from any source."
        )

    label = _format_score(avg)
    return (
        f"**Sentiment for {ticker}**: {label}\n\n"
        f"Scored from the latest news headlines across "
        f"yfinance, Yahoo RSS, and Google RSS. "
        f"Score persisted to Iceberg for use in "
        f"forecasting."
    )


@tool
def get_cached_sentiment(ticker: str) -> str:
    """Get the most recent cached sentiment score.

    Returns the latest score from Iceberg with its date.
    If the score is stale (older than 24 hours), suggests
    refreshing with ``score_ticker_sentiment``.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Cached sentiment score with freshness indicator.
    """
    repo = _get_repo()
    if repo is None:
        return (
            f"Sentiment data unavailable for {ticker} "
            "— repository not accessible."
        )

    series = repo.get_sentiment_series(ticker)
    if series.empty:
        return (
            f"No cached sentiment for {ticker}. "
            "Use score_ticker_sentiment to fetch and "
            "score live headlines."
        )

    latest = series.iloc[-1]
    score = float(latest["avg_score"])
    sd = latest["score_date"]
    count = int(latest.get("headline_count", 0))
    source = latest.get("source", "unknown")

    label = _format_score(score)
    stale = _staleness_note(sd)

    return (
        f"**Cached sentiment for {ticker}**: "
        f"{label}{stale}\n"
        f"Headlines scored: {count} | "
        f"Source: {source} | Date: {sd}"
    )


@tool
def get_market_sentiment() -> str:
    """Get aggregate market sentiment.

    Aggregates sentiment across the user's portfolio
    tickers and broad indices (SPY, ^GSPC, ^DJI, ^IXIC).
    Returns overall mood and top bullish/bearish movers.

    Returns:
        Market sentiment summary with per-ticker breakdown.
    """
    repo = _get_repo()
    if repo is None:
        return "Market sentiment unavailable — repository not accessible."

    # Collect all tickers: portfolio + broad indices.
    tickers: list[str] = list(_BROAD_INDICES)
    try:
        registry = repo.get_all_registry()
        if registry:
            tickers.extend(t for t in registry.keys() if t not in tickers)
    except Exception:
        pass

    scores: list[tuple[str, float, str]] = []
    for ticker in tickers:
        try:
            series = repo.get_sentiment_series(ticker)
            if series.empty:
                continue
            latest = series.iloc[-1]
            s = float(latest["avg_score"])
            sd = str(latest["score_date"])
            scores.append((ticker, s, sd))
        except Exception:
            continue

    if not scores:
        return (
            "No sentiment data available. Run the "
            "daily sentiment batch or use "
            "score_ticker_sentiment for individual "
            "tickers."
        )

    # Aggregate.
    avg_all = sum(s for _, s, _ in scores) / len(scores)
    label = _format_score(avg_all)

    # Top movers.
    sorted_scores = sorted(
        scores,
        key=lambda x: x[1],
        reverse=True,
    )
    top_bull = sorted_scores[:3]
    top_bear = sorted_scores[-3:]

    lines = [
        f"**Overall Market Sentiment**: {label} " f"({len(scores)} tickers)\n",
        "**Top Bullish:**",
    ]
    for t, s, d in top_bull:
        lines.append(
            f"  - {t}: {_format_score(s)} ({d})",
        )

    lines.append("\n**Top Bearish:**")
    for t, s, d in top_bear:
        lines.append(
            f"  - {t}: {_format_score(s)} ({d})",
        )

    return "\n".join(lines)

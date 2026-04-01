"""LLM-based sentiment scoring for stock news headlines.

Scores headlines from multiple sources on a -1.0 (bearish) to
+1.0 (bullish) scale using a weighted average that accounts for
source reliability.

Uses FallbackLLM for full LangSmith/LangFuse observability.
No new dependencies — leverages the existing LLM stack.

Public API
----------
- :func:`score_headlines` — score a list of HeadlineItems
- :func:`refresh_ticker_sentiment` — end-to-end fetch+score+persist
- :func:`fetch_news_headlines` — legacy wrapper (deprecated)
- :func:`score_headlines_llm` — legacy wrapper (deprecated)
- :func:`compute_sentiment_regressor` — Prophet regressor builder
"""

from __future__ import annotations

import json
import logging
import re

import pandas as pd

from tools._sentiment_sources import (
    HeadlineItem,
    fetch_all_headlines,
)

_logger = logging.getLogger(__name__)

_SENTIMENT_PROMPT = (
    "You are a financial sentiment analyst. "
    "Rate each news headline on a scale from "
    "-1.0 (very bearish) to +1.0 (very bullish). "
    "0.0 means neutral.\n\n"
    "Return ONLY a JSON array of numbers, one per "
    "headline. Example: [-0.3, 0.7, 0.0]\n\n"
    "Headlines:\n{headlines}"
)


# ------------------------------------------------------------------
# Core scoring
# ------------------------------------------------------------------


def _parse_scores(raw: str, count: int) -> list[float]:
    """Extract float scores from LLM response."""
    # Try JSON parse first.
    try:
        arr = json.loads(raw)
        if isinstance(arr, list) and len(arr) == count:
            return [max(-1.0, min(1.0, float(x))) for x in arr]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Fallback: extract all floats from text.
    nums = re.findall(r"-?\d+\.?\d*", raw)
    if len(nums) >= count:
        return [max(-1.0, min(1.0, float(x))) for x in nums[:count]]

    _logger.debug(
        "Could not parse %d scores from: %s",
        count,
        raw[:200],
    )
    return [0.0] * count


def score_headlines(
    headlines: list[HeadlineItem],
    llm=None,
) -> float | None:
    """Score headlines via LLM, return weighted composite.

    Computes ``Σ(score × source_weight) / Σ(source_weight)``
    so that higher-trust sources contribute more.

    Args:
        headlines: Annotated headline items with weights.
        llm: LLM instance (FallbackLLM or any
            ``BaseChatModel``).  If ``None``, returns
            ``None``.

    Returns:
        Weighted average score in [-1.0, +1.0], or
        ``None`` if no headlines or LLM unavailable.
    """
    if not headlines:
        return None
    if llm is None:
        return None

    titles = [h.title for h in headlines]
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    prompt = _SENTIMENT_PROMPT.format(
        headlines=numbered,
    )

    try:
        from langchain_core.messages import (
            HumanMessage,
        )

        result = llm.invoke(
            [HumanMessage(content=prompt)],
        )
        text = result.content if hasattr(result, "content") else str(result)
        raw_scores = _parse_scores(text, len(headlines))
    except Exception as exc:
        _logger.warning(
            "Sentiment scoring failed: %s",
            exc,
        )
        return None

    from tools._date_utils import time_decay_weight

    # Weighted average with time-decay.
    total_weight = 0.0
    weighted_sum = 0.0
    for item, score in zip(headlines, raw_scores):
        decay = time_decay_weight(item.published)
        w = item.weight * decay
        weighted_sum += score * w
        total_weight += w

    if total_weight == 0:
        return 0.0

    avg = weighted_sum / total_weight
    return max(-1.0, min(1.0, avg))


def refresh_ticker_sentiment(
    ticker: str,
    llm=None,
    max_age_days: int = 7,
) -> float | None:
    """End-to-end: fetch → score → persist to Iceberg.

    This is the single shared code path used by both
    ``gap_filler.refresh_sentiment()`` and the sentiment
    agent tools.

    Idempotent: skips scoring if a row for today already
    exists in Iceberg.

    Args:
        ticker: Stock ticker symbol.
        llm: LLM instance for scoring.

    Returns:
        Average sentiment score, or ``None`` on failure.
    """
    from datetime import date

    try:
        from tools._stock_shared import _require_repo

        repo = _require_repo()
    except Exception:
        _logger.debug(
            "Repo unavailable for sentiment refresh",
        )
        return None

    # Skip if already scored today.
    existing = repo.get_sentiment_series(ticker)
    if not existing.empty:
        latest = pd.Timestamp(
            existing["score_date"].max(),
        ).date()
        if latest >= date.today():
            _logger.debug(
                "Sentiment for %s already fresh (%s)",
                ticker,
                latest,
            )
            return float(existing["avg_score"].iloc[-1])

    # Fetch from all sources.
    headlines = fetch_all_headlines(
        ticker, max_age_days=max_age_days,
    )
    if not headlines:
        _logger.info(
            "No headlines for %s, skipping",
            ticker,
        )
        return None

    # Score via LLM.
    avg = score_headlines(headlines, llm=llm)
    if avg is None:
        # LLM unavailable — write a zero-score row.
        repo.insert_sentiment_score(
            ticker,
            date.today(),
            0.0,
            headline_count=len(headlines),
            source="none",
        )
        return 0.0

    repo.insert_sentiment_score(
        ticker,
        date.today(),
        avg,
        headline_count=len(headlines),
        source="llm",
    )
    _logger.info(
        "Sentiment scored %s: %.3f (%d headlines, " "%d sources)",
        ticker,
        avg,
        len(headlines),
        len({h.source for h in headlines}),
    )
    return avg


# ------------------------------------------------------------------
# Legacy wrappers (backward compatibility)
# ------------------------------------------------------------------


def fetch_news_headlines(ticker: str) -> list[str]:
    """Fetch recent news headlines for a ticker.

    .. deprecated::
        Use :func:`fetch_all_headlines` from
        ``_sentiment_sources`` instead.
    """
    items = fetch_all_headlines(ticker)
    return [h.title for h in items]


def score_headlines_llm(
    headlines: list[str],
    llm=None,
) -> list[float]:
    """Score headline strings using the LLM.

    .. deprecated::
        Use :func:`score_headlines` with
        :class:`HeadlineItem` list instead.
    """
    if not headlines:
        return []
    if llm is None:
        return [0.0] * len(headlines)

    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
    prompt = _SENTIMENT_PROMPT.format(
        headlines=numbered,
    )

    try:
        from langchain_core.messages import (
            HumanMessage,
        )

        result = llm.invoke(
            [HumanMessage(content=prompt)],
        )
        text = result.content if hasattr(result, "content") else str(result)
        return _parse_scores(text, len(headlines))
    except Exception as exc:
        _logger.warning(
            "Sentiment scoring failed: %s",
            exc,
        )
        return [0.0] * len(headlines)


def compute_sentiment_regressor(
    ticker: str,
    prophet_df: pd.DataFrame,
    llm=None,
) -> pd.DataFrame | None:
    """Build a sentiment regressor for Prophet.

    Scores today's headlines and creates a constant
    sentiment value aligned to the training dates.  For
    a proper rolling average, historical sentiment data
    is read from Iceberg by ``_forecast_shared.py``.

    .. deprecated::
        The Iceberg-based regressor path in
        ``_forecast_shared._load_regressors_from_iceberg``
        is the production path.  This function is kept
        for backward compatibility only.
    """
    headlines = fetch_all_headlines(ticker)
    if not headlines:
        _logger.info(
            "No headlines for %s, skipping sentiment",
            ticker,
        )
        return None

    avg = score_headlines(headlines, llm=llm)
    if avg is None:
        avg = 0.0

    _logger.info(
        "Sentiment for %s: %.2f (%d headlines)",
        ticker,
        avg,
        len(headlines),
    )

    result = pd.DataFrame(
        {
            "ds": prophet_df["ds"],
            "sentiment": avg,
        }
    )
    return result

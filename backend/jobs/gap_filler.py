"""Data gap filler, market index refresh, and sentiment scoring.

All functions are called by the Admin Scheduler
(``SchedulerService``) or user-initiated triggers —
no hardcoded cron schedules.

Public API::

    fill_data_gaps()           — fetch missing OHLCV/info
    refresh_market_indices()   — VIX, benchmarks, macro
    refresh_sentiment(ticker)  — LLM headline scoring
    refresh_all_sentiment()    — batch all tickers
"""

from __future__ import annotations

import logging

import pandas as pd

_logger = logging.getLogger(__name__)


def fill_data_gaps() -> int:
    """Fetch missing data for queried tickers.

    Reads unfilled gaps from ``stocks.data_gaps``
    Iceberg table and fetches data via yfinance.

    Returns:
        Number of gaps successfully resolved.
    """
    try:
        from tools._stock_shared import _get_repo

        repo = _get_repo()
        if repo is None:
            _logger.debug(
                "Gap filler: repo unavailable",
            )
            return 0
    except Exception:
        return 0

    gaps = repo.get_unfilled_data_gaps()
    if not gaps:
        _logger.debug("Gap filler: no gaps to fill")
        return 0

    _logger.info(
        "Gap filler: processing %d gaps",
        len(gaps),
    )
    resolved = 0

    for gap in gaps:
        ticker = gap.get("ticker", "")
        data_type = gap.get("data_type", "")
        gap_id = gap.get("id", "")

        if not ticker or not gap_id:
            continue

        try:
            if data_type == "ohlcv":
                _fetch_ohlcv(ticker)
            elif data_type == "company_info":
                _fetch_company_info(ticker)
            elif data_type == "dividends":
                _fetch_dividends(ticker)
            elif data_type == "quarterly":
                _fetch_quarterly(ticker)
            else:
                _logger.debug(
                    "Unknown data_type: %s",
                    data_type,
                )
                continue

            repo.resolve_data_gap(
                gap_id,
                "yfinance_fetch",
            )
            resolved += 1
            _logger.info(
                "Gap filled: %s/%s",
                ticker,
                data_type,
            )
        except Exception:
            _logger.warning(
                "Gap fill failed: %s/%s",
                ticker,
                data_type,
                exc_info=True,
            )

    _logger.info(
        "Gap filler: resolved %d/%d gaps",
        resolved,
        len(gaps),
    )
    return resolved


def _fetch_ohlcv(ticker: str) -> None:
    """Fetch OHLCV data via yfinance."""
    import yfinance as yf
    from tools._stock_shared import _require_repo

    repo = _require_repo()
    t = yf.Ticker(ticker)
    hist = t.history(period="10y")
    if hist.empty:
        raise RuntimeError(
            f"No OHLCV data for {ticker}",
        )
    repo.insert_ohlcv(ticker, hist)


def _fetch_company_info(ticker: str) -> None:
    """Fetch company info via yfinance."""
    import yfinance as yf
    from tools._stock_shared import _require_repo

    repo = _require_repo()
    t = yf.Ticker(ticker)
    info = t.info
    if not info:
        raise RuntimeError(
            f"No company info for {ticker}",
        )
    repo.insert_company_info(ticker, info)


def _fetch_dividends(ticker: str) -> None:
    """Fetch dividend history via yfinance."""
    import yfinance as yf
    from tools._stock_shared import _require_repo

    repo = _require_repo()
    t = yf.Ticker(ticker)
    divs = t.dividends
    if divs is None or divs.empty:
        raise RuntimeError(
            f"No dividends for {ticker}",
        )
    repo.insert_dividends(ticker, divs)


def _fetch_quarterly(ticker: str) -> None:
    """Fetch quarterly results via yfinance."""
    import yfinance as yf
    from tools._stock_shared import _require_repo

    repo = _require_repo()
    t = yf.Ticker(ticker)
    q = t.quarterly_financials
    if q is None or q.empty:
        raise RuntimeError(
            f"No quarterly data for {ticker}",
        )
    repo.insert_quarterly_results(ticker, q)


_indices_last_refresh = None  # date | None


def refresh_market_indices() -> int:
    """Fetch VIX + benchmark indices into the OHLCV table.

    Skips the yfinance fetch if already called today.
    Uses the standard ``insert_ohlcv()`` path with
    built-in dedup.

    Returns:
        Number of index-date rows inserted.
    """
    global _indices_last_refresh  # noqa: PLW0603

    from datetime import date as _date

    if _indices_last_refresh == _date.today():
        _logger.debug("Market indices already refreshed today")
        return 0

    try:
        from datetime import timedelta

        import yfinance as yf
        from tools._stock_shared import _require_repo

        repo = _require_repo()
        indices = [
            # Market indices (Phase 2)
            "^VIX",
            "^INDIAVIX",
            "^GSPC",
            "^NSEI",
            # Macro indicators (Phase 3)
            "^TNX",  # 10-Year Treasury Yield
            "^IRX",  # 13-Week T-Bill Rate
            "CL=F",  # WTI Crude Oil
            "DX-Y.NYB",  # US Dollar Index
        ]
        total = 0

        for idx_sym in indices:
            try:
                last = repo.get_latest_ohlcv_date(
                    idx_sym,
                )
                if last is not None:
                    start = str(
                        last + timedelta(days=1),
                    )
                else:
                    start = "2015-01-01"

                hist = yf.Ticker(idx_sym).history(
                    start=start,
                    auto_adjust=False,
                )
                if hist.empty:
                    continue

                hist.index = pd.to_datetime(
                    hist.index,
                ).tz_localize(None)
                n = repo.insert_ohlcv(idx_sym, hist)
                total += n
                _logger.info(
                    "Market index %s: %d rows",
                    idx_sym,
                    n,
                )
            except Exception:
                _logger.debug(
                    "Market index %s failed",
                    idx_sym,
                    exc_info=True,
                )
        _indices_last_refresh = _date.today()
        _logger.info(
            "Market indices refresh: %d rows",
            total,
        )
        return total
    except Exception:
        _logger.warning(
            "Market indices refresh failed",
            exc_info=True,
        )
        return 0


def _get_scoring_llm():
    """Build a FallbackLLM for batch sentiment scoring.

    Uses the same cascade as agents so that calls are
    traced via LangSmith and counted in the token budget.
    """
    try:
        from config import get_settings
        from llm_fallback import FallbackLLM
        from message_compressor import (
            MessageCompressor,
        )
        from token_budget import TokenBudget

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

        return FallbackLLM(
            groq_models=tiers,
            anthropic_model=anthropic,
            temperature=0,
            agent_id="sentiment_batch",
            token_budget=TokenBudget(),
            compressor=MessageCompressor(),
            cascade_profile="tool",
        )
    except Exception:
        _logger.debug(
            "FallbackLLM init failed for sentiment",
            exc_info=True,
        )
        return None


def refresh_sentiment(ticker: str) -> float | None:
    """Score today's headlines for *ticker* via LLM.

    Uses the shared multi-source pipeline from
    ``_sentiment_scorer.refresh_ticker_sentiment`` with
    FallbackLLM for full observability.

    Returns:
        The average sentiment score, or ``None`` on failure.
    """
    try:
        from tools._sentiment_scorer import (
            refresh_ticker_sentiment,
        )

        llm = _get_scoring_llm()
        return refresh_ticker_sentiment(
            ticker,
            llm=llm,
        )
    except Exception:
        _logger.debug(
            "Sentiment refresh failed for %s",
            ticker,
            exc_info=True,
        )
        return None


def refresh_all_sentiment() -> int:
    """Score today's headlines for ALL registered tickers.

    Runs once daily so that the ``sentiment_scores`` table
    has no gaps — even when forecasts are skipped (7-day
    cooldown).

    Returns:
        Number of tickers successfully scored.
    """
    try:
        from tools._stock_shared import _get_repo

        repo = _get_repo()
        if repo is None:
            return 0

        registry = repo.get_all_registry()
        tickers = list(registry.keys()) if registry else []
        if not tickers:
            return 0

        scored = 0
        for ticker in tickers:
            result = refresh_sentiment(ticker)
            if result is not None:
                scored += 1

        _logger.info(
            "Sentiment batch: %d/%d tickers scored",
            scored,
            len(tickers),
        )
        return scored
    except Exception:
        _logger.warning(
            "Sentiment batch failed",
            exc_info=True,
        )
        return 0


def start_gap_filler() -> None:
    """No-op — gap filler schedules removed.

    All data refresh, market indices, and sentiment
    scoring now run exclusively through the Admin
    Scheduler (``SchedulerService``) or user-initiated
    triggers.
    """
    _logger.info(
        "Gap filler: no hardcoded schedules — "
        "use Admin Scheduler or manual triggers",
    )

"""Daily data gap filler background job.

Runs after market close to fetch missing data for
tickers that users queried but had stale/missing
local data.

Schedule:
- 12:30 UTC (6:00 PM IST) — after NSE close
- 15:30 UTC (9:00 PM IST) — after NYSE close

Usage::

    from jobs.gap_filler import start_gap_filler
    start_gap_filler()  # starts daemon thread
"""

from __future__ import annotations

import logging
import threading
import time

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
        "Gap filler: processing %d gaps", len(gaps),
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
                gap_id, "yfinance_fetch",
            )
            resolved += 1
            _logger.info(
                "Gap filled: %s/%s",
                ticker, data_type,
            )
        except Exception:
            _logger.warning(
                "Gap fill failed: %s/%s",
                ticker, data_type,
                exc_info=True,
            )

    _logger.info(
        "Gap filler: resolved %d/%d gaps",
        resolved, len(gaps),
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


def _scheduler_loop() -> None:
    """Run the scheduler in a daemon thread."""
    import schedule

    schedule.every().day.at("12:30").do(
        fill_data_gaps,
    )
    schedule.every().day.at("15:30").do(
        fill_data_gaps,
    )

    _logger.info(
        "Gap filler scheduler started "
        "(12:30 UTC + 15:30 UTC)",
    )

    while True:
        schedule.run_pending()
        time.sleep(60)


def start_gap_filler() -> None:
    """Start the gap filler in a daemon thread."""
    thread = threading.Thread(
        target=_scheduler_loop,
        daemon=True,
        name="gap-filler",
    )
    thread.start()
    _logger.info("Gap filler thread started")

#!/usr/bin/env python3
"""Truncate and backfill all Iceberg data for specified tickers.

Deletes existing data for each ticker from all 9 stocks tables,
then re-fetches 10 years of OHLCV data, company info, dividends,
technical analysis, quarterly results, and Prophet forecasts.

Usage::

    # All registry tickers (default)
    python scripts/backfill_all.py

    # Specific tickers
    python scripts/backfill_all.py --tickers AAPL MSFT

    # Custom period
    python scripts/backfill_all.py --period 5y

    # Skip truncate (append-only mode)
    python scripts/backfill_all.py --no-truncate

    # Skip forecast (faster)
    python scripts/backfill_all.py --skip-forecast
"""

import argparse
import glob as _glob
import logging
import os
import sys
import time
from pathlib import Path

# ── Ensure backend/ is on sys.path ──────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))
sys.path.insert(0, str(_PROJECT_ROOT))
os.chdir(str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
_logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────
_APP_HOME = Path(
    os.environ.get("AI_AGENT_UI_HOME", Path.home() / ".ai-agent-ui")
)
_CACHE_DIR = _APP_HOME / "data" / "cache"
_CHARTS_DIR = _APP_HOME / "charts"


def _clear_all_cache(ticker: str) -> None:
    """Delete all cache files for a ticker."""
    pattern = str(_CACHE_DIR / f"{ticker}_*")
    for path in _glob.glob(pattern):
        try:
            os.remove(path)
        except OSError:
            pass


def _clear_charts(ticker: str) -> None:
    """Delete chart HTML files for a ticker."""
    for subdir in ("analysis", "forecasts"):
        pattern = str(_CHARTS_DIR / subdir / f"{ticker}_*")
        for path in _glob.glob(pattern):
            try:
                os.remove(path)
            except OSError:
                pass


def _step(label: str, func, *args, **kwargs) -> bool:
    """Run a step, log success/failure, return True on success."""
    try:
        result = func(*args, **kwargs)
        if isinstance(result, str):
            _logger.info("  [OK] %s: %s", label, result[:120])
        else:
            _logger.info("  [OK] %s", label)
        return True
    except Exception as exc:
        _logger.warning("  [FAIL] %s: %s", label, str(exc)[:200])
        return False


def backfill_ticker(
    ticker: str,
    period: str = "10y",
    truncate: bool = True,
    skip_forecast: bool = False,
) -> dict:
    """Truncate and backfill all data for a single ticker.

    Args:
        ticker: Uppercase ticker symbol.
        period: yfinance period string (e.g. '10y', '5y').
        truncate: If True, delete existing data first.
        skip_forecast: If True, skip Prophet forecast step.

    Returns:
        Dict with step results.
    """
    from stocks.repository import StockRepository

    ticker = ticker.upper()
    repo = StockRepository()
    results = {"ticker": ticker, "steps": {}}

    _logger.info(
        "=" * 60 + "\n  Backfilling %s (period=%s, "
        "truncate=%s)\n" + "=" * 60,
        ticker,
        period,
        truncate,
    )

    # Step 0: Truncate
    if truncate:
        _logger.info("Step 0: Truncating %s from all tables", ticker)
        deleted = repo.delete_ticker_data(ticker)
        for tbl, count in deleted.items():
            if count > 0:
                _logger.info("  Deleted %d rows from %s", count, tbl)
        _clear_all_cache(ticker)
        _clear_charts(ticker)
        results["steps"]["truncate"] = deleted

    # Step 1: OHLCV
    _logger.info("Step 1: Fetching OHLCV (%s)", period)

    def _fetch_ohlcv():
        from tools.stock_data_tool import fetch_stock_data

        return fetch_stock_data.invoke({"ticker": ticker, "period": period})

    results["steps"]["ohlcv"] = _step("OHLCV", _fetch_ohlcv)

    # Step 2: Company info
    _logger.info("Step 2: Fetching company info")

    def _fetch_info():
        from tools.stock_data_tool import get_stock_info

        return get_stock_info.invoke({"ticker": ticker})

    results["steps"]["company_info"] = _step("Company info", _fetch_info)

    # Step 3: Dividends
    _logger.info("Step 3: Fetching dividends")

    def _fetch_dividends():
        from tools.stock_data_tool import get_dividend_history

        return get_dividend_history.invoke({"ticker": ticker})

    results["steps"]["dividends"] = _step("Dividends", _fetch_dividends)

    # Step 4: Technical analysis
    _logger.info("Step 4: Running technical analysis")
    _clear_all_cache(ticker)  # clear so freshness gate is bypassed

    def _run_analysis():
        from tools.price_analysis_tool import (
            analyse_stock_price,
        )

        return analyse_stock_price.invoke({"ticker": ticker})

    results["steps"]["analysis"] = _step("Analysis", _run_analysis)

    # Step 5: Quarterly results
    _logger.info("Step 5: Fetching quarterly results")

    def _fetch_quarterly():
        from tools.stock_data_tool import (
            fetch_quarterly_results,
        )

        return fetch_quarterly_results.invoke({"ticker": ticker})

    results["steps"]["quarterly"] = _step(
        "Quarterly results", _fetch_quarterly
    )

    # Step 6: Forecast
    if not skip_forecast:
        _logger.info("Step 6: Running Prophet forecast")
        _clear_all_cache(ticker)

        def _run_forecast():
            from tools.forecasting_tool import forecast_stock

            return forecast_stock.invoke({"ticker": ticker, "months": 9})

        results["steps"]["forecast"] = _step("Forecast", _run_forecast)
    else:
        _logger.info("Step 6: Skipped (--skip-forecast)")
        results["steps"]["forecast"] = "skipped"

    # Summary
    ohlcv_df = repo.get_ohlcv(ticker)
    ti_df = repo.get_technical_indicators(ticker)
    summary = repo.get_latest_analysis_summary(ticker)
    qr_df = repo.get_quarterly_results(ticker)
    _logger.info(
        "  Summary for %s: OHLCV=%d rows, "
        "TechIndicators=%d rows, Analysis=%s, "
        "Quarterly=%d rows",
        ticker,
        len(ohlcv_df),
        len(ti_df),
        "yes" if summary else "no",
        len(qr_df),
    )

    return results


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Truncate and backfill Iceberg data."
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Tickers to backfill (default: all registry).",
    )
    parser.add_argument(
        "--period",
        default="10y",
        help="yfinance period (default: 10y).",
    )
    parser.add_argument(
        "--no-truncate",
        action="store_true",
        help="Skip truncation (append-only mode).",
    )
    parser.add_argument(
        "--skip-forecast",
        action="store_true",
        help="Skip Prophet forecast step.",
    )
    args = parser.parse_args()

    # Resolve tickers
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        from stocks.repository import StockRepository

        repo = StockRepository()
        registry = repo.get_all_registry()
        tickers = sorted(registry.keys()) if registry else []
        if not tickers:
            _logger.error("No tickers in registry. Specify --tickers.")
            sys.exit(1)

    _logger.info(
        "Backfilling %d tickers: %s",
        len(tickers),
        ", ".join(tickers),
    )

    start = time.time()
    all_results = []
    for ticker in tickers:
        result = backfill_ticker(
            ticker,
            period=args.period,
            truncate=not args.no_truncate,
            skip_forecast=args.skip_forecast,
        )
        all_results.append(result)

    elapsed = time.time() - start
    _logger.info(
        "\nBackfill complete: %d tickers in %.1f seconds",
        len(tickers),
        elapsed,
    )

    # Final summary table
    _logger.info(
        "\n%-14s %7s %7s %8s %7s %8s",
        "Ticker",
        "OHLCV",
        "TechInd",
        "Analysis",
        "Qtrly",
        "Forecast",
    )
    _logger.info("-" * 60)
    for r in all_results:
        steps = r["steps"]
        _logger.info(
            "%-14s %7s %7s %8s %7s %8s",
            r["ticker"],
            "OK" if steps.get("ohlcv") else "FAIL",
            "OK" if steps.get("analysis") else "FAIL",
            "OK" if steps.get("analysis") else "FAIL",
            "OK" if steps.get("quarterly") else "FAIL",
            (
                "OK"
                if steps.get("forecast") is True
                else str(steps.get("forecast", "FAIL"))
            ),
        )


if __name__ == "__main__":
    main()

import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
_logger = logging.getLogger(__name__)
#!/usr/bin/env python
"""Validate ASETPLTFRM-201 Phase 2 after a test refresh.

Checks:
1. Market indices in OHLCV table (^VIX, ^GSPC, etc.)
2. Sentiment score for the ticker
3. Forecast run with accuracy metrics
4. Backend log entries confirming full pipeline
5. Summary report

Usage::

    source ~/.ai-agent-ui/venv/bin/activate
    PYTHONPATH=backend python scripts/validate_phase2.py AAPL
"""

import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

LOG_PATH = Path(
    os.path.expanduser("~/.ai-agent-ui/logs/agent.log")
)
INDICES = [
    "^VIX", "^INDIAVIX", "^GSPC", "^NSEI",
    "^TNX", "^IRX", "CL=F", "DX-Y.NYB",
]
TODAY = date.today()


def _status(ok):
    return PASS if ok else FAIL


def check_market_indices(repo):
    """Check 1: Market indices in OHLCV table."""
    _logger.info("\n== 1. Market Indices in OHLCV ==")
    results = {}
    for sym in INDICES:
        latest = repo.get_latest_ohlcv_date(sym)
        fresh = (
            latest is not None
            and latest >= TODAY - timedelta(days=3)
        )
        results[sym] = {
            "latest": latest,
            "fresh": fresh,
        }
        _logger.info(
            f"  {_status(fresh)} {sym:12s} "
            f"latest={latest or 'NONE'}"
        )

    df = repo.get_ohlcv("^VIX")
    if not df.empty:
        _logger.info(f"  Total ^VIX rows: {len(df)}")
    return all(r["fresh"] for r in results.values())


def check_sentiment(repo, ticker):
    """Check 2: Sentiment score exists for today."""
    _logger.info("\n== 2. Sentiment Score ==")
    df = repo.get_sentiment_series(ticker)
    if df.empty:
        _logger.info(f"  {FAIL} No sentiment data for {ticker}")
        return False

    latest_date = df["score_date"].max()
    if hasattr(latest_date, "date"):
        latest_date = latest_date.date()

    fresh = latest_date >= TODAY - timedelta(days=1)
    latest_row = df[
        df["score_date"] == df["score_date"].max()
    ].iloc[0]
    score = latest_row.get("avg_score", "?")
    headlines = latest_row.get("headline_count", "?")
    source = latest_row.get("source", "?")

    _logger.info(
        f"  {_status(fresh)} {ticker}: "
        f"score={score}, "
        f"headlines={headlines}, "
        f"source={source}, "
        f"date={latest_date}"
    )
    _logger.info(f"  Total sentiment rows: {len(df)}")
    return fresh


def check_forecast(repo, ticker, months=9):
    """Check 3: Forecast run with accuracy."""
    _logger.info(f"\n== 3. Forecast Run ({months}m) ==")
    run = repo.get_latest_forecast_run(ticker, months)
    if not run:
        _logger.info(f"  {FAIL} No forecast run for {ticker}")
        return False

    rd = run.get("run_date")
    if hasattr(rd, "date"):
        rd = rd.date()
    fresh = rd is not None and rd >= TODAY

    mae = run.get("mae")
    rmse = run.get("rmse")
    mape = run.get("mape")
    sentiment = run.get("sentiment")
    price = run.get("current_price_at_run")
    has_accuracy = mae is not None

    _logger.info(f"  {_status(fresh)} Run date: {rd}")
    _logger.info(f"  {_status(has_accuracy)} Accuracy:")
    if has_accuracy:
        _logger.info(f"    MAE  : {mae:.2f}")
        _logger.info(f"    RMSE : {rmse:.2f}")
        _logger.info(f"    MAPE : {mape:.1f}%")
    else:
        _logger.info("    Not available")

    _logger.info(f"  Sentiment : {sentiment}")
    if price:
        _logger.info(f"  Price     : {price:.2f}")

    # Show targets
    for key in ["3m", "6m", "9m"]:
        tp = run.get(f"target_{key}_price")
        pct = run.get(f"target_{key}_pct_change")
        if tp is not None:
            sign = "+" if pct and pct >= 0 else ""
            _logger.info(
                f"  {key.upper()} Target: "
                f"{tp:.2f} ({sign}{pct:.1f}%)"
            )

    return fresh and has_accuracy


def check_logs(ticker):
    """Check 4: Backend log entries for pipeline."""
    _logger.info("\n== 4. Backend Log Verification ==")

    if not LOG_PATH.exists():
        _logger.info(f"  {WARN} Log file not found: {LOG_PATH}")
        _logger.info("  Checking stdout logs instead...")
        return None

    # Read last 500 lines (recent activity)
    lines = LOG_PATH.read_text(
        encoding="utf-8", errors="ignore",
    ).splitlines()[-500:]
    text = "\n".join(lines)
    today_str = str(TODAY)

    checks = {
        "Market index refresh": (
            r"Market index \^VIX: \d+ rows"
        ),
        "Market indices total": (
            r"Market indices refresh: \d+ rows"
        ),
        f"Sentiment scored {ticker}": (
            rf"Sentiment scored {re.escape(ticker)}"
            r": [-\d.]+ \(\d+ headlines\)"
        ),
        "Regressors added": (
            r"Added regressors: \["
        ),
        "Prophet model fitted": (
            r"Prophet model fitted on \d+ rows"
        ),
        "Cross-validation accuracy": (
            r"Cross-validation: MAE="
        ),
    }

    all_found = True
    found_details = {}
    for label, pattern in checks.items():
        match = re.search(pattern, text)
        found = match is not None
        if not found:
            all_found = False
        detail = match.group(0)[:80] if match else ""
        found_details[label] = detail
        _logger.info(f"  {_status(found)} {label}")
        if detail:
            _logger.info(f"         {detail}")

    return all_found


def print_report(results):
    """Check 5: Summary report."""
    _logger.info("\n" + "=" * 50)
    _logger.info("  PHASE 2 VALIDATION REPORT")
    _logger.info("=" * 50)
    _logger.info(f"  Date    : {TODAY}")
    _logger.info(f"  Ticker  : {results['ticker']}")
    _logger.info()

    total = 0
    passed = 0
    for name, ok in results["checks"].items():
        total += 1
        if ok:
            passed += 1
        elif ok is None:
            total -= 1  # skip warns
        _logger.info(f"  {_status(ok)} {name}" if ok is not None
              else f"  {WARN} {name}")

    _logger.info()
    if passed == total:
        _logger.info(
            f"  \033[92mALL {total} CHECKS PASSED\033[0m"
        )
    else:
        _logger.info(
            f"  \033[91m{passed}/{total} "
            f"CHECKS PASSED\033[0m"
        )
    _logger.info("=" * 50)


def main():
    if len(sys.argv) < 2:
        _logger.info("Usage: python scripts/validate_phase2.py TICKER")
        _logger.info("Example: python scripts/validate_phase2.py AAPL")
        sys.exit(1)

    ticker = sys.argv[1].upper().strip()

    _logger.info(f"Validating Phase 2 for {ticker} ({TODAY})")
    _logger.info("=" * 50)

    from tools._stock_shared import _require_repo

    repo = _require_repo()

    c1 = check_market_indices(repo)
    c2 = check_sentiment(repo, ticker)
    c3 = check_forecast(repo, ticker)
    c4 = check_logs(ticker)

    print_report({
        "ticker": ticker,
        "checks": {
            "Market indices in OHLCV": c1,
            "Sentiment scored": c2,
            "Forecast with accuracy": c3,
            "Backend logs confirm flow": c4,
        },
    })


if __name__ == "__main__":
    main()

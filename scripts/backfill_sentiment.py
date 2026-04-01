#!/usr/bin/env python
"""Backfill sentiment_scores + market indices into Iceberg.

Sentiment uses a price-derived proxy (daily return → score)
since historical news headlines aren't available for free.
Going forward, real LLM-scored sentiment replaces it daily.

Market indices (^VIX, ^INDIAVIX, ^GSPC, ^NSEI) are stored
in the standard OHLCV table via ``insert_ohlcv()``.

Usage::

    source ~/.ai-agent-ui/venv/bin/activate
    PYTHONPATH=backend python scripts/backfill_sentiment.py
"""

import logging
import sys
import time
from datetime import datetime

import pandas as pd
import pyarrow as pa
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
_logger = logging.getLogger(__name__)


def _return_to_sentiment(ret: float) -> float:
    """Map daily return % to sentiment score."""
    if ret > 2.0:
        return 0.6
    if ret > 0.5:
        return 0.3
    if ret > -0.5:
        return 0.0
    if ret > -2.0:
        return -0.3
    return -0.6


def backfill_sentiment_for_ticker(
    ticker: str,
    repo,
) -> int:
    """Backfill sentiment using price-return proxy."""
    df = repo.get_ohlcv(ticker)
    if df.empty:
        _logger.warning("No OHLCV for %s", ticker)
        return 0

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    df["return_pct"] = df["close"].pct_change() * 100
    df["raw_sent"] = df["return_pct"].apply(
        _return_to_sentiment,
    )
    df["sentiment"] = df["raw_sent"].rolling(7, min_periods=1).mean()
    df = df.dropna(subset=["sentiment"])

    # Check existing
    existing = repo.get_sentiment_series(ticker)
    existing_dates = set()
    if not existing.empty:
        existing_dates = set(pd.to_datetime(existing["score_date"]).dt.date)

    rows_ticker = []
    rows_date = []
    rows_score = []
    now = datetime.utcnow().replace(microsecond=0)

    for _, row in df.iterrows():
        d = row["date"].date()
        if d in existing_dates:
            continue
        rows_ticker.append(ticker)
        rows_date.append(d)
        rows_score.append(round(float(row["sentiment"]), 3))

    if not rows_ticker:
        _logger.info("%s: already backfilled", ticker)
        return 0

    batch = pa.table(
        {
            "ticker": pa.array(rows_ticker, pa.string()),
            "score_date": pa.array(rows_date, pa.date32()),
            "avg_score": pa.array(rows_score, pa.float64()),
            "headline_count": pa.array(
                [0] * len(rows_ticker),
                pa.int32(),
            ),
            "source": pa.array(
                ["price_proxy"] * len(rows_ticker),
                pa.string(),
            ),
            "scored_at": pa.array(
                [now] * len(rows_ticker),
                pa.timestamp("us"),
            ),
        }
    )
    repo._append_rows("stocks.sentiment_scores", batch)
    _logger.info(
        "%s: %d sentiment rows inserted",
        ticker,
        len(rows_ticker),
    )
    return len(rows_ticker)


def backfill_market_indices(repo) -> int:
    """Backfill missing market indices into the OHLCV table.

    Uses the standard ``insert_ohlcv()`` path with built-in
    dedup on ``(ticker, date)``.
    """
    from datetime import timedelta

    indices = [
        # Market indices
        "^VIX",
        "^INDIAVIX",
        "^GSPC",
        "^NSEI",
        # Macro indicators
        "^TNX",
        "^IRX",
        "CL=F",
        "DX-Y.NYB",
    ]
    total = 0

    for idx_sym in indices:
        try:
            last = repo.get_latest_ohlcv_date(idx_sym)
            if last is not None:
                start = str(last + timedelta(days=1))
            else:
                start = "2015-01-01"

            hist = yf.Ticker(idx_sym).history(
                start=start,
                auto_adjust=False,
            )
            if hist.empty:
                _logger.info(
                    "%s: up to date",
                    idx_sym,
                )
                continue

            hist.index = pd.to_datetime(
                hist.index,
            ).tz_localize(None)
            n = repo.insert_ohlcv(idx_sym, hist)
            total += n
            _logger.info(
                "%s: %d rows inserted",
                idx_sym,
                n,
            )
            # Pause between indices to avoid commit
            # conflict.
            time.sleep(2)
        except Exception as exc:
            _logger.error(
                "%s failed: %s",
                idx_sym,
                exc,
            )

    return total


def main() -> None:
    from tools._stock_shared import _require_repo

    repo = _require_repo()

    # 1. Market indices
    _logger.info("=== Backfilling market indices ===")
    mi = backfill_market_indices(repo)
    _logger.info("Market indices: %d rows", mi)

    # 2. Sentiment for all registered tickers
    _logger.info("=== Backfilling sentiment ===")
    registry = repo.get_all_registry()
    tickers = list(registry.keys()) if registry else []
    _logger.info("%d tickers to backfill", len(tickers))

    total = 0
    for i, ticker in enumerate(tickers, 1):
        try:
            n = backfill_sentiment_for_ticker(ticker, repo)
            total += n
            if n > 0:
                _logger.info(
                    "[%d/%d] %s: %d rows",
                    i,
                    len(tickers),
                    ticker,
                    n,
                )
                # Pause to avoid commit conflicts.
                time.sleep(1)
        except Exception as exc:
            _logger.error(
                "[%d/%d] %s failed: %s",
                i,
                len(tickers),
                ticker,
                exc,
            )

    _logger.info(
        "Backfill complete: %d sentiment rows, " "%d index rows",
        total,
        mi,
    )


if __name__ == "__main__":
    sys.exit(main() or 0)

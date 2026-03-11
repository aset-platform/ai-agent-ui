"""One-time backfill: populate ``adj_close`` in Iceberg from parquet files.

Reads ``data/raw/{TICKER}_raw.parquet`` files and uses the ``Adj Close``
column to update the ``stocks.ohlcv`` Iceberg table.  For tickers whose
parquet file lacks good Adj Close coverage (< 50 %), or for any remaining
NaN ``adj_close`` rows after the parquet merge, the ``close`` price is
used as fallback (yfinance >= 1.2 auto-adjusts Close by default).

Idempotent -- safe to run multiple times.  Rows that already have a
non-null ``adj_close`` are overwritten with the parquet value if
available.

Usage::

    python stocks/backfill_adj_close.py
"""

import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent

# Ensure backend/ on sys.path for paths module
_backend_dir = str(_PROJECT_ROOT / "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from paths import RAW_DIR  # noqa: E402

_DATA_RAW = RAW_DIR


def _backfill_from_parquet() -> int:
    """Backfill ``adj_close`` from parquet ``Adj Close`` columns.

    Skips tickers whose parquet file has < 50 % Adj Close coverage.

    Returns:
        Number of tickers updated from parquet data.
    """
    from stocks.repository import StockRepository

    repo = StockRepository()
    registry = repo.get_all_registry()
    if not registry:
        _logger.info("No tickers in registry -- nothing to backfill.")
        return 0

    updated_tickers = 0

    for ticker in sorted(registry.keys()):
        parquet_path = _DATA_RAW / f"{ticker}_raw.parquet"
        if not parquet_path.exists():
            _logger.info(
                "%s: no parquet file at %s -- skipping parquet merge.",
                ticker,
                parquet_path,
            )
            continue

        try:
            pq_df = pd.read_parquet(parquet_path, engine="pyarrow")
        except Exception as exc:
            _logger.warning("%s: failed to read parquet: %s", ticker, exc)
            continue

        if "Adj Close" not in pq_df.columns:
            _logger.info(
                "%s: no 'Adj Close' column in parquet -- skipping.",
                ticker,
            )
            continue

        adj_series = pq_df["Adj Close"].dropna()
        coverage = len(adj_series) / max(len(pq_df), 1)
        if coverage < 0.5:
            _logger.info(
                "%s: parquet Adj Close coverage %.1f%% (< 50%%) "
                "-- skipping parquet merge.",
                ticker,
                coverage * 100,
            )
            continue

        # Build date -> adj_close map from parquet
        pq_df.index = pd.to_datetime(pq_df.index).tz_localize(None)
        adj_map: dict[date, float] = {}
        for ts, val in pq_df["Adj Close"].items():
            if pd.notna(val):
                d = ts.date() if hasattr(ts, "date") else ts
                adj_map[d] = float(val)

        rows_updated = repo.update_ohlcv_adj_close(ticker, adj_map)
        if rows_updated > 0:
            updated_tickers += 1
        _logger.info(
            "%s: merged %d adj_close from parquet " "(%d rows updated).",
            ticker,
            len(adj_map),
            rows_updated,
        )

    return updated_tickers


def _fill_remaining_nulls() -> int:
    """Fill any remaining null ``adj_close`` with ``close`` for all tickers.

    Returns:
        Number of tickers that had NaN values filled.
    """
    from stocks.repository import StockRepository

    repo = StockRepository()
    registry = repo.get_all_registry()
    if not registry:
        return 0

    filled_tickers = 0

    for ticker in sorted(registry.keys()):
        ice_df = repo.get_ohlcv(ticker)
        if ice_df.empty:
            continue

        nan_mask = ice_df["adj_close"].isna()
        if not nan_mask.any():
            _logger.debug("%s: no NaN adj_close -- nothing to fill.", ticker)
            continue

        nan_rows = ice_df[nan_mask]
        fill_map: dict[date, float] = {}
        for _, row in nan_rows.iterrows():
            d = row["date"]
            if hasattr(d, "date"):
                d = d.date()
            close_val = row.get("close")
            if pd.notna(close_val):
                fill_map[d] = float(close_val)

        if not fill_map:
            continue

        rows_updated = repo.update_ohlcv_adj_close(ticker, fill_map)
        if rows_updated > 0:
            filled_tickers += 1
        _logger.info(
            "%s: filled %d NaN adj_close with close price.",
            ticker,
            rows_updated,
        )

    return filled_tickers


def main() -> None:
    """Run the full adj_close backfill."""
    _logger.info("=== adj_close backfill starting ===")
    _logger.info("Parquet source: %s", _DATA_RAW)

    parquet_count = _backfill_from_parquet()
    _logger.info(
        "Phase 1 complete: %d tickers updated from parquet.",
        parquet_count,
    )

    fill_count = _fill_remaining_nulls()
    _logger.info(
        "Phase 2 complete: %d tickers had NaN adj_close filled with close.",
        fill_count,
    )

    _logger.info("=== adj_close backfill finished ===")

    # Verification pass
    from stocks.repository import StockRepository

    repo = StockRepository()
    registry = repo.get_all_registry()
    _logger.info("--- Verification ---")
    for ticker in sorted(registry.keys()):
        ice_df = repo.get_ohlcv(ticker)
        if ice_df.empty:
            _logger.info("%s: no OHLCV data", ticker)
            continue
        total = len(ice_df)
        has_adj = ice_df["adj_close"].notna().sum()
        pct = has_adj / total * 100
        status = "OK" if pct >= 99.9 else "PARTIAL"
        _logger.info(
            "%s: %s  %d/%d rows have adj_close (%.1f%%)",
            ticker,
            status,
            has_adj,
            total,
            pct,
        )


if __name__ == "__main__":
    main()

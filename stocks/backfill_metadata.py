"""One-time migration: flat files to Iceberg.

Reads any remaining ``stock_registry.json``, ``{TICKER}_info.json``, and
raw OHLCV parquet files and inserts them into the corresponding Iceberg
tables if they are not already present.  Idempotent — safe to run
multiple times.

Usage::

    python stocks/backfill_metadata.py
"""

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
_logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"
_DATA_RAW = _PROJECT_ROOT / "data" / "raw"

# Ensure project root on sys.path
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _backfill_registry() -> int:
    """Backfill ``stocks.registry`` from ``stock_registry.json``.

    Returns:
        Number of tickers inserted.
    """
    from stocks.repository import StockRepository

    registry_path = _DATA_METADATA / "stock_registry.json"
    if not registry_path.exists():
        _logger.info("No stock_registry.json found — nothing to backfill.")
        return 0

    with open(registry_path) as fh:
        registry = json.load(fh)

    repo = StockRepository()
    existing = repo.get_all_registry()
    inserted = 0

    for ticker, entry in registry.items():
        if ticker in existing:
            _logger.debug(
                "Registry: %s already in Iceberg — skipping.", ticker
            )
            continue
        try:
            dr = entry.get("date_range", {})
            start_str = dr.get("start")
            end_str = dr.get("end")
            repo.upsert_registry(
                ticker=ticker,
                last_fetch_date=date.fromisoformat(
                    entry.get("last_fetch_date", str(date.today()))
                ),
                total_rows=int(entry.get("total_rows", 0)),
                date_range_start=(
                    date.fromisoformat(start_str)
                    if start_str
                    else date.today()
                ),
                date_range_end=(
                    date.fromisoformat(end_str) if end_str else date.today()
                ),
                market=(
                    "india"
                    if ticker.upper().endswith((".NS", ".BO"))
                    else "us"
                ),
            )
            inserted += 1
            _logger.info("Registry: inserted %s", ticker)
        except Exception as exc:
            _logger.warning("Registry: failed to insert %s: %s", ticker, exc)

    return inserted


def _backfill_company_info() -> int:
    """Backfill ``stocks.company_info`` from ``{TICKER}_info.json`` files.

    Returns:
        Number of tickers inserted.
    """
    from stocks.repository import StockRepository

    info_files = sorted(_DATA_METADATA.glob("*_info.json"))
    if not info_files:
        _logger.info("No *_info.json files found — nothing to backfill.")
        return 0

    repo = StockRepository()
    inserted = 0

    for info_path in info_files:
        ticker = info_path.stem.replace("_info", "").upper()
        existing = repo.get_latest_company_info(ticker)
        if existing is not None:
            _logger.debug(
                "Company info: %s already in Iceberg — skipping.", ticker
            )
            continue
        try:
            with open(info_path) as fh:
                info = json.load(fh)
            # Map flat JSON keys to yfinance-style keys for insert_company_info()
            mapped = {
                "company_name": info.get("company_name")
                or info.get("name", ""),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "marketCap": info.get("market_cap"),
                "trailingPE": info.get("pe_ratio"),
                "fiftyTwoWeekHigh": info.get("52w_high"),
                "fiftyTwoWeekLow": info.get("52w_low"),
                "currentPrice": info.get("current_price"),
                "currency": info.get("currency", "USD"),
            }
            repo.insert_company_info(ticker, mapped)
            inserted += 1
            _logger.info("Company info: inserted %s", ticker)
        except Exception as exc:
            _logger.warning(
                "Company info: failed to insert %s: %s", ticker, exc
            )

    return inserted


def _backfill_ohlcv() -> int:
    """Backfill ``stocks.ohlcv`` from raw parquet files in ``data/raw/``.

    For each ``{TICKER}_raw.parquet`` file, checks whether the Iceberg
    ``stocks.ohlcv`` table already has rows for that ticker.  If not,
    inserts all rows from the parquet file.

    Returns:
        Number of tickers backfilled.
    """
    from stocks.repository import StockRepository

    parquet_files = sorted(_DATA_RAW.glob("*_raw.parquet"))
    if not parquet_files:
        _logger.info("No raw parquet files found — nothing to backfill.")
        return 0

    repo = StockRepository()
    backfilled = 0

    for parquet_path in parquet_files:
        ticker = parquet_path.stem.replace("_raw", "").upper()
        existing = repo.get_ohlcv(ticker)
        if not existing.empty:
            _logger.debug(
                "OHLCV: %s already has %d rows — skipping.",
                ticker,
                len(existing),
            )
            continue
        try:
            df = pd.read_parquet(parquet_path, engine="pyarrow")
            df.index = pd.to_datetime(df.index).tz_localize(None)
            inserted = repo.insert_ohlcv(ticker, df)
            backfilled += 1
            _logger.info("OHLCV: inserted %d rows for %s", inserted, ticker)
        except Exception as exc:
            _logger.warning("OHLCV: failed to insert %s: %s", ticker, exc)

    return backfilled


def main() -> None:
    """Run the full backfill."""
    _logger.info("Starting backfill from %s and %s", _DATA_METADATA, _DATA_RAW)

    reg_count = _backfill_registry()
    info_count = _backfill_company_info()
    ohlcv_count = _backfill_ohlcv()

    _logger.info(
        "Backfill complete: %d registry, %d company info, %d OHLCV tickers inserted.",
        reg_count,
        info_count,
        ohlcv_count,
    )


if __name__ == "__main__":
    main()

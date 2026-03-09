"""Export Iceberg data for seed tickers to JSON fixtures.

Reads live Iceberg tables for 5 demo tickers and writes
compact JSON files into ``fixtures/seed/``.  OHLCV and
technical indicators are trimmed to the most recent year
(~252 trading days) to keep fixtures repo-friendly.

Usage::

    python scripts/export_seed_data.py
"""

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = str(_PROJECT_ROOT / "backend")
_ROOT_DIR = str(_PROJECT_ROOT)
for p in (_BACKEND_DIR, _ROOT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from stocks.repository import StockRepository  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
_logger = logging.getLogger(__name__)

SEED_TICKERS = ["AAPL", "MSFT", "RELIANCE.NS", "TCS.NS", "TSLA"]
OHLCV_TAIL_DAYS = 252  # ~1 trading year
FIXTURES_DIR = _PROJECT_ROOT / "fixtures" / "seed"


def _serialise(obj):
    """JSON serialiser for date/datetime/Timestamp."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Not serialisable: {type(obj)}")


def _df_to_records(df):
    """Convert DataFrame to list of dicts with clean types."""
    records = []
    for _, row in df.iterrows():
        d = {}
        for col in df.columns:
            val = row[col]
            if hasattr(val, "isoformat"):
                d[col] = val.isoformat()
            elif hasattr(val, "item"):
                d[col] = val.item()
            else:
                d[col] = val
            # Convert NaN to None
            if isinstance(d[col], float) and d[col] != d[col]:
                d[col] = None
        records.append(d)
    return records


def _write_json(name, data):
    """Write data to a JSON fixture file."""
    path = FIXTURES_DIR / f"{name}.json"
    with open(path, "w") as f:
        json.dump(data, f, default=_serialise, indent=2)
    _logger.info("Wrote %s (%d entries)", path.name, len(data))


def main():
    """Export seed data for all tables."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    repo = StockRepository()
    registry = repo.get_all_registry()

    # ── Registry ────────────────────────────────────
    reg_data = []
    for t in SEED_TICKERS:
        entry = registry.get(t)
        if not entry:
            _logger.warning("Ticker %s not in registry", t)
            continue
        reg_data.append(
            {
                "ticker": t,
                "last_fetch_date": str(entry.get("last_fetch_date", "")),
                "total_rows": entry.get("total_rows", 0),
                "date_range_start": str(
                    entry.get("date_range", {}).get("start", "")
                ),
                "date_range_end": str(
                    entry.get("date_range", {}).get("end", "")
                ),
                "market": entry.get("market", "us"),
            }
        )
    _write_json("registry", reg_data)

    # ── Company Info ────────────────────────────────
    ci_data = []
    for t in SEED_TICKERS:
        ci = repo.get_latest_company_info(t)
        if ci:
            # Remove internal IDs; seed script generates new
            ci.pop("info_id", None)
            ci.pop("fetched_at", None)
            ci_data.append(ci)
    _write_json("company_info", ci_data)

    # ── OHLCV (trimmed to 1 year) ──────────────────
    ohlcv_data = []
    for t in SEED_TICKERS:
        df = repo.get_ohlcv(t)
        if df.empty:
            continue
        df = df.sort_values("date").tail(OHLCV_TAIL_DAYS)
        ohlcv_data.extend(_df_to_records(df))
    _write_json("ohlcv", ohlcv_data)

    # ── Dividends ───────────────────────────────────
    div_data = []
    for t in SEED_TICKERS:
        df = repo.get_dividends(t)
        if not df.empty:
            div_data.extend(_df_to_records(df))
    _write_json("dividends", div_data)

    # ── Technical Indicators (trimmed to 1 year) ───
    ti_data = []
    for t in SEED_TICKERS:
        df = repo.get_technical_indicators(t)
        if not df.empty:
            df = df.sort_values("date").tail(OHLCV_TAIL_DAYS)
            ti_data.extend(_df_to_records(df))
    _write_json("technical_indicators", ti_data)

    # ── Analysis Summary ────────────────────────────
    as_data = []
    for t in SEED_TICKERS:
        summary = repo.get_latest_analysis_summary(t)
        if summary:
            summary.pop("summary_id", None)
            summary.pop("computed_at", None)
            as_data.append(summary)
    _write_json("analysis_summary", as_data)

    # ── Forecast Runs ───────────────────────────────
    fr_data = []
    for t in SEED_TICKERS:
        run = repo.get_latest_forecast_run(t, 9)
        if run:
            run.pop("run_id", None)
            run.pop("computed_at", None)
            fr_data.append(run)
    _write_json("forecast_runs", fr_data)

    # ── Forecasts (series) ──────────────────────────
    fc_data = []
    for t in SEED_TICKERS:
        df = repo.get_latest_forecast_series(t, 9)
        if df is not None and not df.empty:
            fc_data.extend(_df_to_records(df))
    _write_json("forecasts", fc_data)

    # ── Quarterly Results ───────────────────────────
    qr_data = []
    for t in SEED_TICKERS:
        df = repo.get_quarterly_results(t)
        if not df.empty:
            qr_data.extend(_df_to_records(df))
    _write_json("quarterly_results", qr_data)

    _logger.info("Export complete for %d tickers", len(SEED_TICKERS))


if __name__ == "__main__":
    main()

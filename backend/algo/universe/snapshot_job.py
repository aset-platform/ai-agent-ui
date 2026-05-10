"""Monthly universe-snapshot rebuilder (REGIME-7).

Runs 1st Sunday 03:00 IST. Filters NSE active tickers by 60d ADTV
(>= ``ADTV_MIN_INR``) and market cap (>= ``MARKET_CAP_MIN_INR``);
top-200 by ADTV are flagged ``included_in_top_200=True``. Remaining
filtered tickers are persisted with ``included_in_top_200=False``
so follow-up filters can read them without re-running the job.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pyarrow as pa
from pyiceberg.expressions import EqualTo

from backend.algo.universe.iceberg_init import (
    UNIVERSE_SNAPSHOT_TABLE,
)
from backend.cache import get_cache
from backend.db.duckdb_engine import (
    invalidate_metadata,
    query_iceberg_table,
)

_logger = logging.getLogger(__name__)

MARKET_CAP_MIN_INR = 5_000_000_000      # 500 crore
ADTV_MIN_INR = 100_000_000              # 10 crore
TOP_N = 200
ADTV_LOOKBACK_DAYS = 90  # calendar; ~60 trading days


def _load_candidates(rebalance_date: date) -> list[dict]:
    """Compute 60d ADTV per .NS active ticker + join market cap.

    Single bulk DuckDB query against ``stocks.ohlcv``; then a single
    bulk join against ``stocks.piotroski_scores`` for market_cap +
    sector. Tickers without a piotroski row get ``market_cap=0`` →
    filtered out by the cap floor.
    """
    start = rebalance_date - timedelta(days=ADTV_LOOKBACK_DAYS)
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT ticker, AVG(close * volume) AS adtv_inr_60d "
        "FROM ohlcv "
        "WHERE date BETWEEN ? AND ? "
        "  AND ticker LIKE '%.NS' "
        "  AND ticker NOT LIKE '^%' "
        "GROUP BY ticker",
        [start, rebalance_date],
    )
    if not rows:
        return []
    placeholders = ",".join(["?"] * len(rows))
    sector_rows = query_iceberg_table(
        "stocks.piotroski_scores",
        f"SELECT ticker, MAX(market_cap) AS market_cap, "
        f"       MAX(sector) AS sector "
        f"FROM piotroski_scores "
        f"WHERE ticker IN ({placeholders}) "
        f"GROUP BY ticker",
        [r["ticker"] for r in rows],
    )
    by_t = {r["ticker"]: r for r in sector_rows}
    out: list[dict] = []
    for r in rows:
        meta = by_t.get(r["ticker"], {})
        mc = meta.get("market_cap") or 0
        out.append({
            "ticker": r["ticker"],
            "adtv_inr_60d": float(r["adtv_inr_60d"] or 0),
            "market_cap_inr": float(mc),
            "sector": meta.get("sector"),
        })
    return out


def _upsert_snapshot(
    rebalance_date: date, rows: list[dict],
) -> None:
    """NaN-replaceable upsert: scoped pre-delete on the rebalance
    key, then append the new rows in a single Iceberg commit."""
    if not rows:
        return
    from stocks.create_tables import _get_catalog
    catalog = _get_catalog()
    tbl = catalog.load_table(UNIVERSE_SNAPSHOT_TABLE)
    try:
        tbl.delete(EqualTo("rebalance_date", rebalance_date))
    except Exception as exc:  # noqa: BLE001
        _logger.debug(
            "snapshot pre-delete skipped: %s", exc,
        )

    schema = pa.schema([
        pa.field("rebalance_date", pa.date32(), nullable=False),
        pa.field("ticker", pa.string(), nullable=False),
        pa.field("adtv_inr_60d", pa.float64(), nullable=True),
        pa.field("market_cap_inr", pa.float64(), nullable=True),
        pa.field("sector", pa.string(), nullable=True),
        pa.field(
            "included_in_top_200", pa.bool_(), nullable=False,
        ),
    ])
    arrow_tbl = pa.table(
        {
            "rebalance_date": [rebalance_date] * len(rows),
            "ticker": [r["ticker"] for r in rows],
            "adtv_inr_60d": [r["adtv_inr_60d"] for r in rows],
            "market_cap_inr": [r["market_cap_inr"] for r in rows],
            "sector": [r.get("sector") for r in rows],
            "included_in_top_200": [
                bool(r.get("included_in_top_200", False))
                for r in rows
            ],
        },
        schema=schema,
    )
    tbl.append(arrow_tbl)
    invalidate_metadata(UNIVERSE_SNAPSHOT_TABLE)
    try:
        get_cache().invalidate("cache:universe:*")
    except Exception:  # noqa: BLE001
        # Cache is best-effort — never block writes on Redis.
        pass


def rebuild_universe_snapshot(rebalance_date: date) -> dict:
    """Orchestrate the rebuild. Returns a counts dict.

    1. Fetch candidates (60d ADTV + market cap join).
    2. Apply cap + ADTV floors.
    3. Sort by ADTV desc; top-N flagged ``included_in_top_200=True``;
       remainder persisted with ``False``.
    4. NaN-replaceable upsert by ``rebalance_date``.
    """
    candidates = _load_candidates(rebalance_date)
    if not candidates:
        _logger.warning(
            "No candidates for rebalance_date=%s — snapshot skipped",
            rebalance_date,
        )
        return {"included": 0, "excluded": 0}
    filtered = [
        c for c in candidates
        if c["market_cap_inr"] >= MARKET_CAP_MIN_INR
        and c["adtv_inr_60d"] >= ADTV_MIN_INR
    ]
    filtered.sort(key=lambda c: c["adtv_inr_60d"], reverse=True)
    included = filtered[:TOP_N]
    excluded = filtered[TOP_N:]
    rows = [
        {**c, "included_in_top_200": True} for c in included
    ] + [
        {**c, "included_in_top_200": False} for c in excluded
    ]
    _upsert_snapshot(rebalance_date, rows)
    _logger.info(
        "universe_snapshot: as_of=%s included=%d excluded=%d",
        rebalance_date,
        len(included),
        len(excluded),
    )
    return {
        "included": len(included),
        "excluded": len(excluded),
    }

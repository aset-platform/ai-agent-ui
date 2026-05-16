"""GET /v1/admin/universe-snapshot — read-only operator view of
``stocks.universe_snapshot`` (REGIME-7).

The table powers three production paths — LiveRuntime liquidity
buckets, SimBroker slippage caps, and the PIT universe resolver
for backtests — but had no admin UI. This module mirrors the
``daily_factor_coverage`` admin pattern: superuser-only, Redis
cached at ``TTL_STABLE``, Iceberg scan in a worker thread.

Three endpoints:

* ``GET /admin/universe-snapshot/rebalances`` — distinct
  rebalance_dates (for the date picker).
* ``GET /admin/universe-snapshot`` — per-ticker rows + summary
  aggregates for a single rebalance_date (defaults to latest).
  Returns the FULL snapshot (~700 rows) so the frontend can do
  search / sector / bucket / top-200 filtering + sorting +
  pagination client-side. One cache entry per rebalance_date.
* ``GET /admin/universe-snapshot/diff`` — entries / exits between
  two rebalance_dates (which tickers gained or lost top-200
  membership).

Auth: superuser-only — research / data-quality tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from pyiceberg.expressions import EqualTo

from auth.dependencies import superuser_only
from auth.models import UserContext
from backend.algo.universe.iceberg_init import UNIVERSE_SNAPSHOT_TABLE
from cache import TTL_STABLE, get_cache

_logger = logging.getLogger(__name__)


class UniverseSnapshotRow(BaseModel):
    ticker: str
    sector: str | None = None
    market_cap_inr: float | None = None
    adtv_inr_60d: float | None = None
    liquidity_bucket: str | None = None
    included_in_top_200: bool
    is_top100_mcap: bool | None = None


class SectorBreakdown(BaseModel):
    sector: str
    count: int
    top200_count: int


class BucketBreakdown(BaseModel):
    bucket: str
    count: int


class UniverseSnapshotResponse(BaseModel):
    rebalance_date: date
    total_rows: int
    top200_count: int
    avg_adtv_inr: float | None
    sectors: list[SectorBreakdown]
    buckets: list[BucketBreakdown]
    rows: list[UniverseSnapshotRow]
    computed_at: datetime


class RebalanceList(BaseModel):
    rebalances: list[date]
    computed_at: datetime


class DiffEntry(BaseModel):
    ticker: str
    sector: str | None = None
    adtv_inr_60d: float | None = None


class UniverseDiffResponse(BaseModel):
    from_date: date
    to_date: date
    entries: list[DiffEntry]
    exits: list[DiffEntry]
    computed_at: datetime


# ---------------------------------------------------------------
# Iceberg readers — sync, designed to run inside asyncio.to_thread
# ---------------------------------------------------------------

def _list_rebalances_sync() -> dict[str, Any]:
    from stocks.create_tables import _get_catalog

    try:
        cat = _get_catalog()
        tbl = cat.load_table(UNIVERSE_SNAPSHOT_TABLE)
        tbl = tbl.refresh()
        df = tbl.scan(
            selected_fields=("rebalance_date",),
        ).to_pandas()
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[universe-snapshot] rebalances scan failed: %s",
            exc,
            exc_info=True,
        )
        df = None

    now_iso = datetime.now(timezone.utc).isoformat()
    if df is None or df.empty:
        return {"rebalances": [], "computed_at": now_iso}

    dates = (
        df["rebalance_date"]
        .dropna()
        .drop_duplicates()
        .sort_values(ascending=False)
    )
    return {
        "rebalances": [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in dates],
        "computed_at": now_iso,
    }


def _load_snapshot_sync(
    *,
    rebalance_date: date | None,
) -> dict[str, Any]:
    """Read one rebalance_date snapshot from Iceberg.

    If ``rebalance_date`` is ``None``, the latest available
    snapshot is returned. Returns the FULL snapshot (~700 rows);
    search / sector / bucket / top-200 filtering happens
    client-side.
    """
    from stocks.create_tables import _get_catalog

    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        cat = _get_catalog()
        tbl = cat.load_table(UNIVERSE_SNAPSHOT_TABLE)
        tbl = tbl.refresh()

        if rebalance_date is None:
            # Scan only the date column first to find the latest
            # available rebalance — keeps the heavy scan scoped.
            dates_df = tbl.scan(
                selected_fields=("rebalance_date",),
            ).to_pandas()
            if dates_df.empty:
                return _empty_snapshot_payload(None, now_iso)
            latest = dates_df["rebalance_date"].dropna().max()
            if hasattr(latest, "date"):
                latest = latest.date()
            rebalance_date = latest  # type: ignore[assignment]

        df = tbl.scan(
            row_filter=EqualTo("rebalance_date", rebalance_date),
        ).to_pandas()
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[universe-snapshot] snapshot scan failed "
            "rebalance_date=%s: %s",
            rebalance_date,
            exc,
            exc_info=True,
        )
        return _empty_snapshot_payload(rebalance_date, now_iso)

    if df is None or df.empty:
        return _empty_snapshot_payload(rebalance_date, now_iso)

    df = df.sort_values(
        by=["included_in_top_200", "adtv_inr_60d"],
        ascending=[False, False],
    )

    total_rows = int(len(df))
    top200_count = int(df["included_in_top_200"].sum())
    avg_adtv = (
        float(df["adtv_inr_60d"].dropna().mean())
        if df["adtv_inr_60d"].notna().any()
        else None
    )

    # Sector breakdown (count + top-200 count per sector)
    sectors: list[dict[str, Any]] = []
    if "sector" in df.columns:
        grp = df.assign(
            _sector=df["sector"].fillna("Unknown"),
        ).groupby("_sector", sort=False)
        for sec_name, sub in grp:
            sectors.append(
                {
                    "sector": str(sec_name),
                    "count": int(len(sub)),
                    "top200_count": int(
                        sub["included_in_top_200"].sum(),
                    ),
                }
            )
        sectors.sort(key=lambda r: r["count"], reverse=True)

    # Bucket breakdown
    buckets: list[dict[str, Any]] = []
    if "liquidity_bucket" in df.columns:
        grp_b = df.assign(
            _bucket=df["liquidity_bucket"].fillna("unbucketed"),
        ).groupby("_bucket", sort=False)
        for buc_name, sub in grp_b:
            buckets.append(
                {"bucket": str(buc_name), "count": int(len(sub))}
            )
        buckets.sort(key=lambda r: r["count"], reverse=True)

    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        rows.append(
            {
                "ticker": str(r["ticker"]),
                "sector": (
                    str(r["sector"])
                    if "sector" in df.columns and r["sector"] is not None
                    and str(r["sector"]) != "nan"
                    else None
                ),
                "market_cap_inr": _safe_float(r.get("market_cap_inr")),
                "adtv_inr_60d": _safe_float(r.get("adtv_inr_60d")),
                "liquidity_bucket": (
                    str(r["liquidity_bucket"])
                    if "liquidity_bucket" in df.columns
                    and r["liquidity_bucket"] is not None
                    and str(r["liquidity_bucket"]) != "nan"
                    else None
                ),
                "included_in_top_200": bool(r["included_in_top_200"]),
                "is_top100_mcap": (
                    bool(r["is_top100_mcap"])
                    if "is_top100_mcap" in df.columns
                    and r["is_top100_mcap"] is not None
                    and str(r["is_top100_mcap"]) != "nan"
                    else None
                ),
            }
        )

    return {
        "rebalance_date": _date_iso(rebalance_date),
        "total_rows": total_rows,
        "top200_count": top200_count,
        "avg_adtv_inr": avg_adtv,
        "sectors": sectors,
        "buckets": buckets,
        "rows": rows,
        "computed_at": now_iso,
    }


def _diff_snapshots_sync(
    *,
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    """Compute entries / exits between two rebalance_dates.

    "Entry" = present in `to_date` top-200 but absent (or not in
    top-200) in `from_date`. "Exit" = mirror.
    """
    from stocks.create_tables import _get_catalog
    from pyiceberg.expressions import In

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        cat = _get_catalog()
        tbl = cat.load_table(UNIVERSE_SNAPSHOT_TABLE)
        tbl = tbl.refresh()
        df = tbl.scan(
            row_filter=In(
                "rebalance_date", [from_date, to_date],
            ),
        ).to_pandas()
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[universe-snapshot] diff scan failed "
            "from=%s to=%s: %s",
            from_date,
            to_date,
            exc,
            exc_info=True,
        )
        return {
            "from_date": _date_iso(from_date),
            "to_date": _date_iso(to_date),
            "entries": [],
            "exits": [],
            "computed_at": now_iso,
        }

    if df is None or df.empty:
        return {
            "from_date": _date_iso(from_date),
            "to_date": _date_iso(to_date),
            "entries": [],
            "exits": [],
            "computed_at": now_iso,
        }

    # Coerce rebalance_date column to plain date for comparison.
    rb_col = df["rebalance_date"].apply(
        lambda v: v.date() if hasattr(v, "date") else v,
    )
    from_set = set(
        df.loc[
            (rb_col == from_date) & (df["included_in_top_200"]),
            "ticker",
        ]
    )
    to_set = set(
        df.loc[
            (rb_col == to_date) & (df["included_in_top_200"]),
            "ticker",
        ]
    )

    entries_set = to_set - from_set
    exits_set = from_set - to_set

    # Enrich with sector + adtv from the to-side (entries) and
    # from-side (exits) so the operator sees what was added/removed.
    def _enrich(tickers: set[str], side: date) -> list[dict[str, Any]]:
        sub = df[
            (rb_col == side) & (df["ticker"].isin(tickers))
        ].sort_values(by="adtv_inr_60d", ascending=False)
        out: list[dict[str, Any]] = []
        for _, r in sub.iterrows():
            out.append(
                {
                    "ticker": str(r["ticker"]),
                    "sector": (
                        str(r["sector"])
                        if "sector" in df.columns
                        and r["sector"] is not None
                        and str(r["sector"]) != "nan"
                        else None
                    ),
                    "adtv_inr_60d": _safe_float(r.get("adtv_inr_60d")),
                }
            )
        return out

    return {
        "from_date": _date_iso(from_date),
        "to_date": _date_iso(to_date),
        "entries": _enrich(entries_set, to_date),
        "exits": _enrich(exits_set, from_date),
        "computed_at": now_iso,
    }


def _safe_float(v: Any) -> float | None:
    import math

    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _date_iso(d: date | None) -> str | None:
    if d is None:
        return None
    if hasattr(d, "date"):
        d = d.date()  # type: ignore[assignment]
    return d.isoformat()


def _empty_snapshot_payload(
    rebalance_date: date | None,
    now_iso: str,
) -> dict[str, Any]:
    return {
        "rebalance_date": _date_iso(rebalance_date) or now_iso[:10],
        "total_rows": 0,
        "top200_count": 0,
        "avg_adtv_inr": None,
        "sectors": [],
        "buckets": [],
        "rows": [],
        "computed_at": now_iso,
    }


# ---------------------------------------------------------------
# Cache keys
# ---------------------------------------------------------------

def _cache_key_snapshot(rebalance_date: date | None) -> str:
    rb = rebalance_date.isoformat() if rebalance_date else "latest"
    return f"cache:universe_snapshot:{rb}"


def _cache_key_rebalances() -> str:
    return "cache:universe_snapshot:rebalances"


def _cache_key_diff(from_date: date, to_date: date) -> str:
    return (
        "cache:universe_snapshot:diff:"
        f"{from_date.isoformat()}:{to_date.isoformat()}"
    )


# ---------------------------------------------------------------
# Router
# ---------------------------------------------------------------

def create_universe_snapshot_router() -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    @router.get(
        "/universe-snapshot/rebalances",
        response_model=RebalanceList,
    )
    async def list_rebalances(
        _user: UserContext = Depends(superuser_only),
    ) -> RebalanceList:
        """Distinct ``rebalance_date`` values, newest first.

        Powers the date picker in the admin UI.
        """
        cache = get_cache()
        key = _cache_key_rebalances()
        try:
            cached = cache.get(key)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[universe-snapshot] cache.get crashed key=%s: %s",
                key,
                exc,
                exc_info=True,
            )
            cached = None
        if cached is not None:
            try:
                payload = json.loads(cached)
                return RebalanceList(**payload)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "[universe-snapshot] cached rebalances "
                    "deserialize failed: %s",
                    exc,
                    exc_info=True,
                )

        payload = await asyncio.to_thread(_list_rebalances_sync)

        try:
            cache.set(key, json.dumps(payload), ttl=TTL_STABLE)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[universe-snapshot] cache.set crashed key=%s: %s",
                key,
                exc,
                exc_info=True,
            )

        return RebalanceList(**payload)

    @router.get(
        "/universe-snapshot",
        response_model=UniverseSnapshotResponse,
    )
    async def get_snapshot(
        rebalance_date: date | None = Query(default=None),
        _user: UserContext = Depends(superuser_only),
    ) -> UniverseSnapshotResponse:
        """Per-ticker rows + summary aggregates for one rebalance.

        ``rebalance_date`` defaults to the latest available snapshot.
        Returns the full snapshot (~700 rows); filtering /
        pagination is client-side.
        """
        cache = get_cache()
        key = _cache_key_snapshot(rebalance_date)
        try:
            cached = cache.get(key)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[universe-snapshot] cache.get crashed key=%s: %s",
                key,
                exc,
                exc_info=True,
            )
            cached = None
        if cached is not None:
            try:
                payload = json.loads(cached)
                return UniverseSnapshotResponse(**payload)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "[universe-snapshot] cached snapshot "
                    "deserialize failed: %s",
                    exc,
                    exc_info=True,
                )

        payload = await asyncio.to_thread(
            _load_snapshot_sync,
            rebalance_date=rebalance_date,
        )

        try:
            cache.set(key, json.dumps(payload), ttl=TTL_STABLE)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[universe-snapshot] cache.set crashed key=%s: %s",
                key,
                exc,
                exc_info=True,
            )

        return UniverseSnapshotResponse(**payload)

    @router.get(
        "/universe-snapshot/diff",
        response_model=UniverseDiffResponse,
    )
    async def get_diff(
        from_date: date = Query(..., alias="from"),
        to_date: date = Query(..., alias="to"),
        _user: UserContext = Depends(superuser_only),
    ) -> UniverseDiffResponse:
        """Entries / exits between two rebalance_dates (top-200 only)."""
        if from_date == to_date:
            raise HTTPException(
                status_code=400,
                detail="from and to must be distinct rebalance_dates",
            )
        cache = get_cache()
        key = _cache_key_diff(from_date, to_date)
        try:
            cached = cache.get(key)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[universe-snapshot] cache.get crashed key=%s: %s",
                key,
                exc,
                exc_info=True,
            )
            cached = None
        if cached is not None:
            try:
                payload = json.loads(cached)
                return UniverseDiffResponse(**payload)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "[universe-snapshot] cached diff deserialize "
                    "failed: %s",
                    exc,
                    exc_info=True,
                )

        payload = await asyncio.to_thread(
            _diff_snapshots_sync,
            from_date=from_date,
            to_date=to_date,
        )

        try:
            cache.set(key, json.dumps(payload), ttl=TTL_STABLE)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[universe-snapshot] cache.set crashed key=%s: %s",
                key,
                exc,
                exc_info=True,
            )

        return UniverseDiffResponse(**payload)

    return router

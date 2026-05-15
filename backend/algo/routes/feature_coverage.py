"""GET /v1/admin/feature-coverage — feature-coverage dashboard
(ASETPLTFRM-416 / FE-14).

Computes, for the requested ``(interval_sec, period_start,
period_end, feature_set_version)`` window, the share of
``(ticker, bar_open_ts_ns)`` bar slots in
``stocks.intraday_features`` for which each
``feature_name`` produced a non-null row.

Denominator: ``total_unique_bars`` = the count of distinct
``(ticker, bar_open_ts_ns)`` pairs seen across ANY feature in
the window. This is the universe of bar slots the centralized
feature engine had a chance to write to. Per-feature coverage is
``rows_for_feature / total_unique_bars × 100``.

The intraday writer (FE-3) drops rows where ``feature_value`` is
NaN / non-finite at the PyArrow boundary, so simply counting rows
per ``feature_name`` is equivalent to counting non-null
emissions. Coverage < 100 % therefore means the feature was not
emitted for that bar slot (e.g. warm-up not reached, source data
missing).

Cache: ``cache:feature_coverage:{interval_sec}:{start}:{end}:
{feature_set_version}`` at ``TTL_STABLE`` (300 s).

Auth: superuser-only — this is a research / data-quality tool.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from pyiceberg.expressions import (
    And,
    EqualTo,
    GreaterThanOrEqual,
    LessThanOrEqual,
)

from auth.dependencies import superuser_only
from auth.models import UserContext
from backend.algo.features.version import FEATURE_SET_VERSION
from cache import TTL_STABLE, get_cache

_logger = logging.getLogger(__name__)


class FeatureCoverageRow(BaseModel):
    feature_name: str
    coverage_pct: float
    rows: int
    tickers_seen: int


class FeatureCoverageResponse(BaseModel):
    interval_sec: int
    period_start: date
    period_end: date
    feature_set_version: str
    total_unique_bars: int
    tickers_total: int
    rows_total: int
    coverage: list[FeatureCoverageRow]
    computed_at: datetime


def _cache_key(
    interval_sec: int,
    period_start: date,
    period_end: date,
    feature_set_version: str,
) -> str:
    return (
        "cache:feature_coverage:"
        f"{interval_sec}:"
        f"{period_start.isoformat()}:"
        f"{period_end.isoformat()}:"
        f"{feature_set_version}"
    )


def _build_row_filter(
    *,
    interval_sec: int,
    period_start: date,
    period_end: date,
    feature_set_version: str,
):
    """Iceberg row_filter for the configured window.

    ``bar_date`` is a partitioning-friendly ISO string; pyiceberg
    will prune ``year_month`` partition files automatically when
    the bar_date predicate brackets a calendar range.
    """
    return And(
        And(
            GreaterThanOrEqual(
                "bar_date",
                period_start.isoformat(),
            ),
            LessThanOrEqual(
                "bar_date",
                period_end.isoformat(),
            ),
        ),
        And(
            EqualTo("interval_sec", int(interval_sec)),
            EqualTo(
                "feature_set_version",
                feature_set_version,
            ),
        ),
    )


def _compute_coverage_sync(
    *,
    interval_sec: int,
    period_start: date,
    period_end: date,
    feature_set_version: str,
) -> dict[str, Any]:
    """Iceberg scan + group-by, run from a thread.

    Returns a JSON-ready dict matching FeatureCoverageResponse —
    we serialize datetime as ISO string here so the cache layer
    can round-trip via ``json.dumps`` without custom encoders.
    """
    from stocks.create_tables import (
        _INTRADAY_FEATURES_TABLE,
        _get_catalog,
    )

    row_filter = _build_row_filter(
        interval_sec=interval_sec,
        period_start=period_start,
        period_end=period_end,
        feature_set_version=feature_set_version,
    )

    try:
        cat = _get_catalog()
        tbl = cat.load_table(_INTRADAY_FEATURES_TABLE)
        tbl = tbl.refresh()
        df = tbl.scan(
            row_filter=row_filter,
            selected_fields=(
                "ticker",
                "bar_open_ts_ns",
                "feature_name",
            ),
        ).to_pandas()
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[feature-coverage] Iceberg scan failed "
            "interval_sec=%d window=%s..%s version=%s: %s",
            interval_sec,
            period_start.isoformat(),
            period_end.isoformat(),
            feature_set_version,
            exc,
            exc_info=True,
        )
        df = None

    now_iso = datetime.now(timezone.utc).isoformat()
    if df is None or df.empty:
        return {
            "interval_sec": int(interval_sec),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "feature_set_version": feature_set_version,
            "total_unique_bars": 0,
            "tickers_total": 0,
            "rows_total": 0,
            "coverage": [],
            "computed_at": now_iso,
        }

    # Total unique (ticker, bar_open_ts_ns) pairs — the
    # denominator for per-feature coverage.
    unique_bars_df = df[["ticker", "bar_open_ts_ns"]].drop_duplicates()
    total_unique_bars = int(len(unique_bars_df))
    tickers_total = int(df["ticker"].drop_duplicates().shape[0])
    rows_total = int(len(df))

    # Per-feature rows + per-feature distinct ticker count.
    rows_by_feature = df.groupby("feature_name").size().to_dict()
    tickers_by_feature = (
        df.groupby("feature_name")["ticker"].nunique().to_dict()
    )

    coverage: list[dict[str, Any]] = []
    for feat_name, rows in rows_by_feature.items():
        rows_i = int(rows)
        if total_unique_bars > 0:
            pct = (rows_i / total_unique_bars) * 100.0
        else:
            pct = 0.0
        # Sanity cap — pct should never exceed 100 because a
        # feature can emit at most one row per (ticker, bar)
        # bar slot. Cap defensively so a UI assertion can rely
        # on the bound.
        if pct > 100.0:
            pct = 100.0
        coverage.append(
            {
                "feature_name": str(feat_name),
                "coverage_pct": round(pct, 4),
                "rows": rows_i,
                "tickers_seen": int(
                    tickers_by_feature.get(feat_name, 0),
                ),
            }
        )

    coverage.sort(
        key=lambda r: r["coverage_pct"],
        reverse=True,
    )

    return {
        "interval_sec": int(interval_sec),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "feature_set_version": feature_set_version,
        "total_unique_bars": total_unique_bars,
        "tickers_total": tickers_total,
        "rows_total": rows_total,
        "coverage": coverage,
        "computed_at": now_iso,
    }


def create_feature_coverage_router() -> APIRouter:
    router = APIRouter(
        prefix="/admin",
        tags=["admin"],
    )

    @router.get(
        "/feature-coverage",
        response_model=FeatureCoverageResponse,
    )
    async def get_feature_coverage(
        period_start: date = Query(...),
        period_end: date = Query(...),
        interval_sec: int = Query(900),
        feature_set_version: str | None = Query(None),
        _user: UserContext = Depends(superuser_only),
    ) -> FeatureCoverageResponse:
        """Coverage matrix for the intraday feature store.

        Window is inclusive on both ends. Default interval is
        15 minutes (the only cadence FE-3 ships today; 5m / 1m
        are wired for forward compatibility). If
        ``feature_set_version`` is omitted, the current pinned
        ``FEATURE_SET_VERSION`` is used.
        """
        if period_end < period_start:
            raise HTTPException(
                status_code=400,
                detail=("period_end must be >= period_start"),
            )
        version = (
            feature_set_version if feature_set_version else FEATURE_SET_VERSION
        )

        cache = get_cache()
        key = _cache_key(
            interval_sec=interval_sec,
            period_start=period_start,
            period_end=period_end,
            feature_set_version=version,
        )
        try:
            cached = cache.get(key)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[feature-coverage] cache.get crashed " "key=%s: %s",
                key,
                exc,
                exc_info=True,
            )
            cached = None
        if cached is not None:
            try:
                import json

                payload = json.loads(cached)
                return FeatureCoverageResponse(**payload)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "[feature-coverage] cache blob "
                    "deserialize failed key=%s: %s",
                    key,
                    exc,
                    exc_info=True,
                )

        payload = await asyncio.to_thread(
            _compute_coverage_sync,
            interval_sec=interval_sec,
            period_start=period_start,
            period_end=period_end,
            feature_set_version=version,
        )

        try:
            import json

            cache.set(
                key,
                json.dumps(payload),
                ttl=TTL_STABLE,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[feature-coverage] cache.set crashed " "key=%s: %s",
                key,
                exc,
                exc_info=True,
            )

        return FeatureCoverageResponse(**payload)

    return router

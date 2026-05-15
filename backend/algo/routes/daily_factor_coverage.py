"""GET /v1/admin/daily-factor-coverage — daily-factor coverage
dashboard (sibling of /admin/feature-coverage for the WIDE
``stocks.daily_factors`` table).

Computes, for the requested ``[period_start, period_end]`` window,
the share of ``(ticker, bar_date)`` rows in ``stocks.daily_factors``
where each factor column carries a non-null value.

The factor library (``backend/algo/factors/iceberg_init.py``) is a
WIDE Iceberg table — one row per ``(ticker, bar_date)``, one
column per factor (19 doubles + sector string). This is the
classical factor-library shape, distinct from the long-form
``stocks.intraday_features`` table that powers
``/admin/feature-coverage``.

Denominator: ``total_rows`` = count of ``(ticker, bar_date)`` rows
in the window. Per-factor coverage is
``non_null_count / total_rows × 100``.

Cache:
``cache:daily_factor_coverage:{start}:{end}`` at ``TTL_STABLE``
(300 s).

Auth: superuser-only — research / data-quality tool.
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
    GreaterThanOrEqual,
    LessThanOrEqual,
)

from auth.dependencies import superuser_only
from auth.models import UserContext
from backend.algo.factors.iceberg_init import (
    ALL_FACTOR_KEYS,
    DAILY_FACTORS_TABLE,
)
from cache import TTL_STABLE, get_cache

_logger = logging.getLogger(__name__)

# All non-key columns that contribute to coverage. Sector is
# string-valued but still meaningful (presence/absence).
_COVERAGE_COLUMNS: tuple[str, ...] = tuple(ALL_FACTOR_KEYS) + ("sector",)


class DailyFactorCoverageRow(BaseModel):
    factor_name: str
    coverage_pct: float
    non_null_rows: int
    tickers_seen: int


class DailyFactorCoverageResponse(BaseModel):
    period_start: date
    period_end: date
    total_rows: int
    tickers_total: int
    coverage: list[DailyFactorCoverageRow]
    computed_at: datetime


def _cache_key(period_start: date, period_end: date) -> str:
    return (
        "cache:daily_factor_coverage:"
        f"{period_start.isoformat()}:"
        f"{period_end.isoformat()}"
    )


def _compute_coverage_sync(
    *,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    """Iceberg scan + per-column non-null count, run from a thread.

    Returns a JSON-ready dict for cache round-tripping.
    """
    from stocks.create_tables import _get_catalog

    row_filter = And(
        GreaterThanOrEqual("bar_date", period_start),
        LessThanOrEqual("bar_date", period_end),
    )

    selected: tuple[str, ...] = ("ticker", "bar_date") + _COVERAGE_COLUMNS
    try:
        cat = _get_catalog()
        tbl = cat.load_table(DAILY_FACTORS_TABLE)
        tbl = tbl.refresh()
        df = tbl.scan(
            row_filter=row_filter,
            selected_fields=selected,
        ).to_pandas()
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "[daily-factor-coverage] Iceberg scan failed "
            "window=%s..%s: %s",
            period_start.isoformat(),
            period_end.isoformat(),
            exc,
            exc_info=True,
        )
        df = None

    now_iso = datetime.now(timezone.utc).isoformat()
    if df is None or df.empty:
        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "total_rows": 0,
            "tickers_total": 0,
            "coverage": [],
            "computed_at": now_iso,
        }

    total_rows = int(len(df))
    tickers_total = int(df["ticker"].drop_duplicates().shape[0])

    coverage: list[dict[str, Any]] = []
    for col in _COVERAGE_COLUMNS:
        if col not in df.columns:
            # Defensive: a factor key was added to ALL_FACTOR_KEYS
            # but the column isn't in the scanned schema yet.
            coverage.append(
                {
                    "factor_name": col,
                    "coverage_pct": 0.0,
                    "non_null_rows": 0,
                    "tickers_seen": 0,
                }
            )
            continue
        # ``notna()`` counts as present; we don't gate on
        # truthiness because 0.0 is a valid factor value.
        non_null_mask = df[col].notna()
        non_null_rows = int(non_null_mask.sum())
        pct = (
            (non_null_rows / total_rows) * 100.0 if total_rows > 0 else 0.0
        )
        if pct > 100.0:
            pct = 100.0
        tickers_seen = int(
            df.loc[non_null_mask, "ticker"].drop_duplicates().shape[0],
        )
        coverage.append(
            {
                "factor_name": str(col),
                "coverage_pct": round(pct, 4),
                "non_null_rows": non_null_rows,
                "tickers_seen": tickers_seen,
            }
        )

    coverage.sort(key=lambda r: r["coverage_pct"], reverse=True)

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "total_rows": total_rows,
        "tickers_total": tickers_total,
        "coverage": coverage,
        "computed_at": now_iso,
    }


def create_daily_factor_coverage_router() -> APIRouter:
    router = APIRouter(
        prefix="/admin",
        tags=["admin"],
    )

    @router.get(
        "/daily-factor-coverage",
        response_model=DailyFactorCoverageResponse,
    )
    async def get_daily_factor_coverage(
        period_start: date = Query(...),
        period_end: date = Query(...),
        _user: UserContext = Depends(superuser_only),
    ) -> DailyFactorCoverageResponse:
        """Coverage matrix for the daily factor library.

        Window is inclusive on both ends. Mirrors the
        ``/admin/feature-coverage`` UX but reads
        ``stocks.daily_factors`` (WIDE) instead of
        ``stocks.intraday_features`` (LONG).
        """
        if period_end < period_start:
            raise HTTPException(
                status_code=400,
                detail="period_end must be >= period_start",
            )

        cache = get_cache()
        key = _cache_key(period_start, period_end)
        try:
            cached = cache.get(key)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[daily-factor-coverage] cache.get crashed "
                "key=%s: %s",
                key,
                exc,
                exc_info=True,
            )
            cached = None
        if cached is not None:
            try:
                import json

                payload = json.loads(cached)
                return DailyFactorCoverageResponse(**payload)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "[daily-factor-coverage] cache blob "
                    "deserialize failed key=%s: %s",
                    key,
                    exc,
                    exc_info=True,
                )

        payload = await asyncio.to_thread(
            _compute_coverage_sync,
            period_start=period_start,
            period_end=period_end,
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
                "[daily-factor-coverage] cache.set crashed "
                "key=%s: %s",
                key,
                exc,
                exc_info=True,
            )

        return DailyFactorCoverageResponse(**payload)

    return router

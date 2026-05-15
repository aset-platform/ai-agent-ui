"""GET /v1/algo/strategies/{strategy_id}/feature-importance —
top-N feature importance ranking for closed-trade research
(ASETPLTFRM-402 / FE-11).

Loads ``stocks.trade_feature_snapshots`` rows for the
requested strategy + date window via PyIceberg, then delegates
the (CPU-bound) sklearn fit to
:func:`backend.algo.features.importance.compute_feature_importance`
inside ``asyncio.to_thread`` so the FastAPI event loop is
never blocked (CLAUDE.md §5.1).

Caching (CLAUDE.md §5.13):
  * key  — ``cache:feature_importance:{strategy_id}:{start}:
    {end}:{top_n}:{min_trades}``
  * ttl  — ``TTL_STABLE`` (300s). Sklearn fits are
    deterministic for a given dataset; a 5-minute staleness
    window is harmless and avoids the re-fit cost on rapid
    repeat queries. Cache invalidation on new snapshot writes
    is a Phase-3-v2 follow-up.

Auth: ``pro_or_superuser`` (research surface, parallels the
factors / drift admin endpoints).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Any

from cache import TTL_STABLE, get_cache
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.dependencies import pro_or_superuser
from auth.models.response import UserContext
from backend.algo.features.importance import (
    FeatureImportanceResult,
    InsufficientDataError,
    compute_feature_importance,
)

_logger = logging.getLogger(__name__)

_TRADE_FEATURE_SNAPSHOTS_TABLE = "stocks.trade_feature_snapshots"

_MAX_TOP_N = 50
_MIN_MIN_TRADES = 5


class FeatureScoreResponse(BaseModel):
    name: str
    importance: float


class FeatureImportanceResponse(BaseModel):
    strategy_id: str
    period_start: date
    period_end: date
    n_trades_used: int
    n_features: int
    top_features: list[FeatureScoreResponse]
    classifier_version: str
    fitted_at: datetime


def _cache_key(
    strategy_id: str,
    period_start: date,
    period_end: date,
    top_n: int,
    min_trades: int,
) -> str:
    return (
        f"cache:feature_importance:{strategy_id}:"
        f"{period_start.isoformat()}:{period_end.isoformat()}:"
        f"{top_n}:{min_trades}"
    )


def _result_to_response(
    result: FeatureImportanceResult,
) -> FeatureImportanceResponse:
    return FeatureImportanceResponse(
        strategy_id=result.strategy_id,
        period_start=result.period_start,
        period_end=result.period_end,
        n_trades_used=result.n_trades_used,
        n_features=result.n_features,
        top_features=[
            FeatureScoreResponse(
                name=fs.name,
                importance=fs.importance,
            )
            for fs in result.top_features
        ],
        classifier_version=result.classifier_version,
        fitted_at=result.fitted_at,
    )


def _load_snapshots(
    *,
    strategy_id: str,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    """Single PyIceberg scan over
    ``stocks.trade_feature_snapshots`` filtered by
    ``strategy_id`` + ``bar_date`` window.

    Partition prune on ``year_month`` fires automatically
    via the per-row ``bar_date`` filter. Returns a list of
    plain dicts so the importance module stays library-free.
    """
    from pyiceberg.expressions import (
        And,
        EqualTo,
        GreaterThanOrEqual,
        LessThanOrEqual,
    )

    from stocks.create_tables import _get_catalog

    iso_start = period_start.isoformat()
    iso_end = period_end.isoformat()

    try:
        cat = _get_catalog()
        tbl = cat.load_table(_TRADE_FEATURE_SNAPSHOTS_TABLE)
        # ``.refresh()`` ensures recent commits are visible —
        # FE-13's outcome backfill is a separate writer.
        tbl = tbl.refresh()
        row_filter = And(
            EqualTo("strategy_id", strategy_id),
            And(
                GreaterThanOrEqual("bar_date", iso_start),
                LessThanOrEqual("bar_date", iso_end),
            ),
        )
        df = tbl.scan(row_filter=row_filter).to_pandas()
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "[feature-importance] Iceberg scan failed "
            "strategy_id=%s window=%s..%s: %s",
            strategy_id,
            iso_start,
            iso_end,
            exc,
            exc_info=True,
        )
        raise

    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


def _train_blocking(
    *,
    snapshots: list[dict[str, Any]],
    strategy_id: str,
    period_start: date,
    period_end: date,
    top_n: int,
    min_trades: int,
) -> FeatureImportanceResult:
    """Sync wrapper around the pure compute function.

    Injects ``strategy_id`` / ``period_start`` / ``period_end``
    onto every snapshot row so the result dataclass carries
    the header metadata even when the snapshot row dict was
    sourced from PyIceberg (which doesn't know about the
    request window).
    """
    for r in snapshots:
        r.setdefault("strategy_id", strategy_id)
        r.setdefault("period_start", period_start)
        r.setdefault("period_end", period_end)
    return compute_feature_importance(
        snapshots,
        top_n=top_n,
        min_trades=min_trades,
    )


def create_feature_importance_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/strategies",
        tags=["algo-trading"],
    )

    @router.get(
        "/{strategy_id}/feature-importance",
        response_model=FeatureImportanceResponse,
    )
    async def get_feature_importance(
        strategy_id: str,
        period_start: date = Query(...),
        period_end: date = Query(...),
        top_n: int = Query(10, ge=1, le=_MAX_TOP_N),
        min_trades: int = Query(30, ge=_MIN_MIN_TRADES),
        _user: UserContext = Depends(pro_or_superuser),
    ) -> FeatureImportanceResponse:
        if period_end < period_start:
            raise HTTPException(
                status_code=400,
                detail=("period_end must be on or after " "period_start"),
            )

        cache = get_cache()
        key = _cache_key(
            strategy_id,
            period_start,
            period_end,
            top_n,
            min_trades,
        )
        try:
            cached = cache.get(key)
        except Exception:  # noqa: BLE001
            cached = None
            _logger.warning(
                "[feature-importance] cache.get crashed " "key=%s",
                key,
                exc_info=True,
            )
        if cached:
            try:
                return FeatureImportanceResponse(
                    **json.loads(cached),
                )
            except (json.JSONDecodeError, ValueError):
                _logger.warning(
                    "[feature-importance] cache blob "
                    "deserialize failed key=%s",
                    key,
                    exc_info=True,
                )

        # Load + train — Iceberg + sklearn are both blocking.
        try:
            snapshots = await asyncio.to_thread(
                _load_snapshots,
                strategy_id=strategy_id,
                period_start=period_start,
                period_end=period_end,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[feature-importance] snapshot load failed "
                "strategy_id=%s: %s",
                strategy_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to load trade snapshots",
            )

        try:
            result = await asyncio.to_thread(
                _train_blocking,
                snapshots=snapshots,
                strategy_id=strategy_id,
                period_start=period_start,
                period_end=period_end,
                top_n=top_n,
                min_trades=min_trades,
            )
        except InsufficientDataError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Strategy {strategy_id} has insufficient "
                    f"labeled trades in "
                    f"[{period_start.isoformat()}, "
                    f"{period_end.isoformat()}] for stable "
                    f"importance ranking: {exc}"
                ),
            )
        except ValueError as exc:
            # GBC needs >= 2 classes — surface as 422 (the
            # caller's data is the constraint violation, not
            # a server-side bug).
            raise HTTPException(
                status_code=422,
                detail=(f"Cannot fit feature importance: {exc}"),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[feature-importance] fit crashed " "strategy_id=%s: %s",
                strategy_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Feature importance fit failed",
            )

        # Re-stamp header fields so the response carries the
        # query window (compute_feature_importance picks up
        # ``period_start`` / ``period_end`` from row dicts;
        # they're always present because ``_train_blocking``
        # injects them, but be explicit here for clarity).
        response = _result_to_response(result)
        response = response.model_copy(
            update={
                "strategy_id": strategy_id,
                "period_start": period_start,
                "period_end": period_end,
            }
        )

        try:
            cache.set(
                key,
                response.model_dump_json(),
                ttl=TTL_STABLE,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "[feature-importance] cache.set crashed " "key=%s",
                key,
                exc_info=True,
            )

        return response

    return router


__all__ = [
    "FeatureImportanceResponse",
    "FeatureScoreResponse",
    "create_feature_importance_router",
]

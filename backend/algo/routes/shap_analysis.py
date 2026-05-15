"""GET /v1/algo/strategies/{strategy_id}/shap — per-prediction
SHAP attribution for FE-11's trade-outcome classifier
(ASETPLTFRM-402 / FE-12 / ASETPLTFRM-414).

Loads ``stocks.trade_feature_snapshots`` rows for the requested
strategy + date window (same loader pattern as FE-11), trains
the GBC via :func:`backend.algo.features.importance.
train_classifier`, then explains each row with
:func:`backend.algo.features.shap_analysis.
compute_shap_for_trades`. Both the load + train + SHAP fit are
CPU-bound and run in ``asyncio.to_thread`` (CLAUDE.md §5.1).

Caching (CLAUDE.md §5.13):
  * key  — ``cache:shap:{strategy_id}:{start}:{end}:{top_n}:
    {min_trades}:{fill_ids_hash}``
  * ttl  — ``TTL_STABLE`` (300s). SHAP attribution is
    deterministic for a given (rows, model) pair.

``fill_ids_hash`` is a SHA-1 of the SORTED unique fill_ids
list (or the literal string ``"all"`` when the param is
absent) — so semantically-equivalent queries hit the same
cache entry regardless of caller ordering.

Auth: ``pro_or_superuser`` — same surface as FE-11's
feature-importance research endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
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
    InsufficientDataError,
    TrainedClassifier,
    train_classifier,
)
from backend.algo.features.shap_analysis import (
    ShapAnalysisResult,
    compute_shap_for_trades,
)

_logger = logging.getLogger(__name__)

_TRADE_FEATURE_SNAPSHOTS_TABLE = "stocks.trade_feature_snapshots"

_MAX_TOP_N = 50
_MIN_MIN_TRADES = 5


class FillShapResponse(BaseModel):
    fill_id: str
    shap_values: dict[str, float]
    base_value: float
    prediction: float


class ShapAnalysisResponse(BaseModel):
    strategy_id: str
    period_start: date
    period_end: date
    n_fills: int
    n_features: int
    classifier_version: str
    top_features_by_mean_abs_shap: list[tuple[str, float]]
    per_fill: list[FillShapResponse]
    computed_at: datetime


def _hash_fill_ids(fill_ids: list[str] | None) -> str:
    """SHA-1 of the SORTED+deduped fill_ids list, or ``"all"``
    when the param is absent. Hash makes the cache key bounded
    even when 1000+ fill_ids are passed.
    """
    if not fill_ids:
        return "all"
    canonical = ",".join(sorted(set(fill_ids)))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _cache_key(
    strategy_id: str,
    period_start: date,
    period_end: date,
    top_n: int,
    min_trades: int,
    fill_ids_hash: str,
) -> str:
    return (
        f"cache:shap:{strategy_id}:"
        f"{period_start.isoformat()}:{period_end.isoformat()}:"
        f"{top_n}:{min_trades}:{fill_ids_hash}"
    )


def _result_to_response(
    result: ShapAnalysisResult,
) -> ShapAnalysisResponse:
    return ShapAnalysisResponse(
        strategy_id=result.strategy_id,
        period_start=result.period_start,
        period_end=result.period_end,
        n_fills=result.n_fills,
        n_features=result.n_features,
        classifier_version=result.classifier_version,
        top_features_by_mean_abs_shap=list(
            result.top_features_by_mean_abs_shap
        ),
        per_fill=[
            FillShapResponse(
                fill_id=fs.fill_id,
                shap_values=fs.shap_values,
                base_value=fs.base_value,
                prediction=fs.prediction,
            )
            for fs in result.per_fill
        ],
        computed_at=result.computed_at,
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

    Mirrors the FE-11 loader so cache keys + behaviour line up.
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
            "[shap] Iceberg scan failed strategy_id=%s " "window=%s..%s: %s",
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
    min_trades: int,
) -> TrainedClassifier:
    """Sync wrapper around :func:`train_classifier`.

    Stamps the header metadata onto every snapshot row so the
    trained-classifier dataclass picks up strategy_id +
    period bounds even when PyIceberg rows omit them.
    """
    for r in snapshots:
        r.setdefault("strategy_id", strategy_id)
        r.setdefault("period_start", period_start)
        r.setdefault("period_end", period_end)
    return train_classifier(snapshots, min_trades=min_trades)


def _shap_blocking(
    *,
    trained: TrainedClassifier,
    fill_ids: list[str] | None,
    top_n: int,
) -> ShapAnalysisResult:
    """Sync wrapper around :func:`compute_shap_for_trades`."""
    return compute_shap_for_trades(
        trained,
        fill_ids=fill_ids,
        top_n=top_n,
    )


def _parse_fill_ids(raw: str | None) -> list[str] | None:
    """Parse ``fill_ids=a,b,c`` query string → list. Trim
    whitespace, drop empty tokens. ``None`` / empty string →
    ``None`` (means "all rows")."""
    if raw is None:
        return None
    tokens = [tok.strip() for tok in raw.split(",")]
    tokens = [tok for tok in tokens if tok]
    return tokens or None


def create_shap_analysis_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/strategies",
        tags=["algo-trading"],
    )

    @router.get(
        "/{strategy_id}/shap",
        response_model=ShapAnalysisResponse,
    )
    async def get_shap_analysis(
        strategy_id: str,
        period_start: date = Query(...),
        period_end: date = Query(...),
        top_n: int = Query(10, ge=1, le=_MAX_TOP_N),
        min_trades: int = Query(30, ge=_MIN_MIN_TRADES),
        fill_ids: str | None = Query(None),
        _user: UserContext = Depends(pro_or_superuser),
    ) -> ShapAnalysisResponse:
        if period_end < period_start:
            raise HTTPException(
                status_code=400,
                detail=("period_end must be on or after " "period_start"),
            )

        fill_ids_list = _parse_fill_ids(fill_ids)
        fill_ids_hash = _hash_fill_ids(fill_ids_list)

        cache = get_cache()
        key = _cache_key(
            strategy_id,
            period_start,
            period_end,
            top_n,
            min_trades,
            fill_ids_hash,
        )
        try:
            cached = cache.get(key)
        except Exception:  # noqa: BLE001
            cached = None
            _logger.warning(
                "[shap] cache.get crashed key=%s",
                key,
                exc_info=True,
            )
        if cached:
            try:
                return ShapAnalysisResponse(**json.loads(cached))
            except (json.JSONDecodeError, ValueError):
                _logger.warning(
                    "[shap] cache blob deserialize failed " "key=%s",
                    key,
                    exc_info=True,
                )

        # Load snapshots.
        try:
            snapshots = await asyncio.to_thread(
                _load_snapshots,
                strategy_id=strategy_id,
                period_start=period_start,
                period_end=period_end,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[shap] snapshot load failed strategy_id=%s: " "%s",
                strategy_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to load trade snapshots",
            )

        # Train the classifier.
        try:
            trained = await asyncio.to_thread(
                _train_blocking,
                snapshots=snapshots,
                strategy_id=strategy_id,
                period_start=period_start,
                period_end=period_end,
                min_trades=min_trades,
            )
        except InsufficientDataError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Strategy {strategy_id} has insufficient "
                    f"labeled trades in "
                    f"[{period_start.isoformat()}, "
                    f"{period_end.isoformat()}] for SHAP "
                    f"attribution: {exc}"
                ),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Cannot train classifier for SHAP " f"attribution: {exc}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[shap] train crashed strategy_id=%s: %s",
                strategy_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Classifier training failed",
            )

        # Compute SHAP attribution.
        try:
            result = await asyncio.to_thread(
                _shap_blocking,
                trained=trained,
                fill_ids=fill_ids_list,
                top_n=top_n,
            )
        except ValueError as exc:
            # Unknown fill_ids surface here as ValueError.
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )
        except RuntimeError as exc:
            # ``shap`` not installed → 503.
            _logger.error(
                "[shap] runtime error strategy_id=%s: %s",
                strategy_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "[shap] attribution crashed strategy_id=%s: " "%s",
                strategy_id,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="SHAP attribution failed",
            )

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
                "[shap] cache.set crashed key=%s",
                key,
                exc_info=True,
            )

        return response

    return router


__all__ = [
    "FillShapResponse",
    "ShapAnalysisResponse",
    "create_shap_analysis_router",
]

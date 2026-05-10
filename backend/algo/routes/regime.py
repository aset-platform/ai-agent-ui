"""GET /v1/algo/regime/* — exposes regime context to frontend.

Endpoints:
  * ``GET /v1/algo/regime/current``           — latest regime row
  * ``GET /v1/algo/regime/history?days=N``    — bar_date window
  * ``GET /v1/algo/regime/classifier-health`` — HMM + regime age

Cache TTLs (per CLAUDE.md §5.13):
  * /current             — TTL_VOLATILE  (60s)
  * /history             — TTL_STABLE    (300s)
  * /classifier-health   — un-cached  (cheap; no Iceberg scan)
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.dependencies import get_current_user
from auth.models.response import UserContext
from backend.algo.regime.repo import (
    get_latest_hmm_state,
    get_latest_regime,
    get_regime_history,
)
from cache import TTL_STABLE, TTL_VOLATILE, get_cache

_logger = logging.getLogger(__name__)


class CurrentRegimeResponse(BaseModel):
    bar_date: date
    regime_label: str
    stress_prob: float | None
    rule_inputs: dict[str, Any]
    classifier_version: str


class RegimeHistoryRow(BaseModel):
    bar_date: date
    regime_label: str
    stress_prob: float | None


class RegimeHistoryResponse(BaseModel):
    rows: list[RegimeHistoryRow]


class ClassifierHealthResponse(BaseModel):
    hmm_trained_through: date | None
    hmm_age_days: int | None
    last_regime_bar_date: date | None
    last_regime_age_days: int | None


# ----------------------------------------------------------------
# Cache helpers — module-level so tests can monkeypatch them
# without having to stub the Redis client itself.
# ----------------------------------------------------------------
def _cache_get(key: str) -> str | None:
    try:
        return get_cache().get(key)
    except Exception as exc:  # pragma: no cover
        _logger.debug("regime cache get failed: %s", exc)
        return None


def _cache_set(key: str, value: str, ttl: int) -> None:
    try:
        get_cache().set(key, value, ttl=ttl)
    except Exception as exc:  # pragma: no cover
        _logger.debug("regime cache set failed: %s", exc)


def create_regime_router() -> APIRouter:
    """Factory matches the rest of the algo route modules so the
    aggregator in ``backend.algo.routes`` can re-export it."""
    router = APIRouter(prefix="/algo/regime", tags=["algo-regime"])

    @router.get("/current", response_model=CurrentRegimeResponse)
    def current(
        _user: UserContext = Depends(get_current_user),
    ) -> CurrentRegimeResponse:
        cached = _cache_get("cache:regime:current")
        if cached:
            return CurrentRegimeResponse(**json.loads(cached))

        row = get_latest_regime()
        if row is None:
            raise HTTPException(404, "No regime row yet")
        resp = CurrentRegimeResponse(
            bar_date=row.bar_date,
            regime_label=row.regime_label,
            stress_prob=row.stress_prob,
            rule_inputs=row.rule_inputs,
            classifier_version=row.classifier_version,
        )
        _cache_set(
            "cache:regime:current",
            resp.model_dump_json(),
            ttl=TTL_VOLATILE,
        )
        return resp

    @router.get("/history", response_model=RegimeHistoryResponse)
    def history(
        days: int = Query(252, ge=1, le=1095),
        _user: UserContext = Depends(get_current_user),
    ) -> RegimeHistoryResponse:
        end = date.today()
        start = end - timedelta(days=days)
        key = f"cache:regime:history:{start}:{end}"
        cached = _cache_get(key)
        if cached:
            return RegimeHistoryResponse(**json.loads(cached))

        rows = get_regime_history(start=start, end=end)
        resp = RegimeHistoryResponse(rows=[
            RegimeHistoryRow(
                bar_date=r.bar_date,
                regime_label=r.regime_label,
                stress_prob=r.stress_prob,
            )
            for r in rows
        ])
        _cache_set(key, resp.model_dump_json(), ttl=TTL_STABLE)
        return resp

    @router.get(
        "/classifier-health",
        response_model=ClassifierHealthResponse,
    )
    def classifier_health(
        _user: UserContext = Depends(get_current_user),
    ) -> ClassifierHealthResponse:
        today = date.today()
        hmm = get_latest_hmm_state()
        last = get_latest_regime()
        return ClassifierHealthResponse(
            hmm_trained_through=(
                hmm.trained_through if hmm else None
            ),
            hmm_age_days=(
                (today - hmm.trained_through).days
                if hmm else None
            ),
            last_regime_bar_date=(
                last.bar_date if last else None
            ),
            last_regime_age_days=(
                (today - last.bar_date).days
                if last else None
            ),
        )

    return router

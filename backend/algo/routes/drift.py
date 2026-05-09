"""Drift-state routes — V2-3.

``GET  /algo/drift``          — list open drifts for the caller.
``PATCH /algo/drift/threshold`` — update per-user drift threshold.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.auth.dependencies import pro_or_superuser
from backend.auth.models import UserContext

_logger = logging.getLogger(__name__)


class DriftThresholdRequest(BaseModel):
    threshold_shares: int = Field(
        ge=0,
        description=(
            "Absolute share difference that counts as drift. "
            "0 = any non-zero diff."
        ),
    )


def create_drift_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/drift", tags=["algo-trading"],
    )

    @router.get("")
    async def get_open_drifts(
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """List all unresolved position drifts for the caller."""
        from backend.algo.live.drift_repo import DriftRepo
        repo = DriftRepo()
        user_uuid = UUID(user.user_id)
        rows = await repo.get_open_drifts(user_uuid)
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "symbol": r["symbol"],
                "first_seen_at": (
                    r["first_seen_at"].isoformat()
                    if r.get("first_seen_at") else None
                ),
                "consecutive_runs": int(
                    r.get("consecutive_runs", 1),
                ),
                "last_diff": r.get("last_diff") or {},
                "resolved_at": (
                    r["resolved_at"].isoformat()
                    if r.get("resolved_at") else None
                ),
            })
        return out

    @router.get("/threshold")
    async def get_threshold(
        user: UserContext = Depends(pro_or_superuser),
    ) -> dict[str, int]:
        """Return the caller's current drift threshold."""
        from backend.algo.live.drift_repo import DriftRepo
        repo = DriftRepo()
        threshold = await repo.get_drift_threshold(
            UUID(user.user_id),
        )
        return {"threshold_shares": threshold}

    @router.patch("/threshold", status_code=200)
    async def set_threshold(
        body: DriftThresholdRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> dict[str, int]:
        """Update the caller's drift threshold."""
        from backend.algo.live.drift_repo import DriftRepo
        repo = DriftRepo()
        await repo.set_drift_threshold(
            UUID(user.user_id), body.threshold_shares,
        )
        return {"threshold_shares": body.threshold_shares}

    return router

# backend/algo/routes/strategies.py
"""CRUD endpoints for algo.strategies."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.strategy.ast import Strategy, parse_strategy
from backend.algo.strategy.metadata_repo import (
    StrategyMetadata,
    get_metadata,
    upsert_metadata,
)
from backend.algo.strategy.repo import (
    archive_strategy,
    create_strategy,
    get_strategy,
    hard_delete_strategy,
    list_strategies,
    update_strategy,
)

# REGIME-3: default = regime-agnostic (all 3).
_DEFAULT_REGIMES: list[str] = ["bull", "sideways", "bear"]

_logger = logging.getLogger(__name__)


def _serialize_validation_errors(exc: ValidationError) -> list[dict]:
    """Return JSON-serializable error list from a Pydantic error.

    Pydantic 2's ``ctx`` dict may contain ``ValueError`` objects;
    convert them to strings so FastAPI can JSON-serialize the response.
    """
    out = []
    for e in exc.errors(include_url=False):
        entry = {k: v for k, v in e.items() if k != "ctx"}
        if "ctx" in e:
            entry["ctx"] = {
                k: str(v) for k, v in e["ctx"].items()
            }
        out.append(entry)
    return out


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


class StrategySummary(BaseModel):
    id: UUID
    name: str
    mode: str
    status: str
    created_at: Any
    updated_at: Any
    archived_at: Any


class StrategyListResponse(BaseModel):
    strategies: list[StrategySummary]


class StrategyCreateRequest(BaseModel):
    payload: dict = Field(..., description="Full AST payload")
    # REGIME-3: optional binding.  ``None`` means "use default
    # all-3-regimes" — kept distinct from an explicit empty list so
    # the API can later add stricter semantics if needed.
    applicable_regimes: list[str] | None = Field(
        default=None,
        description="Regimes the strategy is designed for; "
        "default = ['bull','sideways','bear'] (regime-agnostic).",
    )


class StrategyCreateResponse(BaseModel):
    id: UUID


class StrategyResponse(BaseModel):
    """REGIME-3 GET wrapper.

    The bare AST (``Strategy``) has ``extra='forbid'`` so we cannot
    splice ``applicable_regimes`` onto it directly.  This wrapper
    nests the AST and exposes the metadata as a sibling field.
    """
    strategy: Strategy
    applicable_regimes: list[str]


def create_strategies_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/strategies", tags=["algo-trading"],
    )

    @router.get("", response_model=StrategyListResponse)
    async def list_(
        user: UserContext = Depends(pro_or_superuser),
        include_archived: bool = False,
    ) -> StrategyListResponse:
        factory = _get_session_factory()
        async with factory() as session:
            rows = await list_strategies(
                session, UUID(user.user_id),
                include_archived=include_archived,
            )
        return StrategyListResponse(
            strategies=[StrategySummary(**r) for r in rows],
        )

    @router.get("/{strategy_id}", response_model=StrategyResponse)
    async def get_(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> StrategyResponse:
        factory = _get_session_factory()
        async with factory() as session:
            s = await get_strategy(
                session, UUID(user.user_id), strategy_id,
            )
            if s is None:
                raise HTTPException(
                    status_code=404, detail="Strategy not found",
                )
            md = await get_metadata(session, strategy_id)
        regimes = (
            list(md.applicable_regimes)
            if md is not None and md.applicable_regimes
            else list(_DEFAULT_REGIMES)
        )
        return StrategyResponse(strategy=s, applicable_regimes=regimes)

    @router.post(
        "",
        status_code=status.HTTP_201_CREATED,
        response_model=StrategyCreateResponse,
    )
    async def create_(
        body: StrategyCreateRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> StrategyCreateResponse:
        try:
            strategy = parse_strategy(body.payload)
        except ValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=_serialize_validation_errors(exc),
            )
        factory = _get_session_factory()
        regimes = (
            list(body.applicable_regimes)
            if body.applicable_regimes is not None
            else list(_DEFAULT_REGIMES)
        )
        async with factory() as session:
            new_id = await create_strategy(
                session, UUID(user.user_id), strategy,
            )
            await upsert_metadata(
                session, new_id,
                StrategyMetadata(applicable_regimes=regimes),
            )
            await session.commit()
        return StrategyCreateResponse(id=new_id)

    @router.put(
        "/{strategy_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def update_(
        strategy_id: UUID,
        body: StrategyCreateRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> None:
        try:
            strategy = parse_strategy(body.payload)
        except ValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=_serialize_validation_errors(exc),
            )
        factory = _get_session_factory()
        async with factory() as session:
            ok = await update_strategy(
                session, UUID(user.user_id), strategy_id, strategy,
            )
            if not ok:
                raise HTTPException(
                    status_code=404, detail="Strategy not found",
                )
            # REGIME-3: PUT also upserts metadata.  Pass-through
            # default = all 3 when client omits applicable_regimes.
            regimes = (
                list(body.applicable_regimes)
                if body.applicable_regimes is not None
                else list(_DEFAULT_REGIMES)
            )
            await upsert_metadata(
                session, strategy_id,
                StrategyMetadata(applicable_regimes=regimes),
            )
            await session.commit()

    @router.delete(
        "/{strategy_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def archive_(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> None:
        # Soft-archive first; hard-delete only when called on
        # an already-archived row (idempotent client UX).
        factory = _get_session_factory()
        async with factory() as session:
            archived = await archive_strategy(
                session, UUID(user.user_id), strategy_id,
            )
            if archived:
                return
            hard_deleted = await hard_delete_strategy(
                session, UUID(user.user_id), strategy_id,
            )
        if hard_deleted:
            return
        raise HTTPException(
            status_code=404, detail="Strategy not found",
        )

    return router

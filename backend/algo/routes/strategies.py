# backend/algo/routes/strategies.py
"""CRUD endpoints for algo.strategies."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.strategy.ast import Strategy, parse_strategy
from backend.algo.strategy.metadata_repo import (
    StrategyMetadata,
    get_metadata,
    upsert_metadata,
)
from backend.algo.strategy.mode_repo import (
    ALL_MODES,
    MODE_DRAFT,
    MODE_LIVE,
    MODE_PAPER,
    hash_ast,
    list_transitions,
    write_transition,
)
from backend.algo.strategy.promotion import (
    can_take_legal_step,
    check_eligibility,
    is_known_mode,
)
from backend.algo.strategy.repo import (
    archive_strategy,
    create_strategy,
    get_strategy,
    hard_delete_strategy,
    list_strategies,
    update_strategy,
)
from backend.algo.strategy.runtime_state import get_runtime_state

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
    # Promotion-workflow extensions (added by the promotion
    # epic). Optional / defaulted so legacy clients deserialise.
    has_active_runtime: bool = False
    active_runtime_modes: list[str] = Field(default_factory=list)
    open_position_count: int = 0
    has_ever_been_live: bool = False
    last_transition_at: Any = None
    last_transition_by: str | None = None


class StrategyListResponse(BaseModel):
    strategies: list[StrategySummary]


class ModeTransitionRow(BaseModel):
    id: UUID
    from_mode: str | None
    to_mode: str
    reason: str | None
    bypass_used: bool
    user_email: str
    ast_hash: str | None
    transitioned_at: Any


class TransitionEligibilityResponse(BaseModel):
    target: str
    allowed: bool
    reasons: list[str]
    bypass_available: bool


class EligibilityResponseBody(BaseModel):
    current_mode: str
    transitions: list[TransitionEligibilityResponse]


class ModePatchRequest(BaseModel):
    mode: str = Field(..., description="Target mode")
    bypass: bool = Field(
        default=False,
        description=(
            "Skip workflow gates. Only honoured when the "
            "eligibility check reports bypass_available=true "
            "for the target."
        ),
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Free-form note recorded on the audit row. Required "
            "when bypass=true so the trail explains the override."
        ),
    )


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
        user_uuid = UUID(user.user_id)
        async with factory() as session:
            rows = await list_strategies(
                session, user_uuid,
                include_archived=include_archived,
            )
            enriched: list[StrategySummary] = []
            for r in rows:
                sid = r["id"]
                runtime = await get_runtime_state(
                    session, strategy_id=sid, user_id=user_uuid,
                )
                transitions = await list_transitions(
                    session, strategy_id=sid, limit=200,
                )
                has_ever_been_live = any(
                    t.to_mode == MODE_LIVE for t in transitions
                )
                last = transitions[0] if transitions else None
                enriched.append(
                    StrategySummary(
                        **r,
                        has_active_runtime=runtime.has_active_runtime,
                        active_runtime_modes=runtime.active_modes,
                        open_position_count=runtime.open_position_count,
                        has_ever_been_live=has_ever_been_live,
                        last_transition_at=(
                            last.transitioned_at if last else None
                        ),
                        last_transition_by=(
                            last.user_email if last else None
                        ),
                    )
                )
        return StrategyListResponse(strategies=enriched)

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
        user_uuid = UUID(user.user_id)
        async with factory() as session:
            result = await update_strategy(
                session, user_uuid, strategy_id, strategy,
            )
            if not result.found:
                raise HTTPException(
                    status_code=404, detail="Strategy not found",
                )
            # Auto-demote audit: any save on a non-draft strategy
            # flipped mode to draft inside update_strategy. Record
            # the transition so the history popover surfaces it
            # and the bypass eligibility check can fire later.
            if result.demoted_from is not None:
                await write_transition(
                    session,
                    strategy_id=strategy_id,
                    user_id=user_uuid,
                    user_email=user.email or "unknown",
                    from_mode=result.demoted_from,
                    to_mode=MODE_DRAFT,
                    reason="auto-demoted on AST edit",
                    bypass_used=False,
                    ast_hash=result.ast_hash,
                )
                await session.commit()
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

    @router.post(
        "/{strategy_id}/clone",
        status_code=status.HTTP_201_CREATED,
        response_model=StrategyCreateResponse,
    )
    async def clone_(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> StrategyCreateResponse:
        factory = _get_session_factory()
        async with factory() as session:
            source = await get_strategy(
                session, UUID(user.user_id), strategy_id,
            )
            if source is None:
                raise HTTPException(
                    status_code=404, detail="Strategy not found",
                )
            source_md = await get_metadata(session, strategy_id)

            # Suffix the name once; the AST id is re-minted by
            # create_strategy so the clone is a fresh row.
            cloned = source.model_copy(
                update={"name": f"{source.name} (Copy)"},
            )
            new_id = await create_strategy(
                session, UUID(user.user_id), cloned,
            )

            regimes = (
                list(source_md.applicable_regimes)
                if source_md is not None
                and source_md.applicable_regimes
                else list(_DEFAULT_REGIMES)
            )
            await upsert_metadata(
                session, new_id,
                StrategyMetadata(applicable_regimes=regimes),
            )
            await session.commit()
        return StrategyCreateResponse(id=new_id)

    @router.get(
        "/{strategy_id}/mode-transitions/eligibility",
        response_model=EligibilityResponseBody,
    )
    async def eligibility_(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> EligibilityResponseBody:
        factory = _get_session_factory()
        user_uuid = UUID(user.user_id)
        async with factory() as session:
            s = await get_strategy(session, user_uuid, strategy_id)
            if s is None:
                raise HTTPException(
                    status_code=404, detail="Strategy not found",
                )
            # Mode lives on the strategies row, not on the parsed
            # AST. Re-read it directly so we use what's in the DB.
            row = (
                await session.execute(
                    text(
                        "SELECT mode FROM algo.strategies "
                        "WHERE id = :sid AND user_id = :uid"
                    ),
                    {"sid": strategy_id, "uid": user_uuid},
                )
            ).first()
            if row is None:
                raise HTTPException(
                    status_code=404, detail="Strategy not found",
                )
            current_mode = row[0]
            elig = await check_eligibility(
                session,
                strategy_id=strategy_id,
                current_mode=current_mode,
            )
        return EligibilityResponseBody(
            current_mode=elig.current_mode,
            transitions=[
                TransitionEligibilityResponse(
                    target=t.target,
                    allowed=t.allowed,
                    reasons=t.reasons,
                    bypass_available=t.bypass_available,
                )
                for t in elig.transitions
            ],
        )

    @router.get(
        "/{strategy_id}/mode-transitions",
        response_model=list[ModeTransitionRow],
    )
    async def transitions_(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[ModeTransitionRow]:
        factory = _get_session_factory()
        user_uuid = UUID(user.user_id)
        async with factory() as session:
            # Ownership guard — only the strategy's owner can see
            # its transition history.
            s = await get_strategy(session, user_uuid, strategy_id)
            if s is None:
                raise HTTPException(
                    status_code=404, detail="Strategy not found",
                )
            rows = await list_transitions(
                session, strategy_id=strategy_id, limit=100,
            )
        return [
            ModeTransitionRow(
                id=r.id,
                from_mode=r.from_mode,
                to_mode=r.to_mode,
                reason=r.reason,
                bypass_used=r.bypass_used,
                user_email=r.user_email,
                ast_hash=r.ast_hash,
                transitioned_at=r.transitioned_at,
            )
            for r in rows
        ]

    @router.patch(
        "/{strategy_id}/mode",
        status_code=status.HTTP_200_OK,
    )
    async def patch_mode_(
        strategy_id: UUID,
        body: ModePatchRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> dict:
        if not is_known_mode(body.mode):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown mode {body.mode!r}; expected one "
                    f"of {sorted(ALL_MODES)}."
                ),
            )
        if body.bypass and not (body.reason or "").strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "Bypass promotions require a non-empty "
                    "'reason' on the audit row."
                ),
            )
        factory = _get_session_factory()
        user_uuid = UUID(user.user_id)
        async with factory() as session:
            s = await get_strategy(session, user_uuid, strategy_id)
            if s is None:
                raise HTTPException(
                    status_code=404, detail="Strategy not found",
                )
            cur_row = (
                await session.execute(
                    text(
                        "SELECT mode FROM algo.strategies "
                        "WHERE id = :sid AND user_id = :uid "
                        "  AND archived_at IS NULL"
                    ),
                    {"sid": strategy_id, "uid": user_uuid},
                )
            ).first()
            if cur_row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Strategy not found or archived",
                )
            current_mode = cur_row[0]

            if body.mode == current_mode:
                return {
                    "status": "noop",
                    "mode": current_mode,
                }

            elig = await check_eligibility(
                session,
                strategy_id=strategy_id,
                current_mode=current_mode,
            )
            target = next(
                (t for t in elig.transitions if t.target == body.mode),
                None,
            )
            if target is None or not can_take_legal_step(
                current_mode, body.mode,
            ):
                # Bypass exists only for the live target. When
                # set, it lets us skip a one-step legality check
                # — e.g. draft → live in one PATCH.
                if body.bypass and body.mode == MODE_LIVE:
                    pass
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Cannot transition from "
                            f"{current_mode!r} to {body.mode!r}."
                        ),
                    )

            if body.bypass:
                if target is None or not target.bypass_available:
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "Bypass not available for this "
                            "strategy. A strategy must have "
                            "previously held mode='live' to "
                            "qualify for the fast-lane "
                            "re-promotion."
                        ),
                    )
            else:
                if target is None or not target.allowed:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": (
                                "Promotion gates not satisfied."
                            ),
                            "missing": (
                                target.reasons if target else []
                            ),
                        },
                    )

            # Stamp the current AST hash on the transition row so
            # the audit ledger captures exactly what was promoted.
            ast_hash = hash_ast(
                s.model_dump(mode="json", by_alias=True),
            )

            await session.execute(
                text(
                    "UPDATE algo.strategies SET "
                    "  mode = :mode, updated_at = NOW() "
                    "WHERE id = :sid AND user_id = :uid"
                ),
                {
                    "mode": body.mode,
                    "sid": strategy_id,
                    "uid": user_uuid,
                },
            )
            transition_id = await write_transition(
                session,
                strategy_id=strategy_id,
                user_id=user_uuid,
                user_email=user.email or "unknown",
                from_mode=current_mode,
                to_mode=body.mode,
                reason=(body.reason or None),
                bypass_used=body.bypass,
                ast_hash=ast_hash,
            )
            await session.commit()
        return {
            "status": "ok",
            "mode": body.mode,
            "transition_id": str(transition_id),
            "from_mode": current_mode,
            "bypass_used": body.bypass,
        }

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

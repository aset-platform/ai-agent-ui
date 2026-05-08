"""GET /v1/algo/kill-switch
POST /v1/algo/kill-switch/arm
POST /v1/algo/kill-switch/disarm

Per spec § 5.4. Re-arming requires a confirm dialog UI-side;
backend just exposes the toggle. Reason string optional.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.paper.kill_switch_repo import KillSwitchRepo
from backend.algo.paper.types import KillSwitchState

_logger = logging.getLogger(__name__)


class ArmRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=256)


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def _get_redis():
    """Returns an async Redis client (or None).

    Wired in Slice 8c via backend/algo/redis_async.py — uses
    redis.asyncio.from_url(REDIS_URL). Returns None gracefully
    when REDIS_URL is empty so the repo runs PG-only.
    """
    from backend.algo.redis_async import get_async_redis
    return get_async_redis()


def create_kill_switch_router() -> APIRouter:
    router = APIRouter(prefix="/algo", tags=["algo-trading"])

    @router.get(
        "/kill-switch", response_model=KillSwitchState,
    )
    async def get_state(
        user: UserContext = Depends(pro_or_superuser),
    ) -> KillSwitchState:
        repo = KillSwitchRepo(redis_client=_get_redis())
        factory = _get_session_factory()
        async with factory() as session:
            row = await repo.get(
                session, user_id=UUID(user.user_id),
            )
        return KillSwitchState(**row)

    @router.post(
        "/kill-switch/arm", response_model=KillSwitchState,
    )
    async def arm(
        body: ArmRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> KillSwitchState:
        repo = KillSwitchRepo(redis_client=_get_redis())
        factory = _get_session_factory()
        async with factory() as session:
            await repo.arm(
                session,
                user_id=UUID(user.user_id),
                set_by=UUID(user.user_id),
                reason=body.reason,
            )
            await session.commit()
            row = await repo.get(
                session, user_id=UUID(user.user_id),
            )
        return KillSwitchState(**row)

    @router.post(
        "/kill-switch/disarm", response_model=KillSwitchState,
    )
    async def disarm(
        user: UserContext = Depends(pro_or_superuser),
    ) -> KillSwitchState:
        repo = KillSwitchRepo(redis_client=_get_redis())
        factory = _get_session_factory()
        async with factory() as session:
            await repo.disarm(
                session, user_id=UUID(user.user_id),
            )
            await session.commit()
            row = await repo.get(
                session, user_id=UUID(user.user_id),
            )
        return KillSwitchState(**row)

    return router

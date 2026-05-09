"""Kill switch — durable in algo.kill_switch, fast read in Redis.

Per spec § 5.4: arming sets the flag in BOTH PG and Redis;
disarming clears both. Runtime checks only Redis (sub-ms);
restart-replay reads PG and rehydrates Redis.

Redis key: algo:kill:{user_id}, value "1" if armed, absent if not.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)


def _redis_key(user_id: UUID) -> str:
    return f"algo:kill:{user_id}"


class KillSwitchRepo:
    def __init__(self, redis_client=None) -> None:  # noqa: ANN001
        self._redis = redis_client

    async def is_active(self, user_id: UUID) -> bool:
        """Fast read — Redis first, falls back to False if Redis
        unavailable (graceful degradation)."""
        if self._redis is not None:
            try:
                v = await self._redis.get(_redis_key(user_id))
                return bool(v)
            except Exception:  # noqa: BLE001
                _logger.warning(
                    "Redis kill-switch read failed; falling back",
                )
        return False

    async def get(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> dict[str, Any]:
        result = await session.execute(
            text(
                "SELECT user_id, active, set_by, set_at, reason "
                "FROM algo.kill_switch WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        row = result.mappings().first()
        if row is None:
            return {
                "user_id": user_id,
                "active": False,
                "set_by": None,
                "set_at": None,
                "reason": None,
            }
        return dict(row)

    async def arm(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        set_by: UUID,
        reason: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                "INSERT INTO algo.kill_switch ("
                "  user_id, active, set_by, set_at, reason) "
                "VALUES (:uid, true, :sb, :sa, :rs) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "  active = true, set_by = :sb, "
                "  set_at = :sa, reason = :rs"
            ),
            {
                "uid": user_id, "sb": set_by, "sa": now,
                "rs": reason,
            },
        )
        if self._redis is not None:
            try:
                await self._redis.set(
                    _redis_key(user_id), "1",
                )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Redis kill-switch arm mirror failed",
                )

    async def disarm(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> None:
        await session.execute(
            text(
                "UPDATE algo.kill_switch SET active = false "
                "WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        if self._redis is not None:
            try:
                await self._redis.delete(_redis_key(user_id))
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "Redis kill-switch disarm mirror failed",
                )

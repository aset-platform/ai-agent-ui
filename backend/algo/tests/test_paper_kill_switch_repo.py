"""KillSwitchRepo — PG + Redis mirror."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.algo.paper.kill_switch_repo import KillSwitchRepo


class _StubSession:
    def __init__(self) -> None:
        self.rows: dict = {}

    async def execute(self, q, params=None):  # noqa: ANN001
        sql = str(q)
        params = dict(params or {})

        class _Res:
            def __init__(self, items):
                self._items = items

            def mappings(self):
                return self

            def first(self):
                return self._items[0] if self._items else None

        if "SELECT user_id, active" in sql:
            row = self.rows.get(params["uid"])
            return _Res([row] if row else [])
        if "INSERT INTO algo.kill_switch" in sql:
            self.rows[params["uid"]] = {
                "user_id": params["uid"],
                "active": True,
                "set_by": params["sb"],
                "set_at": params["sa"],
                "reason": params["rs"],
            }
            return _Res([])
        if "UPDATE algo.kill_switch" in sql:
            row = self.rows.get(params["uid"])
            if row:
                row["active"] = False
            return _Res([])
        return _Res([])


@pytest.mark.asyncio
async def test_arm_writes_pg_and_redis():
    redis = AsyncMock()
    redis.set = AsyncMock()
    repo = KillSwitchRepo(redis_client=redis)
    session = _StubSession()
    user_id = uuid4()
    await repo.arm(
        session, user_id=user_id, set_by=user_id,
        reason="manual",
    )
    state = await repo.get(session, user_id=user_id)
    assert state["active"] is True
    redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_disarm_clears_pg_and_redis():
    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    repo = KillSwitchRepo(redis_client=redis)
    session = _StubSession()
    user_id = uuid4()
    await repo.arm(
        session, user_id=user_id, set_by=user_id,
        reason=None,
    )
    await repo.disarm(session, user_id=user_id)
    state = await repo.get(session, user_id=user_id)
    assert state["active"] is False
    redis.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_active_reads_redis_only():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"1")
    repo = KillSwitchRepo(redis_client=redis)
    user_id = uuid4()
    assert await repo.is_active(user_id) is True


@pytest.mark.asyncio
async def test_is_active_returns_false_on_redis_error():
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=RuntimeError("boom"))
    repo = KillSwitchRepo(redis_client=redis)
    user_id = uuid4()
    assert await repo.is_active(user_id) is False


@pytest.mark.asyncio
async def test_get_returns_default_when_row_missing():
    repo = KillSwitchRepo(redis_client=None)
    session = _StubSession()
    state = await repo.get(session, user_id=uuid4())
    assert state["active"] is False
    assert state["set_at"] is None

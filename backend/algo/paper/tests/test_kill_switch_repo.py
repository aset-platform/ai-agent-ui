"""Tests for KillSwitchRepo.is_active fail-closed behaviour.

Critical safety gate: if Redis is unavailable and no PG fallback
can confirm the switch is DISARMED, is_active() must return True
(fail CLOSED) so a halted strategy never resumes trading silently.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from backend.algo.paper.kill_switch_repo import KillSwitchRepo


@pytest.mark.asyncio
async def test_redis_armed_returns_true():
    r = AsyncMock()
    r.get.return_value = b"1"
    assert await KillSwitchRepo(r).is_active(uuid4()) is True


@pytest.mark.asyncio
async def test_redis_error_fails_closed_without_pg():
    r = AsyncMock()
    r.get.side_effect = RuntimeError("redis down")
    # No session_factory → cannot confirm safe → fail CLOSED (armed).
    assert await KillSwitchRepo(r).is_active(uuid4()) is True


@pytest.mark.asyncio
async def test_redis_error_falls_back_to_pg_active():
    r = AsyncMock()
    r.get.side_effect = RuntimeError("redis down")
    repo = KillSwitchRepo(r)

    async def fake_get(session, *, user_id):
        return {"active": True}

    repo.get = AsyncMock(side_effect=fake_get)  # type: ignore
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    sf.return_value.__aexit__ = AsyncMock(return_value=False)
    assert await repo.is_active(uuid4(), session_factory=sf) is True


@pytest.mark.asyncio
async def test_redis_error_falls_back_to_pg_inactive():
    r = AsyncMock()
    r.get.side_effect = RuntimeError("redis down")
    repo = KillSwitchRepo(r)

    async def fake_get(session, *, user_id):
        return {"active": False}

    repo.get = AsyncMock(side_effect=fake_get)  # type: ignore
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    sf.return_value.__aexit__ = AsyncMock(return_value=False)
    assert await repo.is_active(uuid4(), session_factory=sf) is False


@pytest.mark.asyncio
async def test_no_redis_no_pg_fails_closed():
    assert await KillSwitchRepo(None).is_active(uuid4()) is True

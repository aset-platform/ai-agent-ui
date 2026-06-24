"""dry_run_flag arm/disarm write-failure surfaces (Task 0.4).

Tests:
1. arm raises on Redis write failure (fail-open eliminated).
2. disarm raises on Redis write failure (fail-open eliminated).
3. is_armed returns gracefully on Redis READ failure (unchanged).

Patched at backend.algo.live.dry_run_flag (source module),
matching the project's mock-patching-gotchas convention.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.algo.live.dry_run_flag import arm, disarm, is_armed

_USER_ID = uuid4()


# ---------------------------------------------------------------
# WRITE tests — must raise on Redis failure
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_arm_raises_on_redis_write_failure():
    """arm() must raise (not return env-default) when Redis set fails,
    so the calling route returns 5xx and the UI shows the error."""
    redis = AsyncMock()
    redis.set.side_effect = ConnectionError("Redis unavailable")

    with pytest.raises(ConnectionError):
        await arm(_USER_ID, redis)


@pytest.mark.asyncio
async def test_disarm_raises_on_redis_write_failure():
    """disarm() must raise (not return env-default) when Redis set fails,
    so the calling route returns 5xx and the UI shows the error."""
    redis = AsyncMock()
    redis.set.side_effect = RuntimeError("Redis timeout")

    with pytest.raises(RuntimeError):
        await disarm(_USER_ID, redis)


# ---------------------------------------------------------------
# READ test — must NOT raise; graceful fallback preserved
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_armed_returns_gracefully_on_redis_read_failure(monkeypatch):
    """is_armed() must NOT raise on a Redis get failure; it falls
    back to the env default (graceful 3-tier resolution unchanged)."""
    monkeypatch.delenv("ALGO_LIVE_DRY_RUN", raising=False)
    redis = AsyncMock()
    redis.get.side_effect = ConnectionError("Redis unavailable")

    # Must not raise; env default is False when ALGO_LIVE_DRY_RUN unset
    result = await is_armed(_USER_ID, redis)
    assert result is False


@pytest.mark.asyncio
async def test_arm_succeeds_sets_redis_and_returns_true():
    """Control: arm() with a healthy Redis client sets the key
    and returns True."""
    redis = AsyncMock()
    redis.set.return_value = True

    result = await arm(_USER_ID, redis)

    redis.set.assert_awaited_once()
    assert result is True


@pytest.mark.asyncio
async def test_disarm_succeeds_sets_redis_and_returns_false():
    """Control: disarm() with a healthy Redis client sets the key
    and returns False."""
    redis = AsyncMock()
    redis.set.return_value = True

    result = await disarm(_USER_ID, redis)

    redis.set.assert_awaited_once()
    assert result is False

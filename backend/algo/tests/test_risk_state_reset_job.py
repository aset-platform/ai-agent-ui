"""algo_risk_state_reset scheduler job."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_reset_job_calls_reset_for_each_user():
    from backend.algo.jobs.risk_state_reset import (
        run_risk_state_reset_job,
    )

    user_a, user_b = uuid4(), uuid4()
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()

    class _Res:
        def mappings(self): return self
        def all(self):
            return [{"user_id": user_a}, {"user_id": user_b}]

    fake_session.execute = AsyncMock(return_value=_Res())

    @asynccontextmanager
    async def fake_factory_ctx():
        yield fake_session

    fake_factory = MagicMock(return_value=fake_factory_ctx())

    repo = MagicMock()
    repo.reset_for_day = AsyncMock()

    with patch(
        "backend.algo.jobs.risk_state_reset.get_session_factory",
        return_value=fake_factory,
    ), patch(
        "backend.algo.jobs.risk_state_reset.RiskStateRepo",
        return_value=repo,
    ):
        result = await run_risk_state_reset_job(None)

    assert result["reset_users"] == 2
    assert repo.reset_for_day.await_count == 2


@pytest.mark.asyncio
async def test_reset_job_handles_no_users():
    from backend.algo.jobs.risk_state_reset import (
        run_risk_state_reset_job,
    )

    fake_session = MagicMock()
    fake_session.commit = AsyncMock()

    class _Res:
        def mappings(self): return self
        def all(self): return []

    fake_session.execute = AsyncMock(return_value=_Res())

    @asynccontextmanager
    async def fake_factory_ctx():
        yield fake_session

    fake_factory = MagicMock(return_value=fake_factory_ctx())

    repo = MagicMock()
    repo.reset_for_day = AsyncMock()

    with patch(
        "backend.algo.jobs.risk_state_reset.get_session_factory",
        return_value=fake_factory,
    ), patch(
        "backend.algo.jobs.risk_state_reset.RiskStateRepo",
        return_value=repo,
    ):
        result = await run_risk_state_reset_job(None)

    assert result["reset_users"] == 0
    repo.reset_for_day.assert_not_awaited()

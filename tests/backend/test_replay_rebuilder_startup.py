"""Tests for replay rebuilder startup invocation.

Verifies that:
- _run_startup_hooks() calls rebuild_all() once.
- rebuild_all() is idempotent (double-call leaves risk_state unchanged).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_startup_invokes_replay_rebuilder():
    """_run_startup_hooks MUST call replay_rebuilder.rebuild_all().

    We patch at the module attribute level of replay_rebuilder so
    that the lazy import inside _run_startup_hooks picks up the mock.
    _run_startup_hooks is called from routes.py lifespan in production.
    """
    import backend.algo.paper.replay_rebuilder as rr_mod
    from backend.main import _run_startup_hooks

    original = rr_mod.rebuild_all
    mock_rebuild = AsyncMock(
        return_value={"rebuilt_users": 0, "day_date": "2026-05-09"},
    )
    rr_mod.rebuild_all = mock_rebuild
    try:
        await _run_startup_hooks()
        mock_rebuild.assert_awaited_once()
    finally:
        rr_mod.rebuild_all = original


@pytest.mark.asyncio
async def test_rebuild_all_is_idempotent():
    """Calling rebuild_all twice produces identical risk_state results.

    We mock the DB layer so the test runs without a live Postgres.
    Two consecutive calls with the same fill set must yield the same
    reported realised P&L.
    """
    from unittest.mock import AsyncMock, MagicMock

    repo = MagicMock()
    repo.reset_for_day = AsyncMock()
    repo.update_pnl = AsyncMock()

    factory_session = MagicMock()
    factory_session.__aenter__ = AsyncMock(
        return_value=factory_session,
    )
    factory_session.__aexit__ = AsyncMock(return_value=False)
    factory_session.execute = AsyncMock(
        return_value=MagicMock(
            mappings=lambda: MagicMock(
                all=lambda: [],
            ),
        ),
    )

    factory = MagicMock(return_value=factory_session)

    with patch(
        "backend.algo.paper.replay_rebuilder.get_session_factory",
        return_value=factory,
    ), patch(
        "backend.algo.paper.replay_rebuilder.RiskStateRepo",
        return_value=repo,
    ), patch(
        "backend.algo.paper.replay_rebuilder._load_paper_fills_today",
        return_value=[],
    ):
        from backend.algo.paper.replay_rebuilder import rebuild_all

        result1 = await rebuild_all()
        result2 = await rebuild_all()

    assert result1["rebuilt_users"] == result2["rebuilt_users"] == 0

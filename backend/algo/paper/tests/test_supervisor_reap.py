"""Task 6.5: PaperSupervisor reap + status tests.

Tests cover:
1. COMPLETED REAP + RE-ARM: completed run can be re-armed.
2. CRASHED REPORTS failed: list_active returns status="failed"
   before reaping, then empty after.
3. CANCELLED: stop_run returns True and entry is gone.
4. RE-ARM AFTER CRASH: completed crash allows a second start_run.
5. ACTIVE BLOCKS: still-running run raises RuntimeError on second
   start_run.
6. REPLAY REBUILD: exc_info logged on per-user failure, loop
   continues.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from backend.algo.paper.supervisor import PaperSupervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_strategy(sid: UUID | None = None) -> Any:
    """Return a minimal Strategy-like namespace."""
    s = MagicMock()
    s.id = sid or uuid4()
    s.name = "test_strategy"
    return s


def _fake_source() -> Any:
    return MagicMock()


async def _completed_run(source: Any) -> int:  # noqa: ARG001
    """Fake runtime.run() that returns immediately (completed)."""
    return 0


async def _crashing_run(source: Any) -> int:  # noqa: ARG001
    """Fake runtime.run() that raises immediately (failed)."""
    raise RuntimeError("strategy exploded")


async def _sleeping_run(source: Any) -> int:  # noqa: ARG001
    """Fake runtime.run() that sleeps forever (active)."""
    await asyncio.sleep(3600)
    return 0


def _make_fake_runtime_cls(run_coro_fn):
    """Return a PaperRuntime-like class whose run() delegates to
    run_coro_fn.  Used to monkeypatch
    ``backend.algo.paper.supervisor.PaperRuntime``.
    """
    class FakeRuntime:
        def __init__(self, **kwargs):  # noqa: ANN001
            pass

        async def run(self, source: Any) -> int:
            return await run_coro_fn(source)

    return FakeRuntime


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completed_reap_and_rearm():
    """A completed run is reaped so the same (user, strategy) can
    re-arm.
    """
    sv = PaperSupervisor()
    uid = uuid4()
    strategy = _fake_strategy()

    FakeRuntime = _make_fake_runtime_cls(_completed_run)
    with patch(
        "backend.algo.paper.supervisor.PaperRuntime", FakeRuntime,
    ):
        await sv.start_run(
            user_id=uid,
            strategy=strategy,
            source=_fake_source(),
            initial_capital_inr=Decimal("100000"),
        )

    key = (uid, strategy.id)
    task: asyncio.Task = sv._runs[key]["task"]
    # Wait for the task to finish
    await asyncio.sleep(0)  # yield to let the task complete
    await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

    # Re-arm: should NOT raise — the task is done
    with patch(
        "backend.algo.paper.supervisor.PaperRuntime", FakeRuntime,
    ):
        row = await sv.start_run(
            user_id=uid,
            strategy=strategy,
            source=_fake_source(),
            initial_capital_inr=Decimal("100000"),
        )

    assert row["status"] == "running"


@pytest.mark.asyncio
async def test_crashed_reports_failed_then_reaped():
    """A crashed run surfaces status='failed' in list_active, then is
    reaped on the following call.
    """
    sv = PaperSupervisor()
    uid = uuid4()
    strategy = _fake_strategy()

    FakeRuntime = _make_fake_runtime_cls(_crashing_run)
    with patch(
        "backend.algo.paper.supervisor.PaperRuntime", FakeRuntime,
    ):
        await sv.start_run(
            user_id=uid,
            strategy=strategy,
            source=_fake_source(),
            initial_capital_inr=Decimal("100000"),
        )

    key = (uid, strategy.id)
    task: asyncio.Task = sv._runs[key]["task"]
    # Wait for the task to finish (crash)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
    except (RuntimeError, asyncio.TimeoutError):
        pass

    # First list_active: should report the failed run
    # (report-then-reap order)
    rows = sv.list_active(user_id=uid)
    assert len(rows) == 1, (
        f"Expected 1 row before reap, got {len(rows)}: {rows}"
    )
    assert rows[0]["status"] == "failed", (
        f"Expected 'failed', got {rows[0]['status']!r}"
    )

    # Second list_active: reaped — no rows
    rows2 = sv.list_active(user_id=uid)
    assert rows2 == [], f"Expected empty after reap, got {rows2}"


@pytest.mark.asyncio
async def test_cancelled_stop_run_returns_true():
    """stop_run cancels and removes the entry; returns True."""
    sv = PaperSupervisor()
    uid = uuid4()
    strategy = _fake_strategy()

    FakeRuntime = _make_fake_runtime_cls(_sleeping_run)
    with patch(
        "backend.algo.paper.supervisor.PaperRuntime", FakeRuntime,
    ):
        await sv.start_run(
            user_id=uid,
            strategy=strategy,
            source=_fake_source(),
            initial_capital_inr=Decimal("100000"),
        )

    stopped = await sv.stop_run(
        user_id=uid, strategy_id=strategy.id,
    )
    assert stopped is True
    assert (uid, strategy.id) not in sv._runs


@pytest.mark.asyncio
async def test_rearm_after_crash():
    """A second start_run after a crash does NOT raise RuntimeError."""
    sv = PaperSupervisor()
    uid = uuid4()
    strategy = _fake_strategy()

    FakeRuntime = _make_fake_runtime_cls(_crashing_run)
    with patch(
        "backend.algo.paper.supervisor.PaperRuntime", FakeRuntime,
    ):
        await sv.start_run(
            user_id=uid,
            strategy=strategy,
            source=_fake_source(),
            initial_capital_inr=Decimal("100000"),
        )

    key = (uid, strategy.id)
    task: asyncio.Task = sv._runs[key]["task"]
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
    except (RuntimeError, asyncio.TimeoutError):
        pass

    # Re-arm after crash: should NOT raise
    FakeCompletedRuntime = _make_fake_runtime_cls(_completed_run)
    with patch(
        "backend.algo.paper.supervisor.PaperRuntime",
        FakeCompletedRuntime,
    ):
        row = await sv.start_run(
            user_id=uid,
            strategy=strategy,
            source=_fake_source(),
            initial_capital_inr=Decimal("100000"),
        )

    assert row["strategy_id"] == str(strategy.id)


@pytest.mark.asyncio
async def test_active_run_blocks_second_start():
    """A still-active run causes a second start_run to raise
    RuntimeError.
    """
    sv = PaperSupervisor()
    uid = uuid4()
    strategy = _fake_strategy()

    FakeRuntime = _make_fake_runtime_cls(_sleeping_run)
    with patch(
        "backend.algo.paper.supervisor.PaperRuntime", FakeRuntime,
    ):
        await sv.start_run(
            user_id=uid,
            strategy=strategy,
            source=_fake_source(),
            initial_capital_inr=Decimal("100000"),
        )

        with pytest.raises(RuntimeError, match="already active"):
            await sv.start_run(
                user_id=uid,
                strategy=strategy,
                source=_fake_source(),
                initial_capital_inr=Decimal("100000"),
            )

    # Clean up the sleeping task
    await sv.stop_run(user_id=uid, strategy_id=strategy.id)


@pytest.mark.asyncio
async def test_replay_rebuild_exc_info_logged_and_loop_continues(
    caplog,
):
    """rebuild_all: per-user failure logs exc_info; loop continues."""
    from backend.algo.paper import replay_rebuilder

    good_uid = uuid4()
    bad_uid = uuid4()

    async def _fake_rebuild(
        session, *, user_id: UUID,
    ) -> dict:
        if user_id == bad_uid:
            raise RuntimeError("db exploded")
        return {
            "user_id": str(user_id),
            "fills_replayed": 0,
            "realised_pnl_inr": "0",
            "day_date": "2026-06-27",
        }

    # Patch the session factory and the user-discovery query so we
    # can control which user IDs are returned.
    fake_session = AsyncMock()
    fake_session.commit = AsyncMock()

    async def _fake_execute(query, params=None):
        result = MagicMock()
        result.mappings.return_value.all.return_value = [
            {"user_id": str(good_uid)},
            {"user_id": str(bad_uid)},
        ]
        return result

    fake_session.execute = _fake_execute

    fake_cm = MagicMock()
    fake_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_cm.__aexit__ = AsyncMock(return_value=False)

    fake_factory = MagicMock(return_value=fake_cm)

    with (
        patch.object(
            replay_rebuilder,
            "get_session_factory",
            return_value=fake_factory,
        ),
        patch.object(
            replay_rebuilder,
            "rebuild_risk_state_for_user",
            side_effect=_fake_rebuild,
        ),
        caplog.at_level(
            logging.WARNING,
            logger="backend.algo.paper.replay_rebuilder",
        ),
    ):
        result = await replay_rebuilder.rebuild_all()

    # The good user was rebuilt; count == 1
    assert result["rebuilt_users"] == 1, (
        f"Expected 1 rebuilt user, got {result}"
    )

    # The bad user's exception must have been logged with exc_info
    exc_records = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and str(bad_uid) in r.getMessage()
    ]
    assert exc_records, (
        "Expected a WARNING/ERROR log for the failing user"
    )
    assert any(r.exc_info is not None for r in exc_records), (
        "Expected exc_info to be set on the failure log record"
    )

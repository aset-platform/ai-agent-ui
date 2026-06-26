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


@pytest.mark.asyncio
async def test_done_callback_does_not_clobber_reamed_run():
    """Regression: stale done-callback from run A must NOT write
    terminal_status onto a freshly re-armed run B for the same
    (user, strategy) key.

    Sequence exercised:
    1. Run A (crashing) is started and awaited to completion so the
       task is done.  At this point the callback *may* fire.
    2. We ensure the callback has executed by yielding once.
    3. We re-arm run B (a long sleeper) for the same (user, strategy).
    4. We grab the _on_done callable that was registered on task A
       and call it directly, simulating the race where the callback
       fires AFTER B is already installed.
    5. Assert that B's entry has NO terminal_status and that
       _public_row reports status="running".
    """
    sv = PaperSupervisor()
    uid = uuid4()
    strategy = _fake_strategy()
    key = (uid, strategy.id)

    # ---- arm run A (crashing) ----
    FakeCrash = _make_fake_runtime_cls(_crashing_run)
    with patch(
        "backend.algo.paper.supervisor.PaperRuntime", FakeCrash,
    ):
        await sv.start_run(
            user_id=uid,
            strategy=strategy,
            source=_fake_source(),
            initial_capital_inr=Decimal("100000"),
        )

    task_a: asyncio.Task = sv._runs[key]["task"]

    # Capture A's done-callback BEFORE it fires.
    # asyncio stores callbacks in task._callbacks; we capture them now.
    # We have to prevent them from running automatically so we can
    # invoke the captured callable manually after B is installed.
    captured_callbacks: list = []

    # Patch: remove the callbacks asyncio already registered so they
    # don't fire on the next yield, then manually invoke them later.
    # task._callbacks is a list of (fn, ctx) pairs on CPython.
    for cb, _ctx in list(getattr(task_a, "_callbacks", [])):
        captured_callbacks.append(cb)

    # Clear the auto-callbacks so they don't fire on their own.
    if hasattr(task_a, "_callbacks"):
        task_a._callbacks.clear()

    # Wait for task_a to finish (raises internally, so shield).
    try:
        await asyncio.wait_for(asyncio.shield(task_a), timeout=1.0)
    except (RuntimeError, asyncio.TimeoutError):
        pass

    # Yield to let any already-scheduled callbacks drain (there
    # should be none now that we cleared _callbacks).
    await asyncio.sleep(0)

    # ---- re-arm run B (sleeping, will never finish during test) ----
    FakeSleep = _make_fake_runtime_cls(_sleeping_run)
    with patch(
        "backend.algo.paper.supervisor.PaperRuntime", FakeSleep,
    ):
        row_b = await sv.start_run(
            user_id=uid,
            strategy=strategy,
            source=_fake_source(),
            initial_capital_inr=Decimal("100000"),
        )

    task_b: asyncio.Task = sv._runs[key]["task"]
    assert task_b is not task_a, "B must be a new task"
    assert row_b["status"] == "running"

    # ---- now manually fire A's stale done-callback ----
    # On the buggy code this writes "failed" onto B's entry.
    for cb in captured_callbacks:
        cb(task_a)

    # ---- assertions ----
    entry_b = sv._runs.get(key)
    assert entry_b is not None, "B's entry should still exist"

    # The critical assertion: stale callback must NOT have planted
    # terminal_status on B's entry.
    assert "terminal_status" not in entry_b, (
        f"Stale done-callback from A clobbered B's entry: "
        f"terminal_status={entry_b.get('terminal_status')!r}"
    )

    # _public_row must still see B as running.
    pub = PaperSupervisor._public_row(entry_b)
    assert pub["status"] == "running", (
        f"Expected 'running', got {pub['status']!r} — "
        "stale callback corrupted B's status"
    )

    # Clean up B
    await sv.stop_run(user_id=uid, strategy_id=strategy.id)

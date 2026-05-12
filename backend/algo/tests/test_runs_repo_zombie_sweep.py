"""Tests for BacktestRunsRepo.mark_stale_running_as_crashed.

ASETPLTFRM-379 — verifies the zombie-runs sweep correctly
distinguishes stale 'running' rows from fresh ones and from
already-terminal rows.

Uses a stub session (same pattern as test_backtest_runs_repo.py)
to sidestep the real-PG event-loop coupling and the FK chain on
algo.strategies / auth.users.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from backend.algo.backtest.runs_repo import BacktestRunsRepo


UTC = timezone.utc


class _StubSession:
    """In-memory algo.runs stand-in for the zombie-sweep SQL.

    Only handles the UPDATE … RETURNING id query the sweep emits.
    Returns a result whose iterator yields one-tuple rows so the
    caller's ``[row[0] for row in result]`` works.
    """

    def __init__(self, rows: list[dict]) -> None:
        # Caller seeds rows directly; this stub doesn't simulate
        # arbitrary inserts.
        self.rows = rows
        self.commit_count = 0

    async def execute(self, q, params=None):  # noqa: ANN001
        sql = str(q)
        params = dict(params or {})

        class _Res:
            def __init__(self, items):
                self._items = items

            def __iter__(self):
                return iter(self._items)

            def mappings(self):
                # Materialise tuples → mappings for tests that
                # call .mappings().first(). Each item is a dict.
                class _M:
                    def __init__(self, items):
                        self._items = items

                    def first(self):
                        return (
                            self._items[0]
                            if self._items else None
                        )
                return _M(self._items)

        if "UPDATE algo.runs SET" in sql and "crashed" in sql:
            thr = params.get("thr", 3600)
            cutoff = datetime.now(UTC) - timedelta(seconds=thr)
            swept = []
            for row in self.rows:
                if (
                    row["status"] == "running"
                    and row.get("completed_at") is None
                    and row["started_at"] < cutoff
                ):
                    row["status"] = "crashed"
                    row["completed_at"] = datetime.now(UTC)
                    swept.append((row["id"],))
            return _Res(swept)
        return _Res([])

    async def commit(self):
        self.commit_count += 1


@pytest.mark.asyncio
async def test_recent_running_run_is_not_marked_crashed() -> None:
    """A run that started moments ago is presumed alive."""
    repo = BacktestRunsRepo()
    rid = uuid4()
    session = _StubSession([
        {
            "id": rid,
            "status": "running",
            "started_at": datetime.now(UTC) - timedelta(seconds=30),
            "completed_at": None,
        },
    ])
    ids = await repo.mark_stale_running_as_crashed(
        session, threshold_seconds=3600,
    )
    assert ids == []
    # No transition happened — status untouched.
    assert session.rows[0]["status"] == "running"
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_stale_running_run_is_marked_crashed() -> None:
    """A run > threshold ago with status='running' AND no
    completed_at must be swept."""
    repo = BacktestRunsRepo()
    rid = uuid4()
    session = _StubSession([
        {
            "id": rid,
            "status": "running",
            "started_at": datetime.now(UTC) - timedelta(hours=2),
            "completed_at": None,
        },
    ])
    ids = await repo.mark_stale_running_as_crashed(
        session, threshold_seconds=3600,
    )
    assert ids == [rid]
    assert session.rows[0]["status"] == "crashed"
    assert session.rows[0]["completed_at"] is not None
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_old_completed_run_is_untouched() -> None:
    """A run that's old AND already terminal must not be touched."""
    repo = BacktestRunsRepo()
    rid = uuid4()
    old = datetime.now(UTC) - timedelta(hours=2)
    session = _StubSession([
        {
            "id": rid,
            "status": "completed",
            "started_at": old,
            "completed_at": old + timedelta(minutes=1),
        },
    ])
    ids = await repo.mark_stale_running_as_crashed(
        session, threshold_seconds=3600,
    )
    assert ids == []
    assert session.rows[0]["status"] == "completed"
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_mixed_rows_only_stale_running_swept() -> None:
    """Multi-row fixture: only the stale running row gets
    swept; the fresh running + old completed stay put."""
    repo = BacktestRunsRepo()
    rids = [uuid4() for _ in range(3)]
    session = _StubSession([
        {  # stale running → should be swept
            "id": rids[0],
            "status": "running",
            "started_at": datetime.now(UTC) - timedelta(hours=2),
            "completed_at": None,
        },
        {  # fresh running → untouched
            "id": rids[1],
            "status": "running",
            "started_at": datetime.now(UTC) - timedelta(seconds=60),
            "completed_at": None,
        },
        {  # old completed → untouched
            "id": rids[2],
            "status": "completed",
            "started_at": datetime.now(UTC) - timedelta(hours=3),
            "completed_at": datetime.now(UTC) - timedelta(hours=2),
        },
    ])
    ids = await repo.mark_stale_running_as_crashed(
        session, threshold_seconds=3600,
    )
    assert ids == [rids[0]]
    assert session.rows[0]["status"] == "crashed"
    assert session.rows[1]["status"] == "running"
    assert session.rows[2]["status"] == "completed"

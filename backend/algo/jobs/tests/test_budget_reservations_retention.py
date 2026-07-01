"""Tests for the algo.budget_reservations weekly retention job.

The load-bearing guarantees:
  1. Paper / dryrun rows older than grace are deleted.
  2. Live terminal non-filled rows older than grace are deleted.
  3. Live FILLED rows are NEVER deleted (required by
     sum_open_position_cost for open-position accounting).
  4. Active live rows (PENDING / SUBMITTED / PARTIAL) are NEVER
     deleted regardless of age.
  5. dry_run=True returns counts without touching the DB.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.jobs.budget_reservations_retention import (
    _LIVE_TERMINAL_GRACE_DAYS,
    _NON_LIVE_GRACE_DAYS,
    _NON_LIVE_MODES,
    _PURGEABLE_LIVE_STATES,
    run_budget_reservations_retention_job,
)


# ── helpers ──────────────────────────────────────────────────────

def _ts(*, days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _make_row(
    *,
    mode: str,
    state: str,
    days_ago: int,
) -> dict[str, Any]:
    return {
        "reservation_id": str(uuid4()),
        "mode": mode,
        "state": state,
        "transitioned_at": _ts(days_ago=days_ago),
        "reserved_inr": Decimal("1000"),
    }


# ── constant correctness ──────────────────────────────────────────

class TestConstants:
    def test_non_live_modes_contains_paper_and_dryrun(self) -> None:
        assert "paper" in _NON_LIVE_MODES
        assert "dryrun" in _NON_LIVE_MODES

    def test_purgeable_states_does_not_contain_filled(self) -> None:
        assert "FILLED" not in _PURGEABLE_LIVE_STATES

    def test_purgeable_states_contains_timeout_and_cancelled(
        self,
    ) -> None:
        for s in ("TIMEOUT", "CANCELLED", "REJECTED",
                  "PARTIAL_CANCELLED"):
            assert s in _PURGEABLE_LIVE_STATES


# ── dry_run path ──────────────────────────────────────────────────

def _make_session_shim(
    rowcount: int = 5,
    scalar_val: int = 3,
) -> tuple[Any, list[str]]:
    """Return (asynccontextmanager factory, executed_sqls list)."""
    executed_sqls: list[str] = []
    commit_calls: list[int] = []

    async def _fake_execute(stmt, params=None):
        executed_sqls.append(str(stmt))
        m = MagicMock()
        m.rowcount = rowcount
        m.scalar = MagicMock(return_value=scalar_val)
        return m

    async def _fake_commit():
        commit_calls.append(1)

    mock_session = AsyncMock()
    mock_session.execute = _fake_execute
    mock_session.commit = _fake_commit

    @asynccontextmanager
    async def _disposable():
        yield mock_session

    return _disposable, executed_sqls, commit_calls


class TestDryRun:
    def test_dry_run_returns_status_and_no_delete_called(
        self,
    ) -> None:
        shim, sqls, commits = _make_session_shim(scalar_val=3)

        with patch(
            "backend.db.engine.disposable_pg_session",
            new=shim,
        ):
            result = run_budget_reservations_retention_job(
                {"dry_run": True}
            )

        assert result["status"] == "dry_run"
        assert "would_delete_non_live" in result
        assert "would_delete_live_terminal" in result
        # commit must NOT be called in dry_run (only SELECTs)
        assert not any(s for s in sqls if "DELETE" in s.upper())


# ── live execution ────────────────────────────────────────────────

class TestLiveExecution:
    """Mock the DB layer and assert correct SQL is issued."""

    def _run_with_mock(
        self,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str], list[int]]:
        shim, sqls, commits = _make_session_shim(rowcount=5)
        with patch("backend.db.engine.disposable_pg_session", new=shim):
            result = run_budget_reservations_retention_job(payload)
        return result, sqls, commits

    def test_returns_ok_status(self) -> None:
        result, _, _ = self._run_with_mock()
        assert result["status"] == "ok"

    def test_two_delete_passes_issued(self) -> None:
        _, sqls, _ = self._run_with_mock()
        deletes = [s for s in sqls if "DELETE" in s.upper()]
        assert len(deletes) == 2

    def test_pass1_targets_non_live_modes(self) -> None:
        _, sqls, _ = self._run_with_mock()
        pass1 = sqls[0]
        assert "metadata->>'mode'" in pass1
        assert "paper" in pass1
        assert "dryrun" in pass1
        assert "TIMEOUT" not in pass1

    def test_pass2_targets_live_terminal_states(self) -> None:
        _, sqls, _ = self._run_with_mock()
        pass2 = sqls[1]
        assert "TIMEOUT" in pass2
        assert "CANCELLED" in pass2
        assert "REJECTED" in pass2
        assert "FILLED" not in pass2

    def test_pass2_scoped_to_live_mode(self) -> None:
        _, sqls, _ = self._run_with_mock()
        pass2 = sqls[1]
        assert "'live'" in pass2

    def test_commit_called_once(self) -> None:
        _, _, commits = self._run_with_mock()
        assert len(commits) == 1

    def test_deleted_counts_in_result(self) -> None:
        result, _, _ = self._run_with_mock()
        assert result["deleted_non_live"] == 5
        assert result["deleted_live_terminal"] == 5

    def test_custom_grace_days_propagated(self) -> None:
        result, _, _ = self._run_with_mock(
            {"non_live_grace_days": 2, "live_terminal_grace_days": 14}
        )
        assert result["status"] == "ok"
        non_live_cut = datetime.fromisoformat(result["non_live_cut"])
        live_cut = datetime.fromisoformat(result["live_terminal_cut"])
        now = datetime.now(timezone.utc)
        assert abs((now - non_live_cut).days - 2) <= 1
        assert abs((now - live_cut).days - 14) <= 1

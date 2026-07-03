"""Tests for the algo_budget_reservations_retention weekly job."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_wrapper_marks_scheduler_run_success():
    """Found 2026-07-02, same bug as _job_algo_closed_trades_rollup:
    the executor.py wrapper never called the shared _algo_job_success
    helper, so a fast, successful run (the job itself completes in
    well under a second -- two bounded Postgres DELETEs) left the
    scheduler_runs row stuck at status='running' forever, looking
    like a hang."""
    from backend.jobs.executor import (
        _job_algo_budget_reservations_retention,
    )

    run_id = "test-run-id"
    repo = MagicMock()
    with patch(
        "backend.algo.jobs.budget_reservations_retention"
        ".disposable_pg_session",
    ) as mock_session_cm:
        session = MagicMock()

        async def _execute(*args, **kwargs):
            result = MagicMock()
            result.rowcount = 0
            return result

        session.execute = _execute

        async def _commit():
            return None

        session.commit = _commit

        class _CM:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *a):
                return None

        mock_session_cm.return_value = _CM()
        result = _job_algo_budget_reservations_retention(
            scope="all", run_id=run_id, repo=repo, payload={},
        )
    assert result["status"] == "ok"
    repo.update_scheduler_run.assert_called_once()
    call_args = repo.update_scheduler_run.call_args
    assert call_args.args[0] == run_id
    updates = call_args.args[1]
    assert updates["status"] == "success"
    assert "completed_at" in updates

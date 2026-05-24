"""Step-0 refactor: scoped maintenance defers to
verify_or_backup() instead of looping backup_table()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def _fake_repo():
    repo = MagicMock()
    repo.get_scheduler_run.return_value = {"status": "running"}
    return repo


def test_step0_uses_verify_or_backup_when_scoped(_fake_repo):
    """Scoped runs delegate to verify_or_backup, NOT
    backup_table directly."""
    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["iceberg_maintenance"]
    payload = {"tables": ["stocks.ohlcv"]}

    with patch(
        "backend.maintenance.backup.verify_or_backup",
        return_value={
            "mode": "verified",
            "snapshot": "/tmp/backup-2026-05-23",
            "paths": [],
        },
    ) as vob, patch(
        "backend.maintenance.backup.backup_table"
    ) as bt, patch(
        "backend.maintenance.iceberg_maintenance.compact_table",
        return_value={"before": 0, "after": 0, "rows": 0, "elapsed_s": 0.0},
    ), patch(
        "backend.maintenance.iceberg_maintenance.cleanup_orphans_v2",
        return_value={"deleted": 0, "elapsed_s": 0.0},
    ):
        fn(
            "india",
            "run-1",
            _fake_repo,
            cancel_event=None,
            payload=payload,
        )
    vob.assert_called_once()
    args, _ = vob.call_args
    assert list(args[0]) == ["stocks.ohlcv"]
    bt.assert_not_called()


def test_step0_full_warehouse_path_unchanged_when_unscoped(
    _fake_repo,
):
    """Empty payload still triggers run_backup()."""
    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["iceberg_maintenance"]

    with patch(
        "backend.maintenance.backup.run_backup",
        return_value="/tmp/backup-2026-05-23",
    ) as rb, patch(
        "backend.maintenance.backup.verify_or_backup"
    ) as vob, patch(
        "backend.maintenance.iceberg_maintenance.compact_table",
        return_value={"before": 0, "after": 0, "rows": 0, "elapsed_s": 0.0},
    ), patch(
        "backend.maintenance.iceberg_maintenance.cleanup_orphans_v2",
        return_value={"deleted": 0, "elapsed_s": 0.0},
    ):
        fn(
            "india",
            "run-2",
            _fake_repo,
            cancel_event=None,
            payload={},
        )
    rb.assert_called_once()
    vob.assert_not_called()

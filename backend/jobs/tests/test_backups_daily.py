"""Integration test for the backups_daily scheduler job."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.jobs.executor import JOB_EXECUTORS
from backend.maintenance.backup_manifest import (
    MANIFEST_FILENAME,
    read_manifest,
)


def test_backups_daily_writes_manifest(tmp_path, monkeypatch):
    """The job rsyncs the warehouse and writes manifest.json."""
    # Arrange — fake warehouse with two tables
    warehouse = tmp_path / "warehouse"
    (warehouse / "stocks" / "ohlcv" / "data" / "p=1").mkdir(
        parents=True,
    )
    (
        warehouse / "stocks" / "ohlcv" / "data" / "p=1" / "a.parquet"
    ).write_bytes(b"x" * 2048)
    (warehouse / "algo" / "events" / "data" / "p=2").mkdir(
        parents=True,
    )
    (warehouse / "algo" / "events" / "data" / "p=2" / "b.parquet").write_bytes(
        b"x" * 1024
    )
    (tmp_path / "catalog.db").write_bytes(b"x" * 512)

    backup_root = tmp_path / "backups"

    monkeypatch.setattr(
        "backend.maintenance.backup.WAREHOUSE_DIR",
        str(warehouse),
    )
    monkeypatch.setattr(
        "backend.maintenance.backup.BACKUP_ROOT",
        str(backup_root),
    )

    repo = MagicMock()
    fn = JOB_EXECUTORS["backups_daily"]

    # Act
    fn(repo=repo, run_id="run-1", payload={}, cancel_event=None)

    # Assert — exactly one full snapshot
    snapshots = list(backup_root.glob("backup-*"))
    assert len(snapshots) == 1
    manifest = read_manifest(snapshots[0])
    assert manifest is not None
    ids = sorted(t["id"] for t in manifest["tables"])
    assert ids == ["algo.events", "stocks.ohlcv"]
    assert manifest["catalog_present"] is True
    assert manifest["created_by"] == "backups_daily"
    # rsync_duration_s is a real measurement, just ensure it's >= 0
    assert manifest["rsync_duration_s"] >= 0


def test_backups_daily_fails_closed_on_rsync_error(
    tmp_path,
    monkeypatch,
):
    """If run_backup raises, the manifest is NOT written."""
    backup_root = tmp_path / "backups"
    monkeypatch.setattr(
        "backend.maintenance.backup.WAREHOUSE_DIR",
        str(tmp_path / "missing-warehouse"),
    )
    monkeypatch.setattr(
        "backend.maintenance.backup.BACKUP_ROOT",
        str(backup_root),
    )

    repo = MagicMock()
    fn = JOB_EXECUTORS["backups_daily"]

    # rsync against a nonexistent source returns rc=23
    with patch("backend.maintenance.backup.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 23
        mock_run.return_value.stderr = "rsync: link_stat failed"
        with pytest.raises(RuntimeError, match="Backup failed"):
            fn(
                repo=repo,
                run_id="run-2",
                payload={},
                cancel_event=None,
            )
    # No manifest left behind
    assert list(backup_root.rglob(MANIFEST_FILENAME)) == []

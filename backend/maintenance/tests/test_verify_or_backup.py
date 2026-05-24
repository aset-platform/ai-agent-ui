"""Tests for verify_or_backup()."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.maintenance.backup_manifest import (
    SCHEMA_VERSION,
    write_manifest,
)


def _today_root(backup_root: Path) -> Path:
    today = datetime.now(timezone.utc).date().isoformat()
    return backup_root / f"backup-{today}"


def _seed_manifest(
    backup_root: Path,
    *,
    table_ids: list[str],
    age_hours: float = 0.5,
):
    root = _today_root(backup_root)
    root.mkdir(parents=True, exist_ok=True)
    created = (
        datetime.now(timezone.utc)
        - timedelta(hours=age_hours)
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": root.name.replace("backup-", ""),
        "created_at": created.isoformat().replace(
            "+00:00", "Z",
        ),
        "created_by": "test",
        "warehouse_size_mb": 100.0,
        "catalog_present": True,
        "tables": [
            {"id": tid, "size_mb": 1.0} for tid in table_ids
        ],
    }
    write_manifest(root, manifest)
    return root


def test_verify_returns_verified_when_fresh_and_covers(
    tmp_path,
):
    from backend.maintenance.backup import verify_or_backup

    _seed_manifest(
        tmp_path,
        table_ids=["stocks.ohlcv", "stocks.dividends"],
        age_hours=0.5,
    )
    with patch(
        "backend.maintenance.backup.backup_table"
    ) as bt:
        result = verify_or_backup(
            ["stocks.ohlcv"],
            backup_root=str(tmp_path),
        )
    assert result["mode"] == "verified"
    assert result["snapshot"] == str(_today_root(tmp_path))
    bt.assert_not_called()


def test_verify_falls_back_when_no_manifest(tmp_path):
    from backend.maintenance.backup import verify_or_backup

    with patch(
        "backend.maintenance.backup.backup_table"
    ) as bt:
        bt.return_value = "/tmp/fake"
        result = verify_or_backup(
            ["stocks.ohlcv"],
            backup_root=str(tmp_path),
        )
    assert result["mode"] == "fallback_per_table"
    assert bt.call_count == 1
    assert result["paths"] == ["/tmp/fake"]


def test_verify_falls_back_when_stale(tmp_path):
    from backend.maintenance.backup import verify_or_backup

    _seed_manifest(
        tmp_path,
        table_ids=["stocks.ohlcv"],
        age_hours=30,  # > 24h default
    )
    with patch(
        "backend.maintenance.backup.backup_table"
    ) as bt:
        bt.return_value = "/tmp/stale-fallback"
        result = verify_or_backup(
            ["stocks.ohlcv"],
            backup_root=str(tmp_path),
        )
    assert result["mode"] == "fallback_per_table"
    bt.assert_called_once_with("stocks.ohlcv")


def test_verify_falls_back_when_table_missing_from_manifest(
    tmp_path,
):
    from backend.maintenance.backup import verify_or_backup

    _seed_manifest(
        tmp_path,
        table_ids=["stocks.ohlcv"],  # missing dividends
    )
    with patch(
        "backend.maintenance.backup.backup_table"
    ) as bt:
        bt.return_value = "/tmp/x"
        result = verify_or_backup(
            ["stocks.ohlcv", "stocks.dividends"],
            backup_root=str(tmp_path),
        )
    assert result["mode"] == "fallback_per_table"
    assert bt.call_count == 2


def test_verify_swallows_filenotfound_in_fallback(tmp_path):
    """Per-table fallback handles "table never written"
    the same way the existing scoped-maintenance path does
    — log + continue."""
    from backend.maintenance.backup import verify_or_backup

    def _bt(t):
        if t == "stocks.never_written":
            raise FileNotFoundError("no data")
        return f"/tmp/{t}"

    with patch(
        "backend.maintenance.backup.backup_table",
        side_effect=_bt,
    ):
        result = verify_or_backup(
            ["stocks.ohlcv", "stocks.never_written"],
            backup_root=str(tmp_path),
        )
    assert result["mode"] == "fallback_per_table"
    assert result["paths"] == ["/tmp/stocks.ohlcv"]

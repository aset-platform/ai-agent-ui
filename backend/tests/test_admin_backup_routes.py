"""Tests for admin backup endpoint helpers.

These tests cover the manifest-driven behaviour of the three
``_admin_backups_*_impl`` helpers lifted out of
``backend.routes.create_app`` in Task 5 of the backup-redesign
epic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.maintenance.backup_manifest import (
    SCHEMA_VERSION,
    write_manifest,
)
from backend.routes import (
    _admin_backup_contents_impl,
    _admin_backups_health_impl,
    _admin_backups_list_impl,
    _is_full_snapshot_dir_name,
)


def _seed_full_snapshot(
    backup_root: Path,
    date_str: str,
    *,
    tables: list[tuple[str, float]],
    catalog: bool = True,
):
    root = backup_root / f"backup-{date_str}"
    (root / "warehouse").mkdir(parents=True, exist_ok=True)
    if catalog:
        (root / "catalog.db").write_bytes(b"x" * 1024)
    total = sum(s for _, s in tables)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": date_str,
        "created_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "created_by": "backups_daily",
        "warehouse_size_mb": total,
        "catalog_present": catalog,
        "tables": [
            {
                "id": tid,
                "namespace": tid.split(".")[0],
                "name": tid.split(".")[1],
                "size_mb": s,
                "partition_count": 1,
                "file_count": 1,
                "last_modified_ns": 0,
            }
            for tid, s in tables
        ],
    }
    write_manifest(root, manifest)
    return root


def _seed_per_table_dir(
    backup_root: Path,
    date_str: str,
    table_suffix: str,
):
    """Create a per-table fallback dir like
    ``backup-2026-05-22-stocks-ohlcv`` (no manifest)."""
    root = backup_root / f"backup-{date_str}-{table_suffix}"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.asyncio
async def test_admin_health_reports_warehouse_size_from_manifest(
    tmp_path,
):
    date_str = "2026-05-23"
    _seed_full_snapshot(
        tmp_path,
        date_str,
        tables=[
            ("stocks.ohlcv", 98.4),
            ("stocks.dividends", 1.2),
            ("stocks.company_info", 9.0),
        ],
    )

    response = await _admin_backups_health_impl(
        backup_root=str(tmp_path),
    )

    assert response["status"] == "healthy"
    assert response["warehouse_size_mb"] == pytest.approx(
        108.6, abs=0.5,
    )
    assert response["table_count"] == 3
    assert response["has_catalog"] is True
    assert response["latest_date"] == date_str


@pytest.mark.asyncio
async def test_admin_list_filters_per_table_dirs(tmp_path):
    """Per-table backup dirs MUST be filtered out of the list."""
    full_date = "2026-05-23"
    _seed_full_snapshot(
        tmp_path,
        full_date,
        tables=[("stocks.ohlcv", 50.0)],
    )
    # Legacy per-table cruft that should be hidden
    _seed_per_table_dir(tmp_path, "2026-05-22", "stocks-ohlcv")

    response = await _admin_backups_list_impl(
        backup_root=str(tmp_path),
    )
    dates = [b["date"] for b in response["backups"]]
    assert len(dates) == 1
    assert all(
        _is_full_snapshot_dir_name(d) for d in dates
    )
    assert dates == [full_date]


@pytest.mark.asyncio
async def test_admin_contents_reads_manifest_tables(tmp_path):
    date_str = "2026-05-23"
    _seed_full_snapshot(
        tmp_path,
        date_str,
        tables=[
            ("stocks.ohlcv", 50.0),
            ("stocks.dividends", 2.5),
        ],
    )

    response = await _admin_backup_contents_impl(
        date_str,
        backup_root=str(tmp_path),
    )

    assert response["date"] == date_str
    names = [t["name"] for t in response["tables"]]
    assert "stocks.ohlcv" in names
    assert "stocks.dividends" in names
    assert response["catalog_present"] is True

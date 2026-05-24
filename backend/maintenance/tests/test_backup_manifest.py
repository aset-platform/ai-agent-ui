"""Tests for backend.maintenance.backup_manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.maintenance.backup_manifest import (
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    build_manifest,
    read_manifest,
    write_manifest,
)


def _make_snapshot(
    tmp_path: Path,
    tables: dict[str, list[tuple[str, int]]],
    *,
    with_catalog: bool = True,
) -> Path:
    """Build a fixture snapshot.

    ``tables`` maps ``"<ns>.<name>"`` to a list of
    ``(partition_dir, file_bytes)`` tuples.  One parquet file is
    written per tuple so file_count and size_mb are predictable.
    """
    root = tmp_path / "backup-2026-05-23"
    wh = root / "warehouse"
    for table_id, parts in tables.items():
        ns, name = table_id.split(".", 1)
        data_root = wh / ns / name / "data"
        for part_dir, nbytes in parts:
            part = data_root / part_dir
            part.mkdir(parents=True, exist_ok=True)
            (part / "00000.parquet").write_bytes(
                b"x" * nbytes,
            )
    if with_catalog:
        (root / "catalog.db").write_bytes(b"x" * 1024)
    return root


def test_build_manifest_lists_all_tables(tmp_path):
    root = _make_snapshot(
        tmp_path,
        tables={
            "stocks.ohlcv": [
                ("date_month=2026-05", 2_000_000),
                ("date_month=2026-04", 1_500_000),
            ],
            "algo.events": [
                ("mode=paper", 500_000),
            ],
        },
    )
    m = build_manifest(
        root,
        created_by="test",
        rsync_duration_s=12,
    )
    assert m["schema_version"] == SCHEMA_VERSION
    ids = sorted(t["id"] for t in m["tables"])
    assert ids == ["algo.events", "stocks.ohlcv"]
    ohlcv = next(
        t for t in m["tables"] if t["id"] == "stocks.ohlcv"
    )
    assert ohlcv["partition_count"] == 2
    assert ohlcv["file_count"] == 2
    assert ohlcv["size_mb"] == pytest.approx(3.3, abs=0.2)
    assert m["catalog_present"] is True
    assert m["rsync_duration_s"] == 12
    assert m["created_by"] == "test"


def test_write_manifest_atomic_no_partial_file(tmp_path):
    root = tmp_path / "backup-2026-05-23"
    root.mkdir()
    m = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": "2026-05-23",
        "tables": [],
        "created_by": "test",
        "created_at": "2026-05-23T00:30:00Z",
        "warehouse_size_mb": 0.0,
        "catalog_present": False,
    }
    final = write_manifest(root, m)
    assert final.exists()
    # No tmp leftover
    assert not list(root.glob(".manifest-*.json"))


def test_read_manifest_returns_none_when_absent(tmp_path):
    assert read_manifest(tmp_path) is None


def test_read_manifest_returns_none_on_invalid_json(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text("not json")
    assert read_manifest(tmp_path) is None


def test_read_manifest_returns_none_on_wrong_schema(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text(
        json.dumps({"schema_version": 999, "tables": []}),
    )
    assert read_manifest(tmp_path) is None


def test_read_manifest_round_trip(tmp_path):
    root = _make_snapshot(
        tmp_path,
        tables={
            "stocks.ohlcv": [
                ("date_month=2026-05", 1000),
            ],
        },
    )
    m = build_manifest(
        root, created_by="rt", rsync_duration_s=5,
    )
    write_manifest(root, m)
    again = read_manifest(root)
    assert again is not None
    assert again["snapshot_id"] == "2026-05-23"
    assert len(again["tables"]) == 1

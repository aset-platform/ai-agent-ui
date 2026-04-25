"""Safety guards on ``cleanup_orphans_v2``.

Five properties are load-bearing (ASETPLTFRM-338):

1. Backup is fail-closed — if ``run_backup`` raises,
   neither expire_snapshots nor any unlink is attempted.
2. Files in the Iceberg-authoritative referenced set
   (``inspect.all_files()`` + ``inspect.all_manifests()``)
   are NEVER deleted.
3. The catalog's ``metadata_location`` pointer is
   protected by a hard exclusion + paranoid assertion.
4. Files newer than ``mtime_grace_minutes`` survive
   the sweep (concurrent-write race protection).
5. ``expire_snapshots`` is invoked with the right ID
   set (oldest snapshots; latest N retained).

Plus unit tests for the ``_normalize_uri`` helper that
the catalog-pointer assertion depends on.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.maintenance import iceberg_maintenance as im

# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------


@pytest.fixture
def tmp_table(monkeypatch, tmp_path):
    """Build a stub on-disk Iceberg table layout under
    a scratch warehouse and a stub catalog.db.

    Returns a dict with keys:
      table_name, table_dir, warehouse, catalog_db,
      data_file, manifest_file, current_metadata,
      old_metadata
    """
    table_name = "stocks.test_orphan"
    warehouse = tmp_path / "warehouse"
    table_dir = warehouse / "stocks" / "test_orphan"
    data_dir = table_dir / "data"
    metadata_dir = table_dir / "metadata"
    data_dir.mkdir(parents=True)
    metadata_dir.mkdir(parents=True)

    # 1 referenced data file + 1 referenced manifest +
    # 1 current metadata.json (catalog points here).
    data_file = data_dir / "live.parquet"
    data_file.write_bytes(b"live-rows")
    manifest_file = metadata_dir / "live.avro"
    manifest_file.write_bytes(b"manifest")
    current_metadata = metadata_dir / "00100-current.metadata.json"
    current_metadata.write_text("{}")
    old_metadata = metadata_dir / "00099-old.metadata.json"
    old_metadata.write_text("{}")

    # Catalog DB at the conventional spot.
    catalog_db = warehouse.parent / "catalog.db"
    catalog_db.parent.mkdir(parents=True, exist_ok=True)
    _seed_catalog_db(
        catalog_db,
        table_name,
        f"file://{current_metadata}",
    )

    # Age every file past the default grace window so
    # it's eligible for sweep.
    _age_all(table_dir, hours=2)

    monkeypatch.setattr(
        im,
        "WAREHOUSE_DIR",
        warehouse,
    )
    monkeypatch.setattr(
        im,
        "DEFAULT_CATALOG_DB",
        catalog_db,
    )

    return {
        "table_name": table_name,
        "table_dir": table_dir,
        "warehouse": warehouse,
        "catalog_db": catalog_db,
        "data_file": data_file,
        "manifest_file": manifest_file,
        "current_metadata": current_metadata,
        "old_metadata": old_metadata,
    }


def _seed_catalog_db(
    db_path: Path,
    table_name: str,
    location: str,
) -> None:
    """Create a minimal ``iceberg_tables`` row with the
    metadata_location pointer the sweep must protect.
    """
    import sqlite3

    ns, name = table_name.split(".", 1)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE iceberg_tables ("
            "table_namespace TEXT, "
            "table_name TEXT, "
            "metadata_location TEXT)"
        )
        conn.execute(
            "INSERT INTO iceberg_tables VALUES (?,?,?)",
            (ns, name, location),
        )
        conn.commit()
    finally:
        conn.close()


def _age_all(root: Path, hours: int) -> None:
    """Backdate every file under ``root`` so the mtime
    grace filter does not save it."""
    past = time.time() - hours * 3600
    for p in root.rglob("*"):
        if p.is_file():
            try:
                # Use os.utime via Path for portability
                import os

                os.utime(p, (past, past))
            except OSError:
                pass


def _build_mock_table(
    referenced_files: list[Path],
    referenced_manifests: list[Path],
    snapshot_ids: list[int],
    scan_raises: bool = False,
    snapshot_manifest_lists: list[Path] | None = None,
) -> MagicMock:
    """Construct a MagicMock that mimics the slice of
    PyIceberg ``Table`` API ``cleanup_orphans_v2`` uses.

    ``snapshot_manifest_lists`` mirrors ``snapshot_ids``
    one-to-one and is exposed via ``snapshot.manifest_list``
    on each mock snapshot — the per-snapshot ``snap-*.avro``
    that the read path opens first.
    """
    tbl = MagicMock()

    af = MagicMock()
    af.column.return_value.to_pylist.return_value = [
        f"file://{p}" for p in referenced_files
    ]
    tbl.inspect.all_files.return_value = af

    am = MagicMock()
    am.column.return_value.to_pylist.return_value = [
        f"file://{p}" for p in referenced_manifests
    ]
    tbl.inspect.all_manifests.return_value = am

    if snapshot_manifest_lists is None:
        snapshot_manifest_lists = [None] * len(
            snapshot_ids,
        )

    snaps = []
    for i, sid in enumerate(snapshot_ids):
        s = MagicMock()
        s.snapshot_id = sid
        # Older snapshots first (lower timestamp)
        s.timestamp_ms = (i + 1) * 1000
        ml = snapshot_manifest_lists[i]
        s.manifest_list = f"file://{ml}" if ml else None
        snaps.append(s)
    tbl.metadata.snapshots = snaps

    if scan_raises:
        tbl.scan.side_effect = RuntimeError(
            "table corrupted",
        )
    else:
        scan = MagicMock()
        scan.to_arrow.return_value.to_pylist.return_value = []
        tbl.scan.return_value = scan

    # expire_snapshots() chain returns chainable mocks
    es = MagicMock()
    es.by_ids.return_value.commit.return_value = None
    tbl.maintenance.expire_snapshots.return_value = es

    return tbl


# ---------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------


def test_normalize_uri_strips_file_scheme():
    out = im._normalize_uri(
        "file:///Users/abhay/x.json",
    )
    assert out.endswith("/Users/abhay/x.json")
    assert "file://" not in out


def test_normalize_uri_collapses_pyiceberg_quad_slash():
    """PyIceberg writes ``file:////abs/path`` with one
    extra slash. ``_normalize_uri`` must collapse it so
    the catalog pointer comparison hits.
    """
    quad = im._normalize_uri(
        "file:////Users/abhay/x.json",
    )
    triple = im._normalize_uri(
        "file:///Users/abhay/x.json",
    )
    plain = im._normalize_uri(
        "/Users/abhay/x.json",
    )
    assert quad == triple == plain


def test_normalize_uri_empty():
    assert im._normalize_uri("") == ""
    assert im._normalize_uri(None) == ""  # type: ignore[arg-type]


def test_read_catalog_metadata_location_returns_pointer(
    tmp_table,
):
    loc = im._read_catalog_metadata_location(
        tmp_table["table_name"],
        catalog_db=tmp_table["catalog_db"],
    )
    assert loc is not None
    assert "00100-current.metadata.json" in loc


def test_read_catalog_metadata_location_missing_db(
    tmp_path,
):
    out = im._read_catalog_metadata_location(
        "stocks.does_not_exist",
        catalog_db=tmp_path / "nope.db",
    )
    assert out is None


# ---------------------------------------------------------------
# cleanup_orphans_v2 — load-bearing properties
# ---------------------------------------------------------------


def test_backup_failure_aborts_no_expire_no_delete(
    tmp_table,
):
    """Property 1: backup raises → no expire, no delete."""
    mock_catalog = MagicMock()

    with (
        patch(
            "backend.maintenance.backup.run_backup",
            side_effect=RuntimeError("disk full"),
        ),
        patch.object(
            im,
            "_get_catalog",
            return_value=mock_catalog,
        ),
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
        )

    assert "backup failed" in result["error"]
    assert result["expired_snapshots"] == 0
    assert result["deleted_files"] == 0

    # Catalog was NEVER consulted
    mock_catalog.load_table.assert_not_called()
    # Live file still on disk
    assert tmp_table["data_file"].exists()


def test_referenced_files_are_never_deleted(
    tmp_table,
):
    """Property 2: anything in all_files() / all_manifests()
    survives the sweep, regardless of mtime.
    """
    mock_tbl = _build_mock_table(
        referenced_files=[tmp_table["data_file"]],
        referenced_manifests=[
            tmp_table["manifest_file"],
        ],
        snapshot_ids=[100, 101, 102],
    )
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_tbl

    with patch.object(
        im,
        "_get_catalog",
        return_value=mock_catalog,
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
        )

    assert result["verified"] is True
    assert tmp_table["data_file"].exists()
    assert tmp_table["manifest_file"].exists()
    # Catalog pointer survives too
    assert tmp_table["current_metadata"].exists()


def test_orphan_data_file_is_deleted(tmp_table):
    """An on-disk parquet not in the referenced set is
    eligible for deletion (after grace window)."""
    orphan = tmp_table["table_dir"] / "data" / "orphan.parquet"
    orphan.write_bytes(b"orphan-rows")
    _age_all(tmp_table["table_dir"], hours=2)

    mock_tbl = _build_mock_table(
        referenced_files=[tmp_table["data_file"]],
        referenced_manifests=[
            tmp_table["manifest_file"],
        ],
        snapshot_ids=[100],
    )
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_tbl

    with patch.object(
        im,
        "_get_catalog",
        return_value=mock_catalog,
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
        )

    assert result["deleted_files"] >= 1
    assert not orphan.exists()
    # Live file untouched
    assert tmp_table["data_file"].exists()
    # Catalog pointer untouched
    assert tmp_table["current_metadata"].exists()


def test_dry_run_lists_candidates_no_unlink(
    tmp_table,
):
    """Property: dry_run leaves every file in place."""
    orphan = tmp_table["table_dir"] / "data" / "orphan.parquet"
    orphan.write_bytes(b"orphan-rows")
    _age_all(tmp_table["table_dir"], hours=2)

    mock_tbl = _build_mock_table(
        referenced_files=[tmp_table["data_file"]],
        referenced_manifests=[
            tmp_table["manifest_file"],
        ],
        snapshot_ids=[100],
    )
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_tbl

    with patch.object(
        im,
        "_get_catalog",
        return_value=mock_catalog,
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
            dry_run=True,
        )

    assert result["dry_run"] is True
    assert result["candidate_count"] >= 1
    assert result["deleted_files"] == 0
    assert orphan.exists()  # NOT deleted


def test_mtime_grace_protects_recent_files(tmp_table):
    """Property 4: a file inside the grace window MUST NOT
    be deleted even if it's not referenced (concurrent-
    write race protection).
    """
    fresh = tmp_table["table_dir"] / "data" / "fresh.parquet"
    fresh.write_bytes(b"hot-write")
    # Leave fresh's mtime at "now" — grace must save it.

    # Age everything else past the window so it's eligible.
    _age_all(tmp_table["table_dir"], hours=2)
    # But re-touch fresh to "now"
    import os

    now = time.time()
    os.utime(fresh, (now, now))

    mock_tbl = _build_mock_table(
        referenced_files=[tmp_table["data_file"]],
        referenced_manifests=[
            tmp_table["manifest_file"],
        ],
        snapshot_ids=[100],
    )
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_tbl

    with patch.object(
        im,
        "_get_catalog",
        return_value=mock_catalog,
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
            mtime_grace_minutes=30,
        )

    assert fresh.exists()
    assert result["grace_skipped"] >= 1


def test_expire_snapshots_called_with_oldest_ids(
    tmp_table,
):
    """Property 5: with retain=2 and 5 snapshots,
    expire_snapshots gets the 3 oldest IDs."""
    mock_tbl = _build_mock_table(
        referenced_files=[tmp_table["data_file"]],
        referenced_manifests=[
            tmp_table["manifest_file"],
        ],
        snapshot_ids=[10, 20, 30, 40, 50],
    )
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_tbl

    with patch.object(
        im,
        "_get_catalog",
        return_value=mock_catalog,
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
            retain_snapshots=2,
        )

    # 5 snapshots in - retain 2 = 3 expired
    assert result["expired_snapshots"] == 3

    # Verify the by_ids call captured the OLDEST 3.
    es = mock_tbl.maintenance.expire_snapshots.return_value
    by_ids_call = es.by_ids.call_args
    assert by_ids_call is not None
    expired_ids = sorted(by_ids_call.args[0])
    # snapshot_ids list order = [10,20,30,40,50] with
    # ascending timestamps; latest 2 by ts are 40, 50.
    # Expired = [10, 20, 30].
    assert expired_ids == [10, 20, 30]


def test_no_expire_when_under_retain_threshold(
    tmp_table,
):
    """Edge: <= retain snapshots → expire_snapshots is
    never called."""
    mock_tbl = _build_mock_table(
        referenced_files=[tmp_table["data_file"]],
        referenced_manifests=[
            tmp_table["manifest_file"],
        ],
        snapshot_ids=[10, 20],
    )
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_tbl

    with patch.object(
        im,
        "_get_catalog",
        return_value=mock_catalog,
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
            retain_snapshots=5,
        )

    assert result["expired_snapshots"] == 0
    es = mock_tbl.maintenance.expire_snapshots.return_value
    es.by_ids.assert_not_called()


def test_read_verify_records_failure(tmp_table):
    """Post-sweep ``scan(limit=1)`` raises → result.verified
    is False (so the operator knows to restore).
    """
    mock_tbl = _build_mock_table(
        referenced_files=[tmp_table["data_file"]],
        referenced_manifests=[
            tmp_table["manifest_file"],
        ],
        snapshot_ids=[100],
        scan_raises=True,
    )
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_tbl

    with patch.object(
        im,
        "_get_catalog",
        return_value=mock_catalog,
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
        )

    assert result["verified"] is False


def test_no_catalog_pointer_refuses_sweep(
    tmp_table,
    tmp_path,
    monkeypatch,
):
    """If the catalog has no row for the table (or
    ``catalog.db`` is missing), the sweep is refused with
    no expiry and no deletion."""
    monkeypatch.setattr(
        im,
        "DEFAULT_CATALOG_DB",
        tmp_path / "missing.db",
    )

    mock_tbl = _build_mock_table(
        referenced_files=[tmp_table["data_file"]],
        referenced_manifests=[
            tmp_table["manifest_file"],
        ],
        snapshot_ids=[100],
    )
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_tbl

    with patch.object(
        im,
        "_get_catalog",
        return_value=mock_catalog,
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
        )

    assert result["error"] == "no catalog pointer"
    assert result["deleted_files"] == 0
    assert tmp_table["data_file"].exists()


def test_invalid_retain_snapshots(tmp_table):
    """retain_snapshots < 1 is a programmer error."""
    with pytest.raises(ValueError):
        im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
            retain_snapshots=0,
        )


def test_snapshot_manifest_list_files_kept_in_referenced(
    tmp_table,
):
    """Regression for live-prod failure (2026-04-25): the
    per-snapshot ``snap-{snapshot_id}-{seq}-{uuid}.avro``
    files referenced by ``snapshot.manifest_list`` MUST
    survive the sweep. Without this, ``tbl.scan()`` opens
    a missing manifest list and the table is unreadable
    until restored from backup.
    """
    metadata_dir = tmp_table["table_dir"] / "metadata"
    snap_avro_kept = metadata_dir / "snap-100-0-aaaaaaaa-bbbb-cccc-dddd.avro"
    snap_avro_kept.write_bytes(b"snapshot-list")
    _age_all(tmp_table["table_dir"], hours=2)

    mock_tbl = _build_mock_table(
        referenced_files=[tmp_table["data_file"]],
        referenced_manifests=[
            tmp_table["manifest_file"],
        ],
        snapshot_ids=[100],
        # Snapshot 100's manifest_list points at our file
        snapshot_manifest_lists=[snap_avro_kept],
    )
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_tbl

    with patch.object(
        im,
        "_get_catalog",
        return_value=mock_catalog,
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
        )

    # The snap-*.avro file MUST still exist
    assert snap_avro_kept.exists(), (
        "snapshot.manifest_list file deleted — would "
        "break tbl.scan() until backup restore"
    )
    assert result["verified"] is True


def test_metadata_chain_kept_in_referenced(tmp_table):
    """Property: the last (retain+5) ``*.metadata.json``
    files in the chain are added to the referenced set,
    so they survive the sweep even if all_files() doesn't
    list them.
    """
    # Add a few extra metadata.json files in the chain.
    extra = []
    metadata_dir = tmp_table["table_dir"] / "metadata"
    for i in range(3):
        p = metadata_dir / f"00{95+i}-old.metadata.json"
        p.write_text("{}")
        extra.append(p)
    _age_all(tmp_table["table_dir"], hours=2)

    mock_tbl = _build_mock_table(
        referenced_files=[tmp_table["data_file"]],
        referenced_manifests=[
            tmp_table["manifest_file"],
        ],
        snapshot_ids=[100],
    )
    mock_catalog = MagicMock()
    mock_catalog.load_table.return_value = mock_tbl

    with patch.object(
        im,
        "_get_catalog",
        return_value=mock_catalog,
    ):
        result = im.cleanup_orphans_v2(
            tmp_table["table_name"],
            skip_backup=True,
            retain_snapshots=5,
        )

    # All metadata.json files survive (3 extra + 2 from
    # fixture = 5, well within retain+5=10 chain budget).
    for p in extra:
        assert p.exists(), f"chain metadata {p.name} must survive"
    assert tmp_table["current_metadata"].exists()
    assert tmp_table["old_metadata"].exists()
    assert result["verified"] is True

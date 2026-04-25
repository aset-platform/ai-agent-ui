"""Safety guards on ``drop_dead_tables``.

Two properties are load-bearing (ASETPLTFRM-328):

1. Backup is fail-closed — if ``run_backup`` raises,
   neither the catalog nor the filesystem is touched.
2. On-disk ``rmtree`` is gated on a successful catalog
   ``drop_table``. A catalog failure must NOT wipe the
   data directory (would produce ``FileNotFoundError``
   on next read of a still-catalog-referenced table).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.maintenance import iceberg_maintenance as im


@pytest.fixture
def tmp_warehouse(monkeypatch, tmp_path):
    """Point WAREHOUSE_DIR at a scratch tree with
    stub data dirs for every ``DEAD_TABLES`` entry.
    """
    warehouse = tmp_path / "warehouse"
    for tn in im.DEAD_TABLES:
        (warehouse / tn.replace(".", "/")).mkdir(
            parents=True, exist_ok=True,
        )
        # Add a sentinel file so rmtree has work to do.
        (
            warehouse
            / tn.replace(".", "/")
            / "sentinel.parquet"
        ).write_text("x")

    monkeypatch.setattr(
        im, "WAREHOUSE_DIR", warehouse,
    )
    return warehouse


def _dir_exists(warehouse: Path, tn: str) -> bool:
    return (warehouse / tn.replace(".", "/")).exists()


def test_backup_failure_aborts_without_touching_data(
    tmp_warehouse,
):
    """Backup raises → no catalog mutation, no rmtree."""
    mock_catalog = MagicMock()

    with patch(
        "backend.maintenance.backup.run_backup",
        side_effect=RuntimeError("disk full"),
    ), patch.object(
        im, "_get_catalog", return_value=mock_catalog,
    ):
        result = im.drop_dead_tables()

    # Report carries the error
    assert "backup failed" in result["error"]
    assert result["dropped"] == []
    assert result["dirs_removed"] == []

    # Catalog was NEVER consulted (no drop_table calls)
    mock_catalog.drop_table.assert_not_called()

    # All data dirs still present
    for tn in im.DEAD_TABLES:
        assert _dir_exists(tmp_warehouse, tn), (
            f"{tn} dir should survive backup failure"
        )


def test_partial_catalog_failure_keeps_failed_dir(
    tmp_warehouse, tmp_path,
):
    """If catalog.drop_table fails for table N, the
    on-disk dir for N must remain — only successful
    drops get rmtreed.
    """
    fail_table = im.DEAD_TABLES[1]  # middle table

    def selective_drop(tn: str):
        if tn == fail_table:
            raise RuntimeError(
                "catalog locked by another txn",
            )
        # success: no-op

    mock_catalog = MagicMock()
    mock_catalog.drop_table.side_effect = selective_drop

    with patch(
        "backend.maintenance.backup.run_backup",
        return_value=tmp_path / "fake-backup",
    ), patch.object(
        im, "_get_catalog", return_value=mock_catalog,
    ):
        result = im.drop_dead_tables()

    # Failure was captured
    assert any(
        fail_table in s for s in result["skipped"]
    ), result
    # Other tables dropped cleanly
    assert len(result["dropped"]) == (
        len(im.DEAD_TABLES) - 1
    )

    # CRITICAL: failed table's dir survives
    assert _dir_exists(tmp_warehouse, fail_table), (
        f"{fail_table} dir must survive catalog "
        f"failure (would corrupt live reads otherwise)"
    )

    # Successful tables' dirs were rmtreed
    for tn in im.DEAD_TABLES:
        if tn == fail_table:
            continue
        assert not _dir_exists(tmp_warehouse, tn), (
            f"{tn} dir should have been removed"
        )


def test_no_such_table_is_idempotent_safe(
    tmp_warehouse, tmp_path,
):
    """NoSuchTableError (already dropped) should
    still allow dir cleanup — re-running is safe.
    """
    # Fabricate a NoSuchTableError-lookalike since
    # importing pyiceberg in-test is heavyweight.
    class NoSuchTableError(Exception):
        pass

    mock_catalog = MagicMock()
    mock_catalog.drop_table.side_effect = (
        NoSuchTableError(
            "already gone from catalog",
        )
    )

    with patch(
        "backend.maintenance.backup.run_backup",
        return_value=tmp_path / "fake-backup",
    ), patch.object(
        im, "_get_catalog", return_value=mock_catalog,
    ):
        result = im.drop_dead_tables()

    # All counted as dropped (idempotent)
    assert len(result["dropped"]) == len(
        im.DEAD_TABLES,
    )
    assert result["skipped"] == []
    # All dirs removed despite catalog raising
    for tn in im.DEAD_TABLES:
        assert not _dir_exists(tmp_warehouse, tn)

"""Tests for ``backup_table`` (ASETPLTFRM-400 slice 1h) +
``_rsync_timeout_s`` (slice 1j).

Covers the targeted per-table backup used by the retention job
and the env-var-driven timeout override. The full-warehouse
``run_backup`` is exercised elsewhere; here we focus on what
slices 1h / 1j changed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.maintenance.backup import (
    _DEFAULT_RSYNC_TIMEOUT_S,
    _rsync_timeout_s,
    _table_id_to_path_parts,
    backup_table,
)

# ────────────────────────────────────────────────────────────────
# _table_id_to_path_parts
# ────────────────────────────────────────────────────────────────


def test_table_id_to_path_parts_splits_namespace():
    assert _table_id_to_path_parts(
        "stocks.intraday_bars",
    ) == ("stocks", "intraday_bars")


def test_table_id_to_path_parts_rejects_unqualified():
    with pytest.raises(ValueError, match="namespace.table"):
        _table_id_to_path_parts("intraday_bars")


# ────────────────────────────────────────────────────────────────
# _rsync_timeout_s
# ────────────────────────────────────────────────────────────────


def test_rsync_timeout_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("BACKUP_RSYNC_TIMEOUT_S", raising=False)
    assert _rsync_timeout_s() == _DEFAULT_RSYNC_TIMEOUT_S


def test_rsync_timeout_default_value_is_1800s():
    """Hard-asserted so a future "back to 600s" regression is
    fail-loud. The 2026-05-13 rsync-timeout incident is the
    reason it lives at 30 min."""
    assert _DEFAULT_RSYNC_TIMEOUT_S == 1800


def test_rsync_timeout_env_override(monkeypatch):
    monkeypatch.setenv("BACKUP_RSYNC_TIMEOUT_S", "3600")
    assert _rsync_timeout_s() == 3600


def test_rsync_timeout_env_clamped_minimum(monkeypatch):
    """Don't accept absurdly low values (e.g. ``0`` would mean
    "abort immediately") — clamp to 60s minimum."""
    monkeypatch.setenv("BACKUP_RSYNC_TIMEOUT_S", "5")
    assert _rsync_timeout_s() == 60


def test_rsync_timeout_invalid_env_falls_back_to_default(
    monkeypatch,
):
    monkeypatch.setenv("BACKUP_RSYNC_TIMEOUT_S", "thirty_min")
    assert _rsync_timeout_s() == _DEFAULT_RSYNC_TIMEOUT_S


# ────────────────────────────────────────────────────────────────
# backup_table — file system smoke
# ────────────────────────────────────────────────────────────────


def _seed_fake_table(
    warehouse: Path, ns: str, name: str, payload: bytes = b"parquet\n"
) -> Path:
    """Create a fake table on disk that backup_table can copy."""
    src = warehouse / ns / name
    (src / "data" / "ticker=AAA").mkdir(parents=True)
    (src / "metadata").mkdir(parents=True)
    (src / "data" / "ticker=AAA" / "00000.parquet").write_bytes(
        payload,
    )
    (src / "metadata" / "v1.metadata.json").write_text("{}")
    # catalog.db sits one level up from the warehouse root.
    (warehouse.parent / "catalog.db").write_bytes(
        b"sqlite\n",
    )
    return src


def test_backup_table_copies_table_dir(tmp_path):
    warehouse = tmp_path / "warehouse"
    backups = tmp_path / "backups"
    _seed_fake_table(warehouse, "stocks", "intraday_bars")

    dest = backup_table(
        "stocks.intraday_bars",
        warehouse=str(warehouse),
        backup_root=str(backups),
    )
    dest_path = Path(dest)
    assert dest_path.exists()
    # Parquet roundtrip
    copied = (
        dest_path / "intraday_bars" / "data" / "ticker=AAA" / "00000.parquet"
    )
    assert copied.read_bytes() == b"parquet\n"
    # Catalog.db is included so the backup is standalone-restorable.
    assert (dest_path / "catalog.db").exists()


def test_backup_table_directory_name_includes_table_id(tmp_path):
    warehouse = tmp_path / "warehouse"
    backups = tmp_path / "backups"
    _seed_fake_table(warehouse, "stocks", "intraday_bars")

    dest = backup_table(
        "stocks.intraday_bars",
        warehouse=str(warehouse),
        backup_root=str(backups),
    )
    # The dated suffix prevents collisions with the
    # full-warehouse ``backup-<date>`` and with other tables
    # backed up on the same day.
    assert "stocks-intraday_bars" in Path(dest).name


def test_backup_table_missing_source_raises(tmp_path):
    """A table the writer never created → rsync would create
    an empty backup which would be misleading. Fail-loud instead."""
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    backups = tmp_path / "backups"
    with pytest.raises(FileNotFoundError, match="missing"):
        backup_table(
            "stocks.intraday_bars",
            warehouse=str(warehouse),
            backup_root=str(backups),
        )


def test_backup_table_passes_through_timeout_kwarg(tmp_path):
    """Explicit ``timeout_s=`` arg overrides the env default."""
    warehouse = tmp_path / "warehouse"
    backups = tmp_path / "backups"
    _seed_fake_table(warehouse, "stocks", "intraday_bars")

    with patch(
        "backend.maintenance.backup.subprocess.run",
    ) as mrun:
        mrun.return_value.returncode = 0
        mrun.return_value.stderr = ""
        mrun.return_value.stdout = "0\t."
        backup_table(
            "stocks.intraday_bars",
            warehouse=str(warehouse),
            backup_root=str(backups),
            timeout_s=42,
        )
    # backup_table makes 2 subprocess.run calls: rsync, then
    # du for size reporting. The rsync call (first) is the
    # one carrying our timeout_s arg.
    rsync_call = next(
        c for c in mrun.call_args_list if c.args and "rsync" in c.args[0][0]
    )
    assert rsync_call.kwargs["timeout"] == 42


# ────────────────────────────────────────────────────────────────
# _rotate_backups — full-snapshot filter
# ────────────────────────────────────────────────────────────────


def test_rotate_backups_ignores_per_table_dirs(tmp_path):
    """Rotation must not consider per-table fallback dirs
    when deciding which full snapshots to keep — otherwise
    a freshly-rsynced full snapshot can be rotated out by
    yesterday's per-table cruft (2026-05-23 incident)."""
    from backend.maintenance.backup import _rotate_backups

    # Three full snapshots — old, middle, newest
    (tmp_path / "backup-2026-05-21").mkdir()
    (tmp_path / "backup-2026-05-22").mkdir()
    (tmp_path / "backup-2026-05-23").mkdir()
    # Per-table fallback dirs that lexicographically sort
    # AFTER backup-2026-05-23 (because "-" > end-of-string)
    (tmp_path / "backup-2026-05-23-stocks-ohlcv").mkdir()
    (tmp_path / "backup-2026-05-23-algo-events").mkdir()

    _rotate_backups(tmp_path, keep=2)

    # Newest two full snapshots survive
    assert (tmp_path / "backup-2026-05-23").exists()
    assert (tmp_path / "backup-2026-05-22").exists()
    # Oldest full snapshot rotated out
    assert not (tmp_path / "backup-2026-05-21").exists()
    # Per-table dirs untouched
    assert (tmp_path / "backup-2026-05-23-stocks-ohlcv").exists()
    assert (tmp_path / "backup-2026-05-23-algo-events").exists()


def test_rotate_backups_pre_existing_full_snapshots_still_work(
    tmp_path,
):
    """Plain rotation behaviour preserved when no per-table
    dirs are present."""
    from backend.maintenance.backup import _rotate_backups

    (tmp_path / "backup-2026-05-20").mkdir()
    (tmp_path / "backup-2026-05-21").mkdir()
    (tmp_path / "backup-2026-05-22").mkdir()
    (tmp_path / "backup-2026-05-23").mkdir()

    _rotate_backups(tmp_path, keep=2)

    assert (tmp_path / "backup-2026-05-23").exists()
    assert (tmp_path / "backup-2026-05-22").exists()
    assert not (tmp_path / "backup-2026-05-21").exists()
    assert not (tmp_path / "backup-2026-05-20").exists()

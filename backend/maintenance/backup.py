"""Iceberg warehouse backup and rotation.

Backs up the entire Iceberg warehouse directory to a
dated folder using rsync. Maintains 2 latest backups
(today + yesterday), rotating out older ones.

Usage::

    from backend.maintenance.backup import (
        run_backup, restore_backup,
    )
    run_backup()                  # backup + rotate
    restore_backup("2026-04-16")  # restore from date
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

_logger = logging.getLogger(__name__)

WAREHOUSE_DIR = os.path.expanduser(
    os.environ.get(
        "AI_AGENT_UI_HOME",
        "~/.ai-agent-ui",
    )
) + "/data/iceberg/warehouse"

BACKUP_ROOT = (
    "/Users/abhay/Documents/projects/"
    "ai-agent-ui-backups"
)

MAX_BACKUPS = 2


def run_backup(
    warehouse: str | None = None,
    backup_root: str | None = None,
    keep: int = MAX_BACKUPS,
) -> str:
    """Backup Iceberg warehouse and rotate.

    Args:
        warehouse: Source dir (default: WAREHOUSE_DIR)
        backup_root: Backup parent dir
        keep: Number of backups to retain

    Returns:
        Path to the new backup directory.
    """
    src = warehouse or WAREHOUSE_DIR
    root = Path(backup_root or BACKUP_ROOT)
    root.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    dest = root / f"backup-{today}"

    _logger.info(
        "Backing up %s → %s", src, dest,
    )

    # rsync: archive mode, delete files in dest
    # that no longer exist in source
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync", "-a", "--delete",
        f"{src}/",
        f"{dest}/warehouse/",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        _logger.error(
            "rsync failed: %s", result.stderr,
        )
        raise RuntimeError(
            f"Backup failed: {result.stderr}"
        )

    # Also backup the SQLite catalog
    catalog_db = Path(src).parent / "catalog.db"
    if catalog_db.exists():
        import shutil as _shutil

        _shutil.copy2(
            catalog_db,
            dest / "catalog.db",
        )

    size = _dir_size_mb(dest)
    _logger.info(
        "Backup complete: %s (%.1f MB)",
        dest, size,
    )

    # Rotate: keep only N latest backups
    _rotate_backups(root, keep)

    return str(dest)


def restore_backup(
    date_str: str,
    warehouse: str | None = None,
    backup_root: str | None = None,
) -> None:
    """Restore warehouse from a dated backup.

    Args:
        date_str: Backup date (YYYY-MM-DD)
        warehouse: Target dir (default: WAREHOUSE_DIR)
        backup_root: Backup parent dir
    """
    src_dir = (
        Path(backup_root or BACKUP_ROOT)
        / f"backup-{date_str}"
    )
    dest = warehouse or WAREHOUSE_DIR

    if not src_dir.exists():
        raise FileNotFoundError(
            f"No backup found: {src_dir}"
        )

    _logger.info(
        "Restoring from %s → %s",
        src_dir, dest,
    )

    warehouse_src = src_dir / "warehouse"
    if not warehouse_src.exists():
        # Legacy backup format (no subdirs)
        warehouse_src = src_dir

    cmd = [
        "rsync", "-a", "--delete",
        f"{warehouse_src}/",
        f"{dest}/",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Restore failed: {result.stderr}"
        )

    # Restore catalog if present
    cat_backup = src_dir / "catalog.db"
    cat_dest = Path(dest).parent / "catalog.db"
    if cat_backup.exists():
        import shutil as _shutil

        _shutil.copy2(cat_backup, cat_dest)
        _logger.info(
            "Restored catalog.db",
        )

    _logger.info("Restore complete from %s", src_dir)


def list_backups(
    backup_root: str | None = None,
) -> list[dict]:
    """List available backups with dates and sizes.

    Returns:
        List of dicts with:
          - date: folder-name date (e.g. "2026-04-25").
            Suffixed names like "2026-04-22-pre-dedupe"
            preserved verbatim.
          - path: absolute filesystem path.
          - size_mb: directory size in MB.
          - completed_at: ISO 8601 UTC timestamp with
            ``Z`` suffix derived from the directory's
            mtime — the actual backup completion time.
            Use this (not ``date``) for age math; the
            folder name only carries the calendar date,
            not the time-of-completion.
    """
    root = Path(backup_root or BACKUP_ROOT)
    if not root.exists():
        return []

    backups = []
    for d in sorted(root.iterdir(), reverse=True):
        if d.is_dir() and d.name.startswith(
            "backup-",
        ):
            dt = d.name.replace("backup-", "")
            # Convert mtime → UTC ISO string with Z so
            # the frontend's `new Date(...)` parses it
            # unambiguously and renders in the user's
            # browser TZ. Mirrors the `_iso_utc()`
            # convention used by other admin endpoints.
            completed_at = (
                datetime.fromtimestamp(
                    d.stat().st_mtime,
                    tz=timezone.utc,
                )
                .isoformat()
                .replace("+00:00", "Z")
            )
            backups.append({
                "date": dt,
                "path": str(d),
                "size_mb": round(
                    _dir_size_mb(d), 1,
                ),
                "completed_at": completed_at,
            })
    return backups


def _rotate_backups(
    root: Path, keep: int,
) -> None:
    """Remove oldest backups beyond keep limit."""
    dirs = sorted(
        [
            d for d in root.iterdir()
            if d.is_dir()
            and d.name.startswith("backup-")
        ],
        reverse=True,
    )
    for old in dirs[keep:]:
        _logger.info(
            "Rotating out old backup: %s", old,
        )
        shutil.rmtree(old)


def _dir_size_mb(path: Path) -> float:
    """Get directory size in MB via du (fast)."""
    try:
        result = subprocess.run(
            ["du", "-sk", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            kb = int(
                result.stdout.split()[0],
            )
            return kb / 1024.0
    except Exception:
        pass
    # Fallback: Python walk (slow for large dirs)
    total = sum(
        f.stat().st_size
        for f in path.rglob("*")
        if f.is_file()
    )
    return total / (1024 * 1024)

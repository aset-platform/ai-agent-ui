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

WAREHOUSE_DIR = (
    os.path.expanduser(
        os.environ.get(
            "AI_AGENT_UI_HOME",
            "~/.ai-agent-ui",
        )
    )
    + "/data/iceberg/warehouse"
)

BACKUP_ROOT = "/Users/abhay/Documents/projects/" "ai-agent-ui-backups"

MAX_BACKUPS = 2

# Default rsync wall-clock cap. The warehouse grew past 8 GB
# after the intraday_bars backfill landed and 600s started
# timing out on the daily run (2026-05-13 incident). 1800s
# (30 min) accommodates the small-file scan cost.
# Override via ``BACKUP_RSYNC_TIMEOUT_S`` env var.
_DEFAULT_RSYNC_TIMEOUT_S = 1800


def _rsync_timeout_s() -> int:
    raw = os.environ.get("BACKUP_RSYNC_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_RSYNC_TIMEOUT_S
    try:
        return max(60, int(raw))
    except ValueError:
        _logger.warning(
            "BACKUP_RSYNC_TIMEOUT_S=%r is not an int — " "falling back to %ds",
            raw,
            _DEFAULT_RSYNC_TIMEOUT_S,
        )
        return _DEFAULT_RSYNC_TIMEOUT_S


def _table_id_to_path_parts(table_id: str) -> tuple[str, str]:
    """``stocks.intraday_bars`` → ``("stocks", "intraday_bars")``.

    Matches the on-disk layout PyIceberg uses (one dir per
    namespace, one subdir per table).
    """
    if "." not in table_id:
        raise ValueError(
            f"table_id {table_id!r} must be 'namespace.table'",
        )
    ns, name = table_id.split(".", 1)
    return ns, name


def backup_table(
    table_id: str,
    *,
    warehouse: str | None = None,
    backup_root: str | None = None,
    timeout_s: int | None = None,
) -> str:
    """Rsync a single Iceberg table's directory to a dated
    backup location.

    Targeted variant of :func:`run_backup` used when a single
    table needs a safety copy before a destructive op (e.g. the
    intraday-bars retention delete; ASETPLTFRM-400 slice 1h).
    Much faster than the full-warehouse backup because it scans
    one table's parquet tree instead of all of them.

    Args:
        table_id: ``namespace.table`` (e.g.
            ``"stocks.intraday_bars"``).
        warehouse: Override the source warehouse root.
        backup_root: Override the backup parent dir.
        timeout_s: Override the rsync wall-clock cap.

    Returns:
        Absolute path to the per-table backup directory.

    Raises:
        FileNotFoundError: If the source table directory
            doesn't exist (table never written).
        RuntimeError: If rsync exits non-zero or times out.
    """
    ns, name = _table_id_to_path_parts(table_id)
    src_root = warehouse or WAREHOUSE_DIR
    src = Path(src_root) / ns / name
    if not src.exists():
        raise FileNotFoundError(
            f"Table source missing: {src} (table {table_id} "
            "has no data written yet?)",
        )
    root = Path(backup_root or BACKUP_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    dest = root / f"backup-{today}-{ns}-{name}"
    dest.mkdir(parents=True, exist_ok=True)
    timeout = timeout_s or _rsync_timeout_s()

    _logger.info(
        "Backing up table %s: %s → %s (timeout=%ds)",
        table_id,
        src,
        dest,
        timeout,
    )
    cmd = [
        "rsync",
        "-a",
        "--delete",
        "--stats",
        f"{src}/",
        f"{dest}/{name}/",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        _logger.error(
            "table backup rsync failed for %s: %s",
            table_id,
            result.stderr,
        )
        raise RuntimeError(
            f"Backup of {table_id} failed: {result.stderr}",
        )
    # Also copy the SQLite catalog so the backup is restorable
    # standalone (table parquets without the catalog can't be
    # loaded — the catalog stores absolute metadata paths).
    catalog_db = Path(src_root).parent / "catalog.db"
    if catalog_db.exists():
        shutil.copy2(catalog_db, dest / "catalog.db")
    size = _dir_size_mb(dest)
    _logger.info(
        "Table backup complete: %s (%.1f MB)",
        dest,
        size,
    )
    return str(dest)


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
        "Backing up %s → %s",
        src,
        dest,
    )

    # rsync: archive mode, delete files in dest
    # that no longer exist in source. Timeout configurable via
    # ``BACKUP_RSYNC_TIMEOUT_S`` env var (default 1800s; bumped
    # from 600s after the 2026-05-13 maintenance step started
    # timing out on the now-8 GB warehouse).
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync",
        "-a",
        "--delete",
        "--stats",
        f"{src}/",
        f"{dest}/warehouse/",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_rsync_timeout_s(),
    )
    if result.returncode != 0:
        _logger.error(
            "rsync failed: %s",
            result.stderr,
        )
        raise RuntimeError(f"Backup failed: {result.stderr}")

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
        dest,
        size,
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
    src_dir = Path(backup_root or BACKUP_ROOT) / f"backup-{date_str}"
    dest = warehouse or WAREHOUSE_DIR

    if not src_dir.exists():
        raise FileNotFoundError(f"No backup found: {src_dir}")

    _logger.info(
        "Restoring from %s → %s",
        src_dir,
        dest,
    )

    warehouse_src = src_dir / "warehouse"
    if not warehouse_src.exists():
        # Legacy backup format (no subdirs)
        warehouse_src = src_dir

    cmd = [
        "rsync",
        "-a",
        "--delete",
        "--stats",
        f"{warehouse_src}/",
        f"{dest}/",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_rsync_timeout_s(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Restore failed: {result.stderr}")

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
            backups.append(
                {
                    "date": dt,
                    "path": str(d),
                    "size_mb": round(
                        _dir_size_mb(d),
                        1,
                    ),
                    "completed_at": completed_at,
                }
            )
    return backups


def verify_or_backup(
    tables: list[str],
    *,
    max_age_h: float = 24.0,
    backup_root: str | None = None,
) -> dict:
    """Check today's snapshot covers ``tables``; fall back
    to per-table ``backup_table()`` if not.

    Returns:
        ``{"mode": "verified",
           "snapshot": "<path>",
           "paths": []}``
        when the manifest is fresh AND lists every requested
        table.

        ``{"mode": "fallback_per_table",
           "snapshot": None,
           "paths": [<per-table-backup-paths>]}``
        otherwise.

    The fallback path swallows ``FileNotFoundError`` (table
    never written) the same way the legacy scoped-maintenance
    branch does — log + continue.
    """
    from backend.maintenance.backup_manifest import (
        read_manifest,
    )

    root = Path(backup_root or BACKUP_ROOT)
    today = date.today().isoformat()
    snapshot_root = root / f"backup-{today}"

    manifest = read_manifest(snapshot_root)
    if manifest is not None:
        created_iso = manifest.get("created_at", "")
        try:
            created_at = datetime.fromisoformat(
                created_iso.replace("Z", "+00:00"),
            )
        except ValueError:
            created_at = None
        if created_at is not None:
            age_h = (
                datetime.now(timezone.utc) - created_at
            ).total_seconds() / 3600
            listed = {
                t["id"] for t in manifest.get("tables", [])
            }
            if age_h <= max_age_h and set(tables) <= listed:
                return {
                    "mode": "verified",
                    "snapshot": str(snapshot_root),
                    "paths": [],
                }

    paths: list[str] = []
    for t in tables:
        try:
            paths.append(backup_table(t))
        except FileNotFoundError:
            _logger.info(
                "[verify_or_backup] %s: no on-disk data, "
                "skipping per-table backup",
                t,
            )
    return {
        "mode": "fallback_per_table",
        "snapshot": None,
        "paths": paths,
    }


def _rotate_backups(
    root: Path,
    keep: int,
) -> None:
    """Remove oldest backups beyond keep limit."""
    dirs = sorted(
        [
            d
            for d in root.iterdir()
            if d.is_dir() and d.name.startswith("backup-")
        ],
        reverse=True,
    )
    for old in dirs[keep:]:
        _logger.info(
            "Rotating out old backup: %s",
            old,
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
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)

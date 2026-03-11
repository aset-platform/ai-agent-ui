"""Migrate data, charts, and logs from the project root to ~/.ai-agent-ui.

Copies (not moves) every file from the old project-local directories
into the centralised application home so that existing deployments
keep working until the operator confirms the migration succeeded.

The script is **idempotent** — files that already exist at the
destination are skipped (never overwritten).

Usage::

    source ~/.ai-agent-ui/venv/bin/activate
    python scripts/migrate_data_home.py          # dry-run
    python scripts/migrate_data_home.py --apply  # real copy
"""

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-7s  %(message)s",
)
_logger = logging.getLogger(__name__)

# Ensure backend/ on sys.path for paths module
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = str(_PROJECT_ROOT / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from paths import (  # noqa: E402
    APP_HOME,
    AVATARS_DIR,
    CACHE_DIR,
    CHARTS_ANALYSIS_DIR,
    CHARTS_FORECASTS_DIR,
    FORECASTS_DIR,
    ICEBERG_DIR,
    LOGS_DIR,
    METADATA_DIR,
    PROCESSED_DIR,
    RAW_DIR,
    ensure_dirs,
)

# Map: old project-local directory -> new APP_HOME directory
_MIGRATIONS: list[tuple[Path, Path]] = [
    (_PROJECT_ROOT / "data" / "iceberg", ICEBERG_DIR),
    (_PROJECT_ROOT / "data" / "raw", RAW_DIR),
    (_PROJECT_ROOT / "data" / "forecasts", FORECASTS_DIR),
    (_PROJECT_ROOT / "data" / "cache", CACHE_DIR),
    (_PROJECT_ROOT / "data" / "avatars", AVATARS_DIR),
    (_PROJECT_ROOT / "data" / "metadata", METADATA_DIR),
    (_PROJECT_ROOT / "data" / "processed", PROCESSED_DIR),
    (_PROJECT_ROOT / "charts" / "analysis", CHARTS_ANALYSIS_DIR),
    (_PROJECT_ROOT / "charts" / "forecasts", CHARTS_FORECASTS_DIR),
    (_PROJECT_ROOT / "logs", LOGS_DIR),
]


def _copy_tree(
    src: Path,
    dst: Path,
    *,
    dry_run: bool = True,
) -> tuple[int, int]:
    """Recursively copy *src* into *dst*, skipping existing files.

    Args:
        src: Source directory (project-local).
        dst: Destination directory (under APP_HOME).
        dry_run: When ``True``, only report what would happen.

    Returns:
        ``(copied, skipped)`` file counts.
    """
    copied = 0
    skipped = 0
    if not src.exists():
        return copied, skipped
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        dest_dir = dst / rel
        for fname in files:
            src_file = Path(root) / fname
            dst_file = dest_dir / fname
            if dst_file.exists():
                skipped += 1
                continue
            if dry_run:
                _logger.info(
                    "WOULD COPY %s -> %s",
                    src_file,
                    dst_file,
                )
            else:
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                _logger.info("COPIED %s", dst_file)
            copied += 1
    return copied, skipped


def _rewrite_iceberg_paths(
    catalog_db: Path,
    old_root: Path,
    new_root: Path,
) -> None:
    """Rewrite warehouse paths in catalog and metadata files.

    The SQLite catalog and Iceberg metadata JSON files store
    absolute ``file:///`` URIs.  After copying the warehouse
    to a new location these must be updated.

    Args:
        catalog_db: Path to the new ``catalog.db``.
        old_root: Old application root (project checkout).
        new_root: New application root (``APP_HOME``).
    """
    import glob as _glob
    import sqlite3

    old_wh = str(old_root / "data" / "iceberg" / "warehouse")
    new_wh = str(new_root / "data" / "iceberg" / "warehouse")

    # 1. Rewrite SQLite catalog rows
    conn = sqlite3.connect(str(catalog_db))
    cur = conn.cursor()
    for col in ("metadata_location", "previous_metadata_location"):
        cur.execute(
            f"UPDATE iceberg_tables SET {col} = "  # noqa: S608
            f"REPLACE({col}, ?, ?) "
            f"WHERE {col} IS NOT NULL",
            (old_wh, new_wh),
        )
    conn.commit()
    _logger.info(
        "Rewrote catalog paths (%d tables)",
        cur.execute("SELECT count(*) FROM iceberg_tables").fetchone()[0],
    )
    conn.close()

    # 2. Rewrite metadata JSON files
    wh_dir = new_root / "data" / "iceberg" / "warehouse"
    json_files = _glob.glob(str(wh_dir / "**" / "*.json"), recursive=True)
    count = 0
    for fpath in json_files:
        text = Path(fpath).read_text(encoding="utf-8")
        if old_wh in text:
            Path(fpath).write_text(
                text.replace(old_wh, new_wh),
                encoding="utf-8",
            )
            count += 1
    _logger.info("Rewrote %d metadata JSON files", count)


def migrate(*, dry_run: bool = True) -> None:
    """Run the full migration.

    Args:
        dry_run: When ``True`` (default), only logs what
            would be copied without writing any files.
    """
    mode = "DRY RUN" if dry_run else "APPLY"
    _logger.info("=== Migration %s ===", mode)
    _logger.info("Source : %s", _PROJECT_ROOT)
    _logger.info("Target : %s", APP_HOME)
    _logger.info("")

    if not dry_run:
        ensure_dirs()

    total_copied = 0
    total_skipped = 0

    for src, dst in _MIGRATIONS:
        if not src.exists():
            _logger.info(
                "SKIP (not found): %s",
                src.relative_to(_PROJECT_ROOT),
            )
            continue
        c, s = _copy_tree(src, dst, dry_run=dry_run)
        label = src.relative_to(_PROJECT_ROOT)
        _logger.info(
            "  %-25s  copied=%d  skipped=%d",
            label,
            c,
            s,
        )
        total_copied += c
        total_skipped += s

    # Force-copy catalog.db (ensure_dirs may have created
    # an empty one before the migration ran).
    old_catalog = _PROJECT_ROOT / "data" / "iceberg" / "catalog.db"
    if old_catalog.exists():
        from paths import ICEBERG_CATALOG

        if dry_run:
            _logger.info("WOULD FORCE-COPY catalog.db")
        else:
            shutil.copy2(old_catalog, ICEBERG_CATALOG)
            _logger.info("FORCE-COPIED catalog.db")
            _rewrite_iceberg_paths(
                ICEBERG_CATALOG,
                _PROJECT_ROOT,
                APP_HOME,
            )

    # Create backwards-compat symlink so that hardcoded
    # paths inside binary Iceberg avro manifests still resolve.
    _old_iceberg = _PROJECT_ROOT / "data" / "iceberg"
    if not dry_run and not _old_iceberg.exists():
        _old_iceberg.parent.mkdir(parents=True, exist_ok=True)
        _old_iceberg.symlink_to(ICEBERG_DIR)
        _logger.info(
            "Created symlink %s -> %s",
            _old_iceberg,
            ICEBERG_DIR,
        )
    elif dry_run and not _old_iceberg.is_symlink():
        _logger.info(
            "WOULD CREATE symlink %s -> %s",
            _old_iceberg,
            ICEBERG_DIR,
        )

    _logger.info("")
    _logger.info(
        "Total: %d files copied, %d skipped",
        total_copied,
        total_skipped,
    )
    if dry_run and total_copied > 0:
        _logger.info("Re-run with --apply to perform the copy.")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate project data to ~/.ai-agent-ui",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually copy files (default is dry-run).",
    )
    args = parser.parse_args()
    migrate(dry_run=not args.apply)


if __name__ == "__main__":
    main()

"""One-shot cleanup: remove legacy per-table backup
directories created by the pre-ASETPLTFRM-backup-redesign
scoped-maintenance loop.

Safe to run after the new ``backups_daily`` job has produced
its first successful full-warehouse snapshot. Preserves
today's per-table dirs as belt-and-braces — they're deleted
on tomorrow's run.

Usage::

    python scripts/cleanup_per_table_backups.py
    python scripts/cleanup_per_table_backups.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
from datetime import date
from pathlib import Path

_logger = logging.getLogger(__name__)

BACKUP_ROOT = "/Users/abhay/Documents/projects/ai-agent-ui-backups"

PER_TABLE_PATTERN = re.compile(
    r"^backup-\d{4}-\d{2}-\d{2}-(stocks|algo)-.+$",
)


def main(dry_run: bool = False) -> None:
    root = Path(BACKUP_ROOT)
    if not root.exists():
        _logger.error("Backup root missing: %s", root)
        return

    today_prefix = f"backup-{date.today().isoformat()}-"
    removed_bytes = 0
    removed_count = 0
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if not PER_TABLE_PATTERN.match(d.name):
            continue
        if d.name.startswith(today_prefix):
            _logger.info(
                "preserving today's per-table dir: %s",
                d.name,
            )
            continue
        size = sum(
            f.stat().st_size
            for f in d.rglob("*")
            if f.is_file()
        )
        removed_bytes += size
        removed_count += 1
        action = "would remove" if dry_run else "removing"
        _logger.info(
            "%s %s (%.1f MB)", action, d, size / (1024 * 1024),
        )
        if not dry_run:
            shutil.rmtree(d)

    _logger.info(
        "%s %d directories, %.1f MB",
        "would reclaim" if dry_run else "reclaimed",
        removed_count,
        removed_bytes / (1024 * 1024),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)

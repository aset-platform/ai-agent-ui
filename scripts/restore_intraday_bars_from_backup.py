"""Restore ``stocks.intraday_bars`` from the per-table backup
created by ``backup_table`` (ASETPLTFRM-400 slice 1i).

Rolls back a partial / failed partition migration WITHOUT
touching any other table in the SQLite catalog.

Steps
-----
1. Drop the (possibly partial) ``stocks.intraday_bars`` from
   the live catalog. Other tables in the catalog are
   untouched.
2. ``rsync --delete`` the backup's ``intraday_bars/`` over
   the live table directory. Wipes any partial files written
   since the backup was taken.
3. Find the latest ``*.metadata.json`` inside the restored
   directory.
4. Register that metadata.json as ``stocks.intraday_bars`` in
   the live catalog via ``catalog.register_table``. The
   table re-appears with its pre-migration schema + data,
   indistinguishable from "no migration ever happened".
5. ``invalidate_metadata`` so DuckDB's in-memory metadata
   cache picks up the new (= old) latest snapshot on the
   next read.

Usage
-----
::

    PYTHONPATH=.:backend python scripts/restore_intraday_bars_from_backup.py \
        [--backup PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from backend.db.duckdb_engine import invalidate_metadata
from backend.maintenance.backup import _rsync_timeout_s

_logger = logging.getLogger(__name__)

TABLE_ID = "stocks.intraday_bars"
DEFAULT_BACKUP_ROOT = "/Users/abhay/Documents/projects/ai-agent-ui-backups"
LIVE_TABLE_DIR = (
    "/Users/abhay/.ai-agent-ui/data/iceberg/warehouse/" "stocks/intraday_bars"
)


def _today_backup_path() -> Path:
    today = date.today().isoformat()
    return Path(DEFAULT_BACKUP_ROOT) / f"backup-{today}-stocks-intraday_bars"


def _latest_metadata_in(backup_table_dir: Path) -> Path:
    meta_dir = backup_table_dir / "metadata"
    if not meta_dir.exists():
        raise FileNotFoundError(
            f"metadata dir missing in backup: {meta_dir}",
        )
    files = sorted(meta_dir.glob("*.metadata.json"))
    if not files:
        raise FileNotFoundError(
            f"no metadata.json files in {meta_dir}",
        )
    return files[-1]


def run_restore(
    *,
    backup_dir: Path,
    dry_run: bool = False,
) -> dict:
    if not backup_dir.exists():
        raise FileNotFoundError(
            f"backup dir not found: {backup_dir}",
        )
    table_subdir = backup_dir / "intraday_bars"
    if not table_subdir.exists():
        raise FileNotFoundError(
            f"backup table subdir missing: {table_subdir}",
        )
    latest_meta = _latest_metadata_in(table_subdir)
    # The metadata.json inside the backup references the LIVE
    # warehouse paths (rsync preserves the absolute paths
    # embedded by PyIceberg). Confirm by reading the json.
    _logger.info(
        "Restore plan: latest backup metadata = %s",
        latest_meta.name,
    )
    if dry_run:
        return {
            "status": "dry_run",
            "backup_dir": str(backup_dir),
            "latest_metadata": str(latest_meta),
        }

    from stocks.create_tables import _get_catalog

    cat = _get_catalog()

    # 1. Drop the (possibly partial) live table from catalog.
    try:
        cat.drop_table(TABLE_ID)
        _logger.info("Dropped %s from catalog", TABLE_ID)
    except Exception as exc:  # noqa: BLE001
        _logger.info(
            "Drop %s skipped (likely already absent): %s",
            TABLE_ID,
            exc,
        )

    # 2. Wipe live dir + rsync backup over.
    live = Path(LIVE_TABLE_DIR)
    if live.exists():
        _logger.info("Removing live dir %s", live)
        shutil.rmtree(live)
    live.mkdir(parents=True, exist_ok=True)
    _logger.info(
        "Rsync %s → %s (timeout=%ds)",
        table_subdir,
        live,
        _rsync_timeout_s(),
    )
    result = subprocess.run(
        [
            "rsync",
            "-a",
            "--delete",
            "--stats",
            f"{table_subdir}/",
            f"{live}/",
        ],
        capture_output=True,
        text=True,
        timeout=_rsync_timeout_s(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"rsync restore failed: {result.stderr}",
        )

    # 3. Re-register the restored metadata.json under the
    # original table identifier. PyIceberg's
    # ``register_table`` re-attaches a catalog row without
    # rewriting any data — the file paths inside the
    # metadata.json are absolute and unchanged.
    live_meta = live / "metadata" / latest_meta.name
    if not live_meta.exists():
        raise FileNotFoundError(
            f"expected restored metadata at {live_meta}",
        )
    cat.register_table(TABLE_ID, str(live_meta))
    _logger.info(
        "Registered %s @ %s",
        TABLE_ID,
        live_meta.name,
    )

    # 4. Clear DuckDB's cache so subsequent reads see the
    # restored metadata immediately.
    invalidate_metadata(TABLE_ID)

    return {
        "status": "ok",
        "backup_dir": str(backup_dir),
        "registered_metadata": str(live_meta),
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="restore_intraday_bars_from_backup",
        description=(
            "Restore stocks.intraday_bars from a per-table "
            "backup. ASETPLTFRM-400 slice 1i."
        ),
    )
    p.add_argument(
        "--backup",
        default=None,
        help=(
            "Backup dir (default: today's "
            f"{DEFAULT_BACKUP_ROOT}/backup-<today>-stocks-"
            "intraday_bars)."
        ),
    )
    p.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"),
    )
    args = _build_arg_parser().parse_args(argv)
    backup_dir = Path(args.backup) if args.backup else _today_backup_path()
    result = run_restore(
        backup_dir=backup_dir,
        dry_run=args.dry_run,
    )
    _logger.info("Result: %s", result)
    return 0 if result["status"] in ("ok", "dry_run") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

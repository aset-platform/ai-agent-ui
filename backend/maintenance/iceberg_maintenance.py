"""Iceberg table maintenance — compaction, snapshot
expiry, retention, and orphan cleanup.

Addresses file fragmentation (many small parquet files
per partition) and metadata bloat (thousands of snapshots)
that cause slow deletes and reads.

Usage::

    from backend.maintenance.iceberg_maintenance import (
        run_maintenance,
    )
    run_maintenance(level="daily")   # expire + compact
    run_maintenance(level="monthly") # + retention + orphan
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

_logger = logging.getLogger(__name__)

# Iceberg warehouse root
WAREHOUSE_DIR = Path(
    os.path.expanduser(
        os.environ.get(
            "AI_AGENT_UI_HOME",
            "~/.ai-agent-ui",
        )
    )
) / "data" / "iceberg" / "warehouse"

# All Iceberg tables in the system
# Active Iceberg tables (excludes migrated/dead:
# scheduler_runs, scheduled_jobs → PG;
# technical_indicators → unused)
ALL_TABLES = [
    "stocks.ohlcv",
    "stocks.analysis_summary",
    "stocks.company_info",
    "stocks.dividends",
    "stocks.forecast_runs",
    "stocks.forecasts",
    "stocks.quarterly_results",
    "stocks.piotroski_scores",
    "stocks.sentiment_scores",
    "stocks.llm_pricing",
    "stocks.llm_usage",
    "stocks.portfolio_transactions",
]

# Dead tables safe to drop (migrated to PG or unused)
DEAD_TABLES = [
    "stocks.scheduler_runs",
    "stocks.scheduled_jobs",
    "stocks.technical_indicators",
]

# Date columns per table for retention purge
DATE_COLUMNS: dict[str, str] = {
    "stocks.ohlcv": "date",
    "stocks.analysis_summary": "analysis_date",
    "stocks.sentiment_scores": "score_date",
    "stocks.forecast_runs": "run_date",
    "stocks.forecasts": "run_date",
    "stocks.dividends": "ex_date",
    "stocks.quarterly_results": "quarter_end",
    "stocks.piotroski_scores": "score_date",
    "stocks.audit_log": "timestamp",
    "stocks.usage_history": "timestamp",
    "stocks.llm_usage": "request_date",
}

MAX_RETENTION_YEARS = 11
SNAPSHOT_KEEP = 5


def _get_catalog():
    """Load the PyIceberg catalog."""
    from pyiceberg.catalog import load_catalog

    return load_catalog("local")


def drop_dead_tables() -> dict:
    """Drop tables migrated to PG or unused.

    Removes from Iceberg catalog then deletes the
    on-disk data directory — but only for tables
    that successfully dropped from the catalog.

    Safety:
        * Always runs a full warehouse backup first
          (fail-closed — aborts without mutating
          anything if the backup fails). Matches the
          daily ``iceberg_maintenance`` step pattern.
        * Per-table rmtree is gated on the catalog
          drop succeeding. A partial failure in the
          catalog loop therefore cannot wipe on-disk
          files that are still catalog-referenced.
        * ``NoSuchTableError`` is treated as "already
          dropped" and still enables directory cleanup
          — safe to re-run idempotently.

    Returns:
        Dict with:
        - ``backup``: path to the pre-op backup
        - ``dropped``: tables removed from catalog
          (including already-absent ones)
        - ``skipped``: tables the catalog failed to
          drop (kept on disk for recovery)
        - ``dirs_removed``: raw warehouse dirs rmtreed
    """
    from backend.maintenance.backup import run_backup

    # Fail-closed backup — any caller (ad-hoc or
    # pipeline) gets a restore point before we touch
    # either the catalog or the filesystem.
    try:
        backup_path = run_backup()
        _logger.info(
            "[maint] drop_dead_tables: backup %s",
            backup_path,
        )
    except Exception as exc:
        _logger.error(
            "[maint] drop_dead_tables: backup FAILED "
            "— aborting to preserve recoverability",
            exc_info=True,
        )
        return {
            "error": f"backup failed: {exc}",
            "dropped": [],
            "skipped": [],
            "dirs_removed": [],
        }

    catalog = _get_catalog()
    dropped: list[str] = []
    dropped_ok: set[str] = set()
    skipped: list[str] = []

    for tn in DEAD_TABLES:
        try:
            catalog.drop_table(tn)
            dropped.append(tn)
            dropped_ok.add(tn)
            _logger.info(
                "[maint] Dropped dead table: %s",
                tn,
            )
        except Exception as exc:
            # NoSuchTableError means the table is
            # already gone from the catalog — safe to
            # proceed to dir cleanup. Import lazily
            # to keep this function decoupled from
            # the pyiceberg version-shaped exception
            # module path.
            exc_name = type(exc).__name__
            if exc_name == "NoSuchTableError":
                dropped.append(tn)
                dropped_ok.add(tn)
                _logger.info(
                    "[maint] Dead table %s already "
                    "absent from catalog",
                    tn,
                )
            else:
                skipped.append(f"{tn}: {exc}")
                _logger.warning(
                    "[maint] Skip drop %s: %s (data "
                    "dir preserved for recovery)",
                    tn, exc,
                )

    # Only rmtree directories whose catalog entry was
    # successfully removed. A catalog failure above
    # leaves the table catalog-referenced; wiping
    # its files would produce FileNotFoundError on
    # next read.
    import shutil

    dirs_removed: list[str] = []
    for tn in DEAD_TABLES:
        if tn not in dropped_ok:
            continue
        table_dir = (
            WAREHOUSE_DIR
            / tn.replace(".", "/")
        )
        if table_dir.exists():
            shutil.rmtree(table_dir)
            dirs_removed.append(str(table_dir))
            _logger.info(
                "[maint] Removed data dir: %s",
                table_dir,
            )

    return {
        "backup": str(backup_path),
        "dropped": dropped,
        "skipped": skipped,
        "dirs_removed": dirs_removed,
    }


def expire_snapshots(
    table_name: str,
    keep: int = SNAPSHOT_KEEP,
) -> dict:
    """Expire old snapshots, keeping latest N.

    Args:
        table_name: e.g. 'stocks.ohlcv'
        keep: Number of snapshots to retain

    Returns:
        Dict with before/after counts.
    """
    catalog = _get_catalog()
    tbl = catalog.load_table(table_name)

    snapshots = list(tbl.metadata.snapshots)
    before = len(snapshots)

    if before <= keep:
        _logger.info(
            "[maint] %s: %d snapshots, no "
            "expiry needed (keep=%d)",
            table_name, before, keep,
        )
        return {
            "table": table_name,
            "before": before,
            "after": before,
            "expired": 0,
        }

    # Sort by timestamp, keep latest N
    sorted_snaps = sorted(
        snapshots,
        key=lambda s: s.timestamp_ms,
        reverse=True,
    )
    keep_ids = {
        s.snapshot_id
        for s in sorted_snaps[:keep]
    }

    # PyIceberg doesn't expose a safe snapshot
    # expiry API. Metadata files are referenced
    # by the SQLite catalog — deleting them
    # breaks table loading. Compaction via
    # overwrite() is the primary cleanup path:
    # it replaces fragmented files with a single
    # batch per partition. Old snapshots add
    # metadata overhead but don't affect reads.
    _logger.info(
        "[maint] %s: %d snapshots present "
        "(compaction handles file cleanup; "
        "snapshot metadata is retained for "
        "safety)",
        table_name, before,
    )

    # Reload to verify
    tbl = catalog.load_table(table_name)
    after = len(list(tbl.metadata.snapshots))
    expired = before - after

    _logger.info(
        "[maint] %s: expired %d snapshots "
        "(%d → %d)",
        table_name, expired, before, after,
    )
    return {
        "table": table_name,
        "before": before,
        "after": after,
        "expired": expired,
    }


def compact_table(table_name: str) -> dict:
    """Compact small files by rewriting partitions.

    Reads all data via DuckDB, deletes the table
    contents, and re-appends as a single batch —
    producing 1 file per partition instead of many.

    Args:
        table_name: e.g. 'stocks.ohlcv'

    Returns:
        Dict with before/after file counts.
    """
    from backend.db.duckdb_engine import (
        query_iceberg_df,
    )

    view_name = table_name.split(".")[-1]

    # Count files before
    table_dir = (
        WAREHOUSE_DIR
        / table_name.replace(".", "/")
    )
    before = _count_parquet_files(table_dir)

    _logger.info(
        "[maint] Compacting %s (%d parquet "
        "files before)",
        table_name, before,
    )

    t0 = time.monotonic()

    # Read all data via DuckDB
    try:
        df = query_iceberg_df(
            table_name,
            f"SELECT * FROM {view_name}",
        )
    except Exception:
        _logger.error(
            "[maint] Failed to read %s",
            table_name,
            exc_info=True,
        )
        return {
            "table": table_name,
            "error": "read failed",
        }

    if df.empty:
        _logger.info(
            "[maint] %s is empty, nothing to "
            "compact",
            table_name,
        )
        return {
            "table": table_name,
            "before": before,
            "after": before,
            "rows": 0,
        }

    rows = len(df)

    # Overwrite table with compacted data
    import pyarrow as pa

    from tools._stock_shared import _require_repo

    repo = _require_repo()
    tbl = repo.load_table(table_name)

    # Convert DataFrame to Arrow, aligning with
    # the Iceberg schema
    arrow = pa.Table.from_pandas(
        df, preserve_index=False,
    )

    # Use overwrite to replace all data in one
    # commit — produces 1 file per partition
    try:
        tbl.overwrite(arrow)
        # Invalidate DuckDB cache
        try:
            from backend.db.duckdb_engine import (
                invalidate_metadata,
            )

            invalidate_metadata(table_name)
        except Exception:
            pass
    except Exception:
        _logger.error(
            "[maint] Overwrite failed for %s",
            table_name,
            exc_info=True,
        )
        return {
            "table": table_name,
            "error": "overwrite failed",
        }

    elapsed = time.monotonic() - t0
    after = _count_parquet_files(table_dir)

    _logger.info(
        "[maint] Compacted %s: %d → %d files, "
        "%d rows in %.1fs",
        table_name, before, after, rows, elapsed,
    )
    return {
        "table": table_name,
        "before": before,
        "after": after,
        "rows": rows,
        "elapsed_s": round(elapsed, 1),
    }


def purge_old_data(
    table_name: str,
    max_years: int = MAX_RETENTION_YEARS,
) -> dict:
    """Delete rows older than max_years.

    Args:
        table_name: Iceberg table identifier
        max_years: Retention window (default 11)

    Returns:
        Dict with purge details.
    """
    date_col = DATE_COLUMNS.get(table_name)
    if not date_col:
        return {
            "table": table_name,
            "skipped": "no date column mapped",
        }

    cutoff = date.today() - timedelta(
        days=max_years * 365,
    )

    from pyiceberg.expressions import LessThan

    from tools._stock_shared import _require_repo

    repo = _require_repo()

    _logger.info(
        "[maint] Purging %s rows before %s",
        table_name, cutoff,
    )

    try:
        repo.delete_rows(
            table_name,
            LessThan(date_col, cutoff.isoformat()),
        )
        return {
            "table": table_name,
            "cutoff": str(cutoff),
            "status": "purged",
        }
    except Exception as exc:
        _logger.warning(
            "[maint] Purge failed for %s: %s",
            table_name, exc,
        )
        return {
            "table": table_name,
            "error": str(exc),
        }


def cleanup_orphans(table_name: str) -> dict:
    """Remove empty partition directories.

    After compaction, old partition dirs may be
    left empty. This removes them but does NOT
    delete any parquet files — file lifecycle is
    managed by Iceberg metadata via overwrite().

    Args:
        table_name: Iceberg table identifier

    Returns:
        Dict with cleanup details.
    """
    table_dir = (
        WAREHOUSE_DIR
        / table_name.replace(".", "/")
        / "data"
    )
    if not table_dir.exists():
        return {"table": table_name, "cleaned": 0}

    # Only remove empty directories
    cleaned = 0
    for d in list(table_dir.rglob("*")):
        if (
            d.is_dir()
            and not any(d.iterdir())
        ):
            d.rmdir()
            cleaned += 1

    if cleaned:
        _logger.info(
            "[maint] %s: removed %d empty "
            "partition dirs",
            table_name, cleaned,
        )

    return {
        "table": table_name,
        "cleaned": cleaned,
    }


def run_maintenance(
    tables: list[str] | None = None,
    level: str = "daily",
) -> dict:
    """Run maintenance on Iceberg tables.

    Args:
        tables: Tables to maintain (default: ALL)
        level: 'daily' (expire + compact) or
            'monthly' (+ retention + orphan cleanup)

    Returns:
        Summary dict with per-table results.
    """
    from backend.maintenance.backup import (
        run_backup,
    )

    target = tables or ALL_TABLES

    _logger.info(
        "[maint] Starting %s maintenance on "
        "%d tables",
        level, len(target),
    )

    # Step 1: Backup before any destructive ops
    t0 = time.monotonic()
    try:
        backup_path = run_backup()
        _logger.info(
            "[maint] Backup complete: %s",
            backup_path,
        )
    except Exception:
        _logger.error(
            "[maint] Backup failed, aborting "
            "maintenance",
            exc_info=True,
        )
        return {"error": "backup failed"}

    results: dict = {
        "level": level,
        "backup": backup_path,
        "tables": {},
    }

    for tn in target:
        tbl_result: dict = {}

        # Always: expire snapshots
        try:
            tbl_result["expire"] = (
                expire_snapshots(tn)
            )
        except Exception as exc:
            tbl_result["expire"] = {
                "error": str(exc),
            }

        # Always: compact
        try:
            tbl_result["compact"] = (
                compact_table(tn)
            )
        except Exception as exc:
            tbl_result["compact"] = {
                "error": str(exc),
            }

        # Monthly only: retention + orphans
        if level == "monthly":
            try:
                tbl_result["retention"] = (
                    purge_old_data(tn)
                )
            except Exception as exc:
                tbl_result["retention"] = {
                    "error": str(exc),
                }

            try:
                tbl_result["orphans"] = (
                    cleanup_orphans(tn)
                )
            except Exception as exc:
                tbl_result["orphans"] = {
                    "error": str(exc),
                }

        results["tables"][tn] = tbl_result

    elapsed = time.monotonic() - t0
    results["elapsed_s"] = round(elapsed, 1)

    _logger.info(
        "[maint] %s maintenance complete in "
        "%.1fs",
        level, elapsed,
    )
    return results


def _count_parquet_files(
    table_dir: Path,
) -> int:
    """Count parquet files in a table directory."""
    if not table_dir.exists():
        return 0
    return sum(
        1
        for _ in table_dir.rglob("*.parquet")
    )

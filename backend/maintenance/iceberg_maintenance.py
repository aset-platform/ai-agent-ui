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
WAREHOUSE_DIR = (
    Path(
        os.path.expanduser(
            os.environ.get(
                "AI_AGENT_UI_HOME",
                "~/.ai-agent-ui",
            )
        )
    )
    / "data"
    / "iceberg"
    / "warehouse"
)

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
                    "[maint] Dead table %s already " "absent from catalog",
                    tn,
                )
            else:
                skipped.append(f"{tn}: {exc}")
                _logger.warning(
                    "[maint] Skip drop %s: %s (data "
                    "dir preserved for recovery)",
                    tn,
                    exc,
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
        table_dir = WAREHOUSE_DIR / tn.replace(".", "/")
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
            "[maint] %s: %d snapshots, no " "expiry needed (keep=%d)",
            table_name,
            before,
            keep,
        )
        return {
            "table": table_name,
            "before": before,
            "after": before,
            "expired": 0,
        }

    # No-op kept for backwards compatibility — see
    # ``cleanup_orphans_v2`` for the real PyIceberg
    # 0.11.1 expiry path. Compaction via overwrite()
    # remains the primary file-count cleanup; this
    # function previously logged "expired N" but
    # didn't actually call expire_snapshots() because
    # the API was assumed unsafe. That assumption is
    # outdated. Callers wanting real expiry should
    # call ``cleanup_orphans_v2`` instead.
    _logger.info(
        "[maint] %s: %d snapshots present "
        "(legacy no-op; use cleanup_orphans_v2 "
        "for real expiry)",
        table_name,
        before,
    )

    # Reload to verify
    tbl = catalog.load_table(table_name)
    after = len(list(tbl.metadata.snapshots))
    expired = before - after

    _logger.info(
        "[maint] %s: expired %d snapshots " "(%d → %d)",
        table_name,
        expired,
        before,
        after,
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
    table_dir = WAREHOUSE_DIR / table_name.replace(".", "/")
    before = _count_parquet_files(table_dir)

    _logger.info(
        "[maint] Compacting %s (%d parquet " "files before)",
        table_name,
        before,
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
            "[maint] %s is empty, nothing to " "compact",
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
        df,
        preserve_index=False,
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
        "[maint] Compacted %s: %d → %d files, " "%d rows in %.1fs",
        table_name,
        before,
        after,
        rows,
        elapsed,
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
        table_name,
        cutoff,
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
            table_name,
            exc,
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
    table_dir = WAREHOUSE_DIR / table_name.replace(".", "/") / "data"
    if not table_dir.exists():
        return {"table": table_name, "cleaned": 0}

    # Only remove empty directories
    cleaned = 0
    for d in list(table_dir.rglob("*")):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            cleaned += 1

    if cleaned:
        _logger.info(
            "[maint] %s: removed %d empty " "partition dirs",
            table_name,
            cleaned,
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
        "[maint] Starting %s maintenance on " "%d tables",
        level,
        len(target),
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
            "[maint] Backup failed, aborting " "maintenance",
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
            tbl_result["expire"] = expire_snapshots(tn)
        except Exception as exc:
            tbl_result["expire"] = {
                "error": str(exc),
            }

        # Always: compact
        try:
            tbl_result["compact"] = compact_table(tn)
        except Exception as exc:
            tbl_result["compact"] = {
                "error": str(exc),
            }

        # Monthly only: retention + orphans
        if level == "monthly":
            try:
                tbl_result["retention"] = purge_old_data(tn)
            except Exception as exc:
                tbl_result["retention"] = {
                    "error": str(exc),
                }

            try:
                tbl_result["orphans"] = cleanup_orphans(tn)
            except Exception as exc:
                tbl_result["orphans"] = {
                    "error": str(exc),
                }

        results["tables"][tn] = tbl_result

    elapsed = time.monotonic() - t0
    results["elapsed_s"] = round(elapsed, 1)

    _logger.info(
        "[maint] %s maintenance complete in " "%.1fs",
        level,
        elapsed,
    )
    return results


def _count_parquet_files(
    table_dir: Path,
) -> int:
    """Count parquet files in a table directory."""
    if not table_dir.exists():
        return 0
    return sum(1 for _ in table_dir.rglob("*.parquet"))


# ---------------------------------------------------------------
# Orphan sweep v2 — safe physical reclamation.
# Companion to ASETPLTFRM-338. Uses PyIceberg 0.11.1 native
# expire_snapshots() + inspect.all_files() / all_manifests() to
# build an authoritative referenced-set, then unlinks anything
# else that's older than a configurable mtime grace window.
# Catalog pointer is hard-excluded with a paranoid assertion to
# prevent the past failure mode from CLAUDE.md rule 20.
# ---------------------------------------------------------------


# Default catalog DB location. Resolved lazily so tests can
# point at a temp catalog by patching this constant.
DEFAULT_CATALOG_DB = WAREHOUSE_DIR.parent / "catalog.db"


def _normalize_uri(path: str) -> str:
    """Normalize a file path or URI to an absolute string.

    The Iceberg catalog stores ``metadata_location`` as a
    ``file://`` URI (sometimes with extra slashes); the
    filesystem walk yields plain ``/abs/path`` strings.
    Both forms must compare equal so the catalog-pointer
    safety assertion fires reliably.

    Examples::

        file:////Users/abhay/x.json -> /Users/abhay/x.json
        file:///Users/abhay/x.json  -> /Users/abhay/x.json
        /Users/abhay/x.json         -> /Users/abhay/x.json
    """
    if not path:
        return ""
    s = str(path)
    if s.startswith("file://"):
        s = s[len("file://") :]
        # Collapse leading triple-slash from
        # ``file:////abs/path`` (PyIceberg style) to
        # ``/abs/path``.
        while s.startswith("//"):
            s = s[1:]
    return os.path.abspath(s)


def _read_catalog_metadata_location(
    table_name: str,
    catalog_db: Path | None = None,
) -> str | None:
    """Look up the catalog's current ``metadata_location``
    pointer for ``table_name`` (``"namespace.table"``).

    Reads ``catalog.db`` directly via sqlite3 — that's the
    SQLite catalog backing PyIceberg's ``SqlCatalog``. The
    pointer is a single absolute file URI; deleting that
    exact file breaks ``catalog.load_table`` (CLAUDE.md
    rule 20 origin incident).

    Returns ``None`` if the row is missing — caller treats
    that as a hard fail (refuse to sweep).
    """
    import sqlite3

    db = catalog_db or DEFAULT_CATALOG_DB
    if not db.exists():
        _logger.warning(
            "[orphan-sweep] catalog db not found: %s",
            db,
        )
        return None

    ns, name = table_name.split(".", 1)
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT metadata_location FROM "
            "iceberg_tables WHERE table_namespace=? "
            "AND table_name=?",
            (ns, name),
        ).fetchone()
    finally:
        conn.close()

    return row[0] if row else None


def cleanup_orphans_v2(
    table_name: str,
    *,
    retain_snapshots: int = SNAPSHOT_KEEP,
    mtime_grace_minutes: int = 30,
    dry_run: bool = False,
    skip_backup: bool = False,
    catalog_db: Path | None = None,
    warehouse_dir: Path | None = None,
) -> dict:
    """Safe orphan-parquet/manifest/metadata sweep.

    Replaces the no-op ``cleanup_orphans()`` for callers
    that opt-in. ``cleanup_orphans()`` stays as the
    backwards-compatible empty-dir-only fallback.

    Algorithm (matches ASETPLTFRM-338 spec):

    0. Mandatory backup (fail-closed) — unless
       ``skip_backup=True`` (tests only).
    1. Expire old snapshots, keeping the latest
       ``retain_snapshots`` by ``timestamp_ms``.
    2. Build the Iceberg-authoritative referenced set =
       ``inspect.all_files()`` ∪ ``inspect.all_manifests()``.
    3. Add the catalog's current ``metadata_location``
       pointer (the file PyIceberg loads on open).
    4. Add the last ``retain_snapshots + 5`` metadata.json
       files in the chain so a recent ``UPDATE
       metadata_location`` rollback is still possible.
    5. Walk the table dir for parquet + avro +
       ``*.metadata.json``.
    6. Filter to candidates: not in referenced AND mtime
       older than ``mtime_grace_minutes`` (race safety).
    7. Paranoid assertion: refuse to delete anything that
       normalises equal to the catalog pointer.
    8. Unlink (or skip on ``dry_run``).
    9. Read-verify: reload the table and execute a
       1-row scan. If it raises, the sweep is reported
       as ``verified=False`` and the caller decides.

    Args:
        table_name: e.g. ``"stocks.ohlcv"``.
        retain_snapshots: latest N snapshots to keep.
        mtime_grace_minutes: skip files newer than this.
            Default 30 covers a sentiment/forecast batch.
        dry_run: when True, returns the would-delete list
            without unlinking. Backup still runs unless
            ``skip_backup``.
        skip_backup: tests only — bypass the backup step.
            Production callers MUST leave this False.
        catalog_db: override path to ``catalog.db`` (tests).
        warehouse_dir: override warehouse root (tests).

    Returns:
        Dict with:

        - ``backup``: backup path or None
        - ``expired_snapshots``: count of snapshot ids
          passed to ``expire_snapshots``
        - ``referenced_count``: size of the referenced set
        - ``on_disk_count``: number of files walked
        - ``candidate_count``: orphans before grace filter
        - ``grace_skipped``: files skipped due to mtime
        - ``deleted_files``: count actually unlinked
          (0 on dry_run)
        - ``deleted_bytes``: bytes reclaimed
        - ``verified``: True if the post-sweep scan
          succeeded
        - ``dry_run``: echo of the input flag
    """
    from backend.maintenance.backup import run_backup

    if retain_snapshots < 1:
        raise ValueError("retain_snapshots must be >= 1")

    warehouse = warehouse_dir or WAREHOUSE_DIR
    table_dir = warehouse / table_name.replace(".", "/")

    result: dict = {
        "table": table_name,
        "backup": None,
        "expired_snapshots": 0,
        "referenced_count": 0,
        "on_disk_count": 0,
        "candidate_count": 0,
        "grace_skipped": 0,
        "deleted_files": 0,
        "deleted_bytes": 0,
        "verified": False,
        "dry_run": dry_run,
    }

    # Step 0 — backup (fail-closed).
    if not skip_backup:
        try:
            backup_path = run_backup()
            result["backup"] = str(backup_path)
            _logger.info(
                "[orphan-sweep] %s: backup %s",
                table_name,
                backup_path,
            )
        except Exception as exc:
            _logger.error(
                "[orphan-sweep] %s: backup FAILED — "
                "aborting to preserve recoverability",
                table_name,
                exc_info=True,
            )
            result["error"] = f"backup failed: {exc}"
            return result

    catalog = _get_catalog()
    tbl = catalog.load_table(table_name)

    # Step 1 — expire old snapshots, keep latest N.
    snapshots = sorted(
        list(tbl.metadata.snapshots),
        key=lambda s: s.timestamp_ms,
        reverse=True,
    )
    keep_ids = {s.snapshot_id for s in snapshots[:retain_snapshots]}
    expire_ids = [
        s.snapshot_id for s in snapshots if s.snapshot_id not in keep_ids
    ]
    if expire_ids:
        try:
            (tbl.maintenance.expire_snapshots().by_ids(expire_ids).commit())
            tbl = catalog.load_table(table_name)
            result["expired_snapshots"] = len(
                expire_ids,
            )
            _logger.info(
                "[orphan-sweep] %s: expired %d " "snapshots (kept %d)",
                table_name,
                len(expire_ids),
                len(keep_ids),
            )
        except Exception:
            _logger.error(
                "[orphan-sweep] %s: expire_snapshots "
                "failed — continuing without expiry",
                table_name,
                exc_info=True,
            )

    # Step 2 — Iceberg-authoritative referenced set.
    referenced: set[str] = set()
    try:
        af = tbl.inspect.all_files()
        for path in af.column("file_path").to_pylist():
            referenced.add(_normalize_uri(path))
    except Exception:
        _logger.error(
            "[orphan-sweep] %s: all_files() failed",
            table_name,
            exc_info=True,
        )
        result["error"] = "all_files failed"
        return result

    try:
        am = tbl.inspect.all_manifests()
        for path in am.column("path").to_pylist():
            referenced.add(_normalize_uri(path))
    except Exception:
        _logger.error(
            "[orphan-sweep] %s: all_manifests() " "failed",
            table_name,
            exc_info=True,
        )
        result["error"] = "all_manifests failed"
        return result

    # Step 2b — manifest-list files (snap-*.avro) for
    # every retained snapshot. ``inspect.all_manifests()``
    # returns the data manifests ({uuid}-m0.avro) but
    # NOT the per-snapshot manifest LIST files
    # (snap-{snapshot_id}-{seq}-{uuid}.avro). The
    # current snapshot's manifest_list is what
    # ``tbl.scan()`` opens first — deleting it breaks
    # every read until restored from backup.
    for snap in tbl.metadata.snapshots:
        ml = getattr(snap, "manifest_list", None)
        if ml:
            referenced.add(_normalize_uri(ml))

    # Step 3 — catalog pointer (paranoid).
    catalog_pointer = _read_catalog_metadata_location(
        table_name,
        catalog_db=catalog_db,
    )
    if not catalog_pointer:
        _logger.error(
            "[orphan-sweep] %s: catalog pointer not "
            "readable — refusing to sweep",
            table_name,
        )
        result["error"] = "no catalog pointer"
        return result
    catalog_pointer_norm = _normalize_uri(
        catalog_pointer,
    )
    referenced.add(catalog_pointer_norm)

    # Step 4 — last (N+5) metadata.json files in chain.
    metadata_dir = table_dir / "metadata"
    if metadata_dir.exists():
        chain = sorted(
            metadata_dir.glob("*.metadata.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for p in chain[: retain_snapshots + 5]:
            referenced.add(_normalize_uri(str(p)))

    result["referenced_count"] = len(referenced)

    # Step 5 — walk on-disk.
    on_disk: list[Path] = []
    if table_dir.exists():
        for pat in (
            "*.parquet",
            "*.avro",
            "*.metadata.json",
        ):
            on_disk.extend(table_dir.rglob(pat))
    result["on_disk_count"] = len(on_disk)

    # Step 6 — filter candidates + mtime grace.
    cutoff_ts = time.time() - mtime_grace_minutes * 60
    grace_skipped = 0
    candidates: list[Path] = []
    for p in on_disk:
        try:
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            continue
        norm = _normalize_uri(str(p))
        if norm in referenced:
            continue
        if mtime >= cutoff_ts:
            grace_skipped += 1
            continue
        candidates.append(p)
    result["candidate_count"] = len(candidates)
    result["grace_skipped"] = grace_skipped

    # Step 7 — paranoid: never the catalog pointer.
    for p in candidates:
        if _normalize_uri(str(p)) == catalog_pointer_norm:
            raise AssertionError("REFUSING to delete catalog pointer:" f" {p}")

    # Step 8 — unlink (or report on dry_run).
    if dry_run:
        _logger.info(
            "[orphan-sweep] %s DRY-RUN: %d candidates "
            "(skipped %d in grace window)",
            table_name,
            len(candidates),
            grace_skipped,
        )
    else:
        deleted = 0
        bytes_ = 0
        for p in candidates:
            try:
                bytes_ += p.stat().st_size
                p.unlink()
                deleted += 1
            except FileNotFoundError:
                continue
            except Exception:
                _logger.warning(
                    "[orphan-sweep] %s: unlink " "failed for %s",
                    table_name,
                    p,
                    exc_info=True,
                )
        result["deleted_files"] = deleted
        result["deleted_bytes"] = bytes_
        _logger.info(
            "[orphan-sweep] %s: deleted %d files "
            "(%.2f MB), grace-skipped %d",
            table_name,
            deleted,
            bytes_ / 1_048_576,
            grace_skipped,
        )

    # Step 9 — read-verify.
    try:
        tbl_check = catalog.load_table(table_name)
        list(tbl_check.scan(limit=1).to_arrow().to_pylist())
        result["verified"] = True
    except Exception:
        _logger.error(
            "[orphan-sweep] %s: post-sweep read "
            "VERIFY FAILED — restore from backup",
            table_name,
            exc_info=True,
        )
        result["verified"] = False

    # Best-effort: invalidate DuckDB metadata cache so
    # subsequent reads see the post-sweep file set.
    try:
        from backend.db.duckdb_engine import (
            invalidate_metadata,
        )

        invalidate_metadata(table_name)
    except Exception:
        pass

    return result

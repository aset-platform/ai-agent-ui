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
    # Sprint 9 Advanced Analytics tables — daily ingest,
    # need compaction + retention.
    "stocks.nse_delivery",
    "stocks.fundamentals_snapshot",
    "stocks.corporate_events",
    "stocks.promoter_holdings",
    # Intraday backtest data (ASETPLTFRM-400 slice 1b).
    # Historical 15m / 5m / 1m bars from Kite — daily
    # incremental keeper + on-demand backfill writes
    # accumulate small parquets per (ticker, bar_date)
    # partition; daily compaction keeps reads tight.
    "stocks.intraday_bars",
    # Centralized feature engine output (ASETPLTFRM-402 /
    # FE-1). Long-format feature rows accumulate per
    # (ticker, year_month) partition. Intentionally NOT
    # in DATE_COLUMNS below: ``bar_date`` is a STRING and
    # retention is governed by the partition layout, same
    # rationale as ``stocks.intraday_bars`` (see NOTE in
    # DATE_COLUMNS).
    "stocks.intraday_features",
    # Per-fill feature snapshots (ASETPLTFRM-402 / FE-5).
    # One single-row Iceberg append per executed fill —
    # accumulates one parquet per commit without
    # compaction. Same rationale as ``stocks.intraday_bars``
    # for omitting from DATE_COLUMNS: ``bar_date`` is a
    # STRING and retention is governed by the
    # ``(year_month, mode)`` partition layout, not the
    # generic MAX_RETENTION_YEARS purge.
    "stocks.trade_feature_snapshots",
    # NSE index intraday bars (ASETPLTFRM-402 / FE-6).
    # Mirrors ``stocks.intraday_bars`` shape exactly — the
    # daily keeper writes ~10 NSE indices × 1-3 cadences
    # (15m / 5m / 1m) per run. Intentionally NOT in
    # DATE_COLUMNS below: ``bar_date`` is a STRING and
    # retention is governed by the partition layout, same
    # rationale as ``stocks.intraday_bars``.
    "stocks.index_intraday_bars",
    # Algo namespace — write-heavy event streams. 2026-05-12
    # incident: algo.events bloated to 11 GB of metadata.json
    # (5,901 snapshots, ~2 MB each) because it was missing from
    # this list. Every LiveRuntime emission (signal_generated,
    # order_submitted_live, kite_postback_received, fills, etc.)
    # is one commit + one new metadata.json with the full
    # snapshot history embedded. Daily maintenance keeps the
    # snapshot count + manifest count bounded.
    "algo.events",
    "algo.intraday_bars",
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
    # Sprint 9 Advanced Analytics tables.
    "stocks.nse_delivery": "date",
    "stocks.fundamentals_snapshot": "snapshot_date",
    "stocks.corporate_events": "event_date",
    "stocks.promoter_holdings": "quarter_end",
    # Algo event streams — retention pruned by IST partition col.
    "algo.events": "ts_date",
    # NOTE: ``stocks.intraday_bars`` is intentionally absent —
    # retention policy is set by the slice 1d daily-ingest job
    # (rolling 4-year window from the backfill anchor), not
    # the generic MAX_RETENTION_YEARS purge. Symmetric with
    # ``algo.intraday_bars`` which is also kept out for the
    # same reason.
}

MAX_RETENTION_YEARS = 11
SNAPSHOT_KEEP = 5
# Never expire a snapshot younger than this, regardless of
# ``SNAPSHOT_KEEP``. High-commit tables (stocks.ohlcv takes ~13
# commits/day) burn through 5 snapshots in hours, so a pure count
# floor would expire a morning snapshot by evening and delete its
# manifest-list out from under a daily reader's metadata cache
# (ASETPLTFRM-429). 48h covers any reader cache populated
# "yesterday". → reader-side guard in db/duckdb_engine.py.
SNAPSHOT_MIN_AGE_HOURS = 48


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
    """Expire old snapshots (keep latest ``keep``) via the real
    PyIceberg path. Delegates to ``cleanup_orphans_v2`` which
    performs native expire + referenced-set orphan sweep.

    Caller contract: a step-0 backup must already have been taken
    upstream (``skip_backup=True`` here). ``run_maintenance`` and
    the post-pipeline expiry tail satisfy this via the daily
    snapshot + Iceberg snapshot retention.

    Args:
        table_name: e.g. 'stocks.ohlcv'
        keep: Number of snapshots to retain (default SNAPSHOT_KEEP)

    Returns:
        Dict with keys ``table``, ``expired``, ``verified``.
    """
    res = cleanup_orphans_v2(
        table_name,
        retain_snapshots=keep,
        skip_backup=True,
    )
    return {
        "table": table_name,
        "expired": int(res.get("expired_snapshots", 0)),
        "verified": res.get("verified", True),
    }


def compact_table(table_name: str) -> dict:
    """Compact small files by rewriting partitions.

    Reads the table via PyIceberg (catalog truth), deletes the
    contents, and re-appends as a single batch — producing one
    file per partition instead of many.

    Args:
        table_name: e.g. 'stocks.ohlcv'

    Returns:
        Dict with before/after file counts.

    Notes
    -----
    The read path deliberately bypasses DuckDB / ``query_iceberg_df``
    and goes straight through PyIceberg's ``tbl.refresh().scan()``.
    The 2026-05-12 ``stocks.regime_history`` incident traced to a
    stale-read race: the classifier wrote a new row and called
    ``invalidate_metadata``, but a concurrent ``/algo/regime/current``
    request re-populated ``_meta_cache`` with the post-write metadata
    path *while* compaction was about to begin — and the cache races
    even further when ``cleanup_orphans_v2`` later commits its own
    expire-snapshots step. Result: ``query_iceberg_df`` returned the
    snapshot from the cached metadata file (one row short), the
    ``tbl.overwrite()`` below committed that short payload, and the
    just-written daily row was lost. Reading from the same ``tbl``
    object that performs the overwrite guarantees reader and writer
    see the same snapshot, which is the only invariant compaction
    actually needs.
    """
    table_dir = WAREHOUSE_DIR / table_name.replace(".", "/")
    before = _count_parquet_files(table_dir)

    _logger.info(
        "[maint] Compacting %s (%d parquet " "files before)",
        table_name,
        before,
    )

    # Smart-skip: if every partition is already at ~1 parquet,
    # rewriting them is wasted I/O and exposes us to
    # PyIceberg's overwrite-conflict failure mode under
    # concurrent writers. The 2026-05-14 incident burned 6h
    # rewriting an already-optimal stocks.intraday_bars before
    # the commit hit a branch-ref conflict. See
    # ``is_compaction_already_optimal`` for the threshold.
    if is_compaction_already_optimal(table_dir):
        files, partitions, avg = _avg_files_per_partition(
            table_dir,
        )
        _logger.info(
            "[maint] %s already optimal — files=%d "
            "partitions=%d avg=%.2f ≤ %.2f, "
            "skipping rewrite",
            table_name,
            files,
            partitions,
            avg,
            _OPTIMAL_FILES_PER_PARTITION,
        )
        return {
            "table": table_name,
            "before": before,
            "after": before,
            "skipped_optimal": True,
            "partitions": partitions,
            "avg_files_per_partition": avg,
        }

    if before > _MAX_SAFE_COMPACT_FILES:
        files, partitions, avg = _avg_files_per_partition(table_dir)
        _logger.warning(
            "[maint] %s has %d files (> %d safe limit) — "
            "skipping full-table-scan compaction to avoid "
            "freezing uvicorn. Fix the write pipeline to use "
            "overwrite() COW instead of delete()+append().",
            table_name,
            before,
            _MAX_SAFE_COMPACT_FILES,
        )
        return {
            "table": table_name,
            "before": before,
            "after": before,
            "skipped_too_large": True,
            "partitions": partitions,
            "avg_files_per_partition": avg,
        }

    files, partitions, avg = _avg_files_per_partition(table_dir)
    if partitions > 0 and avg > _MAX_AVG_FILES_PER_PARTITION:
        total_bytes = _table_data_bytes(table_dir)
        if total_bytes > _SMALL_TABLE_COMPACT_BYTES:
            _logger.warning(
                "[maint] %s has avg %.1f files/partition "
                "(> %d safe limit, %d files across %d partitions, "
                "%.0f MB) — manifest chain too deep; skipping "
                "compaction to avoid freezing uvicorn. Run "
                "retention first to reduce commit count.",
                table_name,
                avg,
                _MAX_AVG_FILES_PER_PARTITION,
                files,
                partitions,
                total_bytes / (1024 * 1024),
            )
            return {
                "table": table_name,
                "before": before,
                "after": before,
                "skipped_deep_manifest": True,
                "partitions": partitions,
                "avg_files_per_partition": avg,
            }
        _logger.info(
            "[maint] %s avg %.1f files/partition but only "
            "%.0f MB (≤ %.0f MB) — compacting in-process",
            table_name,
            avg,
            total_bytes / (1024 * 1024),
            _SMALL_TABLE_COMPACT_BYTES / (1024 * 1024),
        )

    # Byte ceiling — applies to ALL tables that reached here
    # (incl. low-avg ones the file-count guards let through).
    # compact_table reads the whole table into Arrow; a byte-heavy
    # table OOM-kills uvicorn regardless of file count. Skip with a
    # warning; needs batched per-partition compaction.
    safe_bytes = _table_data_bytes(table_dir)
    if safe_bytes > _MAX_SAFE_COMPACT_BYTES:
        files, partitions, avg = _avg_files_per_partition(table_dir)
        _logger.warning(
            "[maint] %s is %.0f MB (> %d MB in-process limit, "
            "%d files / %d partitions) — skipping full-table-scan "
            "compaction to avoid OOM; needs batched per-partition "
            "compaction.",
            table_name,
            safe_bytes / (1024 * 1024),
            _MAX_SAFE_COMPACT_BYTES // (1024 * 1024),
            files,
            partitions,
        )
        return {
            "table": table_name,
            "before": before,
            "after": before,
            "skipped_too_large_bytes": True,
            "partitions": partitions,
            "avg_files_per_partition": avg,
        }

    t0 = time.monotonic()

    from tools._stock_shared import _require_repo

    try:
        repo = _require_repo()
        tbl = repo.load_table(table_name)
        # tbl.refresh() forces the in-memory metadata view to match
        # the catalog pointer — protects against the (rare but real)
        # case where another thread's commit landed between
        # load_table() and scan().
        tbl.refresh()
        arrow = tbl.scan().to_arrow()
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

    rows = arrow.num_rows
    if rows == 0:
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

    # Align Arrow nullability with the Iceberg schema's
    # required/optional flags. ``tbl.scan().to_arrow()`` already
    # carries the iceberg schema's nullability, but we re-cast
    # defensively in case PyIceberg's projection ever loosens a
    # column to nullable — the historical bug ("Mismatch in fields:
    # bar_date required vs optional") would otherwise resurface on
    # ``overwrite()``. Affects every table with a NOT-NULL column
    # — e.g. ``stocks.regime_history``, ``stocks.daily_factors``,
    # ``stocks.regime_hmm_state``.
    try:
        target_arrow_schema = tbl.schema().as_arrow()
        arrow = arrow.cast(target_arrow_schema)
    except Exception:
        _logger.warning(
            "[maint] Arrow schema cast failed for %s — "
            "proceeding with default Arrow schema",
            table_name,
            exc_info=True,
        )

    # Use overwrite to replace all data in one
    # commit — produces 1 file per partition
    try:
        tbl.overwrite(arrow)
        # Invalidate DuckDB cache so subsequent readers (insights,
        # endpoints) see the post-compact file set.
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


# Threshold for considering a table "already optimal" — average
# parquet files per partition. 1.0 = exactly one file per
# partition (the ideal); 1.5 leaves a small margin for in-flight
# writes from a parallel keeper that committed since the last
# compaction. Above this, compaction does net work; at or below,
# rewriting all files produces zero improvement and risks the
# atomic-overwrite-conflict failure mode (see CLAUDE.md §6.4 /
# the 2026-05-14 stocks.intraday_bars overwrite-failed incident).
_OPTIMAL_FILES_PER_PARTITION = 1.5

# Full-table scan compaction reads ALL parquet files into an
# in-process Arrow table before the overwrite. Above ~40k files
# this seizes uvicorn for 10+ minutes (64k-file incident on
# stocks.intraday_features, 2026-06-17). Tables above this
# threshold are skipped with a warning — the write pipeline
# should use overwrite() COW instead of delete()+append().
_MAX_SAFE_COMPACT_FILES = 40_000

# Even with a low total file count, a table with very high avg
# files/partition has a proportionally deep manifest chain (one
# manifest .avro per commit × avg per partition). algo.events
# froze uvicorn (00:08–00:26 IST 2026-06-18) with only 4,900
# parquet files but avg=700/partition → 4,900 manifest reads
# × ~100 KB each = 500 MB manifest I/O just to enumerate data.
# Threshold=50 skips algo.events (avg=700) while allowing all
# legitimate tables (ohlcv, forecasts, sentiment all avg < 5).
_MAX_AVG_FILES_PER_PARTITION = 50
# A table this small (total parquet bytes) is always safe to
# compact in-process regardless of files/partition — reading it
# into Arrow can't OOM. Lets algo.events (~70 MB) and
# nse_delivery self-compact despite a high avg files/partition,
# while genuinely large fragmented tables still defer.
_SMALL_TABLE_COMPACT_BYTES = 512 * 1024 * 1024

# Hard upper ceiling: compaction reads the WHOLE table into an
# in-process Arrow table (scan().to_arrow()) then overwrite()s.
# Above this byte size that OOM-kills uvicorn even when the file
# count is low — the 2026-06-21 incident: stocks.intraday_features
# (1.2 GB / 70M rows, avg ~1.6 files/partition) passed every
# file-count guard and OOM-killed the backend. Skip these; they
# need batched per-partition compaction (ASETPLTFRM follow-up).
_MAX_SAFE_COMPACT_BYTES = 1024 * 1024 * 1024


def _avg_files_per_partition(
    table_dir: Path,
) -> tuple[int, int, float]:
    """Return ``(files, partitions, avg)`` for the table dir.

    A "partition" is any leaf directory containing at least one
    ``.parquet`` file. For un-partitioned tables, the data dir
    itself counts as one partition. ``avg = files / partitions``;
    returns ``(0, 0, 0.0)`` for an empty / missing table.
    """
    if not table_dir.exists():
        return 0, 0, 0.0
    partition_dirs: set[Path] = set()
    files = 0
    for parquet in table_dir.rglob("*.parquet"):
        files += 1
        partition_dirs.add(parquet.parent)
    partitions = len(partition_dirs)
    if partitions == 0:
        return 0, 0, 0.0
    return files, partitions, files / partitions


def _table_data_bytes(table_dir: Path) -> int:
    """Total bytes of all ``*.parquet`` under the table dir."""
    if not table_dir.exists():
        return 0
    return sum(
        p.stat().st_size for p in table_dir.rglob("*.parquet")
    )


def _bar_month_to_year_month(bar_month: int) -> str:
    """MonthTransform value (months since 1970-01) → 'YYYY-MM'."""
    year = 1970 + bar_month // 12
    month = bar_month % 12 + 1
    return f"{year}-{month:02d}"


def is_compaction_already_optimal(
    table_dir: Path,
) -> bool:
    """True iff the table's partitions are already one-file-per
    (or close enough — see ``_OPTIMAL_FILES_PER_PARTITION``).

    Skipping compaction in this state avoids the
    rewrite-22k-files-into-22k-new-files pathology that triggered
    the 2026-05-14 incident: the table was already
    one-file-per-partition after a fresh backfill, but
    ``compact_table`` re-read every parquet and tried to atomic-
    overwrite the whole table, exposing the long-running write
    to PyIceberg's concurrent-writer-conflict failure mode.

    The fix is conservative: skip only when the layout is
    demonstrably optimal. Empty tables are NOT optimal (callers
    handle that branch separately so the skip-log doesn't
    double-fire with the empty-table log).
    """
    files, partitions, avg = _avg_files_per_partition(table_dir)
    if partitions == 0:
        return False
    return avg <= _OPTIMAL_FILES_PER_PARTITION


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


def _snapshots_to_expire(
    snapshots: list,
    retain_count: int,
    min_age_ms: int,
    now_ms: int,
) -> list[int]:
    """Snapshot ids safe to expire.

    Keeps the latest ``retain_count`` snapshots by timestamp AND
    every snapshot younger than ``min_age_ms`` — so the sweep never
    deletes files for a snapshot recent enough to still sit in a
    daily reader's metadata cache (ASETPLTFRM-429). Returns the
    complement (oldest snapshots beyond both floors).

    Args:
        snapshots: PyIceberg ``Snapshot`` objects (need
            ``snapshot_id`` + ``timestamp_ms``).
        retain_count: count floor — newest N always kept.
        min_age_ms: age floor — anything younger is kept.
        now_ms: current epoch in ms (caller-supplied for testability).
    """
    ordered = sorted(
        snapshots,
        key=lambda s: s.timestamp_ms,
        reverse=True,
    )
    keep = {s.snapshot_id for s in ordered[:retain_count]}
    cutoff_ms = now_ms - min_age_ms
    for s in ordered:
        if s.timestamp_ms >= cutoff_ms:
            keep.add(s.snapshot_id)
    return [
        s.snapshot_id for s in ordered if s.snapshot_id not in keep
    ]


def cleanup_orphans_v2(
    table_name: str,
    *,
    retain_snapshots: int = SNAPSHOT_KEEP,
    retain_snapshot_min_age_hours: int = SNAPSHOT_MIN_AGE_HOURS,
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
       ``retain_snapshots`` by ``timestamp_ms`` AND every
       snapshot younger than ``retain_snapshot_min_age_hours``
       (ASETPLTFRM-429: never delete files for a snapshot recent
       enough to still be in a daily reader's metadata cache).
    2. Build the Iceberg-authoritative referenced set =
       ``inspect.all_files()`` ∪ ``inspect.all_manifests()``.
    3. Add the catalog's current ``metadata_location``
       pointer (the file PyIceberg loads on open).
    4. Add the last ``max(retain_snapshots, kept) + 5``
       metadata.json files in the chain so a recent ``UPDATE
       metadata_location`` rollback is still possible — and so we
       never retain *more* metadata.json files than live snapshots
       (which would leave "poison" metadata pointing at expired
       snapshots).
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
        retain_snapshots: latest N snapshots to keep (count floor).
        retain_snapshot_min_age_hours: never expire a snapshot
            younger than this (age floor). Guards against
            count-floor-only expiry deleting hours-old snapshots
            still referenced by reader caches (ASETPLTFRM-429).
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

    # Step 1 — expire old snapshots, keep latest N + anything
    # younger than the age floor (ASETPLTFRM-429).
    snapshots = sorted(
        list(tbl.metadata.snapshots),
        key=lambda s: s.timestamp_ms,
        reverse=True,
    )
    now_ms = int(time.time() * 1000)
    min_age_ms = retain_snapshot_min_age_hours * 3600 * 1000
    expire_ids = _snapshots_to_expire(
        snapshots,
        retain_snapshots,
        min_age_ms,
        now_ms,
    )
    expire_set = set(expire_ids)
    keep_ids = {
        s.snapshot_id
        for s in snapshots
        if s.snapshot_id not in expire_set
    }
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

    # Step 4 — recent metadata.json files in chain. Keep at least
    # as many as live snapshots (+5 rollback buffer) so we never
    # retain "poison" metadata.json pointing at expired snapshots.
    metadata_dir = table_dir / "metadata"
    if metadata_dir.exists():
        chain = sorted(
            metadata_dir.glob("*.metadata.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        meta_keep = max(retain_snapshots, len(keep_ids)) + 5
        for p in chain[:meta_keep]:
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

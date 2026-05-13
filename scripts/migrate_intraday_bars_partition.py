"""One-shot migration: re-partition ``stocks.intraday_bars``
from ``(ticker, bar_date)`` → ``(ticker, year_month)``
(ASETPLTFRM-400 slice 1i).

Why
---
The original partition scheme cut each ticker's history into
one parquet per day (~5 KB per file, ~450 k files for
4 yr × 500 tickers). Backtest reads — typically multi-month
single-ticker scans — spent more time on file opens than on
parquet reads.

The new ``(ticker, year_month)`` scheme folds each ticker's
month into one parquet (~150 KB) → ~18× fewer files,
proportional speed-up for the typical backtest scan pattern.

Algorithm
---------
1. **Fail-closed backup** of the existing table via
   ``backup_table`` (slice 1h). If backup fails, abort
   without touching catalog state.
2. **Drop** the existing ``stocks.intraday_bars`` table from
   the catalog. (Files remain on disk per the catalog drop
   semantics; the backup is the rollback path.)
3. **Re-create** the table with the new 12-column schema
   (``year_month`` added) and ``(ticker, year_month)``
   partition spec.
4. **Stream** rows ticker-by-ticker from the backup catalog
   into the new table. Tickers are grouped into batches
   (``--batch-tickers``, default 50) and each batch is one
   Iceberg commit — keeps the metadata.json chain short so
   the ``load_table`` cost per commit doesn't grow linearly.
5. **Verify** the row count of the new table matches the
   pre-migration count read from the backup. Fail loud if
   not.

Idempotency
-----------
The migration assumes the existing table has the old
schema. Re-running after a successful migration will detect
the new ``year_month`` column and exit early (no-op).

Usage
-----
::

    PYTHONPATH=.:backend python scripts/migrate_intraday_bars_partition.py \
        [--dry-run] [--backup-only]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.db.duckdb_engine import _create_view  # noqa: E402
from backend.db.duckdb_engine import (
    get_connection,
    invalidate_metadata,
    query_iceberg_table,
)
from backend.maintenance.backup import backup_table

_logger = logging.getLogger(__name__)
TABLE_ID = "stocks.intraday_bars"


def _is_already_migrated() -> bool:
    """True if the live table already has ``year_month`` —
    means a prior migration ran. Re-running is a no-op."""
    from stocks.create_tables import _get_catalog

    cat = _get_catalog()
    tbl = cat.load_table(TABLE_ID)
    field_names = {f.name for f in tbl.schema().fields}
    return "year_month" in field_names


def _read_distinct_tickers() -> list[str]:
    rows = query_iceberg_table(
        TABLE_ID,
        "SELECT DISTINCT ticker FROM intraday_bars ORDER BY ticker",
        [],
    )
    return [r["ticker"] for r in rows or []]


def _read_pre_count() -> int:
    rows = query_iceberg_table(
        TABLE_ID,
        "SELECT COUNT(*) AS c FROM intraday_bars",
        [],
    )
    return int(rows[0]["c"]) if rows else 0


def _read_ticker_rows(ticker: str) -> list[dict[str, Any]]:
    return query_iceberg_table(
        TABLE_ID,
        "SELECT ticker, bar_date, interval_sec, bar_open_ts_ns, "
        "open, high, low, close, volume, written_at, source "
        "FROM intraday_bars WHERE ticker = ? "
        "ORDER BY bar_open_ts_ns",
        [ticker],
    )


def _bulk_read_all_as_arrow() -> pa.Table:
    """Single DuckDB query → entire table as Arrow.

    Bypasses the per-ticker scan that was the bottleneck of
    the original migration (DuckDB's iceberg_scan + per-row
    predicate doesn't partition-prune by ticker so each query
    scanned all 11M rows). One query, ~5-15 sec, ~900 MB
    Arrow table in memory.

    The Arrow result has the OLD 11-column schema. The caller
    is responsible for appending ``year_month`` and casting to
    the new 12-column ``required=True`` schema.
    """
    conn = get_connection()
    try:
        _create_view(conn, TABLE_ID)
        return conn.execute(
            "SELECT ticker, bar_date, interval_sec, "
            "  bar_open_ts_ns, open, high, low, close, "
            "  volume, written_at, source "
            "FROM intraday_bars "
            "ORDER BY ticker, bar_open_ts_ns",
        ).fetch_arrow_table()
    finally:
        conn.close()


def _enrich_with_year_month_and_cast(
    arrow_tbl: pa.Table,
) -> pa.Table:
    """Append ``year_month = bar_date[:7]`` and cast to the
    new 12-column schema (``nullable=False`` on every column,
    matching ``required=True`` in the Iceberg schema)."""
    from backend.algo.backtest.intraday_backfill import _arrow_schema

    bar_date = arrow_tbl.column("bar_date")
    year_month = pc.utf8_slice_codeunits(bar_date, 0, 7)
    enriched = arrow_tbl.append_column("year_month", year_month)
    # Build target schema, then cast column-by-column so type
    # mismatches surface fail-loud rather than via opaque
    # PyIceberg append errors.
    target = _arrow_schema()
    cols = {}
    for name in target.names:
        src = enriched.column(name)
        cols[name] = src.cast(target.field(name).type)
    return pa.table(cols, schema=target)


def _arrow_table_for_ticker(
    ticker_rows: list[dict[str, Any]],
) -> pa.Table:
    """Build the Arrow table for one ticker's rows with
    ``year_month`` populated and the new schema's
    ``nullable=False`` constraints."""
    from backend.algo.backtest.intraday_backfill import _arrow_schema

    schema = _arrow_schema()
    cols: dict[str, list[Any]] = {name: [] for name in schema.names}
    for r in ticker_rows:
        bar_date = r["bar_date"]
        if isinstance(bar_date, str):
            bar_date_str = bar_date
        else:  # date object
            bar_date_str = bar_date.isoformat()
        written_at = r["written_at"]
        if isinstance(written_at, datetime):
            wa = written_at.replace(tzinfo=None)
        elif isinstance(written_at, str):
            wa = datetime.fromisoformat(
                written_at.replace("Z", ""),
            ).replace(tzinfo=None)
        else:
            wa = datetime.now(timezone.utc).replace(tzinfo=None)
        cols["ticker"].append(r["ticker"])
        cols["bar_date"].append(bar_date_str)
        cols["interval_sec"].append(int(r["interval_sec"]))
        cols["bar_open_ts_ns"].append(int(r["bar_open_ts_ns"]))
        cols["open"].append(float(r["open"]))
        cols["high"].append(float(r["high"]))
        cols["low"].append(float(r["low"]))
        cols["close"].append(float(r["close"]))
        cols["volume"].append(int(r["volume"]))
        cols["written_at"].append(wa)
        cols["source"].append(r.get("source") or "manual_backfill")
        cols["year_month"].append(bar_date_str[:7])
    return pa.table(cols, schema=schema)


def _drop_existing_table() -> None:
    from stocks.create_tables import _get_catalog

    cat = _get_catalog()
    _logger.info("Dropping %s from catalog", TABLE_ID)
    cat.drop_table(TABLE_ID)


def _create_new_table() -> None:
    """Re-create with the new schema + partition spec."""
    from stocks.create_tables import (
        _create_table,
        _get_catalog,
        _intraday_bars_schema,
        _ticker_year_month_partition_spec,
    )

    cat = _get_catalog()
    schema = _intraday_bars_schema()
    _create_table(
        cat,
        TABLE_ID,
        schema,
        _ticker_year_month_partition_spec(schema),
    )


def _write_ticker(
    arrow_tbl: pa.Table,
) -> None:
    from stocks.create_tables import _get_catalog

    def _do() -> None:
        cat = _get_catalog()
        tbl = cat.load_table(TABLE_ID)
        tbl.append(arrow_tbl)

    retry_iceberg_op(TABLE_ID, _do)


def _final_count() -> int:
    invalidate_metadata(TABLE_ID)
    rows = query_iceberg_table(
        TABLE_ID,
        "SELECT COUNT(*) AS c FROM intraday_bars",
        [],
    )
    return int(rows[0]["c"]) if rows else 0


def run_migration(
    *,
    dry_run: bool = False,
    backup_only: bool = False,
    batch_tickers: int = 50,
    skip_backup: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    if _is_already_migrated() and not force:
        _logger.info(
            "Migration is a no-op: ``year_month`` column "
            "already present on %s (pass --force to override)",
            TABLE_ID,
        )
        return {"status": "noop_already_migrated"}

    t0 = time.monotonic()
    pre_count = _read_pre_count()
    tickers = _read_distinct_tickers()
    _logger.info(
        "Pre-migration: %s has %d rows across %d tickers",
        TABLE_ID,
        pre_count,
        len(tickers),
    )

    if skip_backup:
        backup_path = "skipped"
        _logger.info(
            "skip_backup=True — relying on prior backup at "
            "backup-<today>-stocks-intraday_bars",
        )
    else:
        _logger.info("Backing up %s (fail-closed)…", TABLE_ID)
        backup_path = backup_table(TABLE_ID)
        _logger.info("Backup complete: %s", backup_path)
    if backup_only:
        return {
            "status": "backup_only",
            "backup_path": backup_path,
            "pre_count": pre_count,
            "tickers": len(tickers),
        }
    if dry_run:
        return {
            "status": "dry_run",
            "backup_path": backup_path,
            "pre_count": pre_count,
            "tickers": len(tickers),
        }

    # Bulk-read the entire table into Arrow BEFORE dropping
    # the old metadata. Per-ticker reads hit a DuckDB
    # full-table scan each time (no partition pruning at the
    # iceberg_scan layer), making them ~30s/ticker. One bulk
    # read is ~5-15s for the full 11M rows.
    _logger.info(
        "Bulk-reading entire table into Arrow…",
    )
    t_read = time.monotonic()
    raw = _bulk_read_all_as_arrow()
    _logger.info(
        "Bulk read complete: %d rows in %.1fs (Arrow size " "≈ %.0f MB)",
        raw.num_rows,
        time.monotonic() - t_read,
        raw.nbytes / (1024 * 1024),
    )
    if raw.num_rows != pre_count:
        return {
            "status": "bulk_read_count_mismatch",
            "pre_count": pre_count,
            "bulk_count": raw.num_rows,
            "backup_path": backup_path,
        }

    # Add ``year_month`` column + cast to new schema.
    t_enrich = time.monotonic()
    enriched = _enrich_with_year_month_and_cast(raw)
    _logger.info(
        "Schema enrichment complete: %d cols in %.1fs",
        len(enriched.schema.names),
        time.monotonic() - t_enrich,
    )
    # Free the raw 11-col arrow table — we don't need both
    # in memory once cast.
    del raw

    _drop_existing_table()
    invalidate_metadata(TABLE_ID)
    _create_new_table()

    # Single PyIceberg append — partitions automatically
    # split by (ticker, year_month) per the new partition
    # spec, producing ~24k parquet files in one commit.
    t_write = time.monotonic()
    _write_ticker(enriched)
    written_total = enriched.num_rows
    _logger.info(
        "Bulk append complete: %d rows in %.1fs",
        written_total,
        time.monotonic() - t_write,
    )

    invalidate_metadata(TABLE_ID)
    post_count = _final_count()
    elapsed = time.monotonic() - t0
    _logger.info(
        "Migration complete: pre=%d post=%d elapsed=%.1fs",
        pre_count,
        post_count,
        elapsed,
    )
    if post_count != pre_count:
        return {
            "status": "row_count_mismatch",
            "pre_count": pre_count,
            "post_count": post_count,
            "backup_path": backup_path,
            "elapsed_s": round(elapsed, 1),
        }
    return {
        "status": "ok",
        "pre_count": pre_count,
        "post_count": post_count,
        "tickers": len(tickers),
        "backup_path": backup_path,
        "elapsed_s": round(elapsed, 1),
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="migrate_intraday_bars_partition",
        description=(
            "Re-partition stocks.intraday_bars from "
            "(ticker, bar_date) to (ticker, year_month). "
            "ASETPLTFRM-400 slice 1i."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Take the backup and report counts; do NOT drop "
            "or rewrite the table."
        ),
    )
    p.add_argument(
        "--backup-only",
        action="store_true",
        help=(
            "Synonym for --dry-run; runs only the backup step "
            "for separate verification."
        ),
    )
    p.add_argument(
        "--batch-tickers",
        type=int,
        default=50,
        help=(
            "Tickers per Iceberg commit (default 50). Larger "
            "batches → fewer commits → less metadata.json "
            "chain growth → faster overall."
        ),
    )
    p.add_argument(
        "--skip-backup",
        action="store_true",
        help=(
            "Skip the in-script backup step. Use ONLY when a "
            "prior backup is already in place (e.g. after a "
            "manual restore + re-run cycle)."
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Re-run even when the catalog already shows the "
            "new ``year_month`` column. Used to recover from "
            "a partial migration where the catalog points to "
            "an empty post-drop table but the on-disk "
            "metadata still has the source data."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"),
    )
    args = _build_arg_parser().parse_args(argv)
    result = run_migration(
        dry_run=args.dry_run,
        backup_only=args.backup_only,
        batch_tickers=args.batch_tickers,
        skip_backup=args.skip_backup,
        force=args.force,
    )
    _logger.info("Result: %s", result)
    return (
        0
        if result.get("status")
        in (
            "ok",
            "noop_already_migrated",
            "dry_run",
            "backup_only",
        )
        else 1
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

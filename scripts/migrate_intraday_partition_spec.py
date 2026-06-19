"""One-time rebuild of the three EOD intraday Iceberg tables onto the
``BucketTransform(16, ticker) + MonthTransform(bar_date_d)`` spec.

Lossless: reads every row from the legacy table, derives ``bar_date_d``
from the ``bar_date`` string, writes into a ``_v2`` table with the new
spec (chunked by ``year_month`` to bound memory), verifies row-count
parity, then atomically renames ``_v2`` into the canonical name. The old
catalog entry is dropped (NOT purged) so its now-orphan files are
reclaimed later by ``cleanup_orphans_v2()`` — never ``rm`` (CLAUDE.md
§4.3 #20).

Run INSIDE the backend container AFTER deploying the Task 1-4 code and
BEFORE the next EOD write::

    docker compose exec -T backend \\
        python -m scripts.migrate_intraday_partition_spec

This script mutates production tables. Task 5 only authors + unit-tests
it; the actual run against live tables is Task 6 (gated on human
approval).
"""

from __future__ import annotations

import logging
from datetime import date

import pyarrow as pa
from pyiceberg.expressions import EqualTo

from backend.maintenance.backup import backup_table
from stocks.create_tables import (
    _get_catalog,
    _index_intraday_bars_schema,
    _index_intraday_bars_sort_order,
    _intraday_bars_schema,
    _intraday_bars_sort_order,
    _intraday_features_schema,
    _intraday_features_sort_order,
    _ticker_bucket_month_partition_spec,
)

_logger = logging.getLogger(__name__)

_TABLES = [
    (
        "stocks.intraday_bars",
        _intraday_bars_schema,
        _intraday_bars_sort_order,
    ),
    (
        "stocks.index_intraday_bars",
        _index_intraday_bars_schema,
        _index_intraday_bars_sort_order,
    ),
    (
        "stocks.intraday_features",
        _intraday_features_schema,
        _intraday_features_sort_order,
    ),
]


def _add_bar_date_d(arrow_tbl: pa.Table) -> pa.Table:
    """Append a ``bar_date_d`` date32 column derived from the existing
    ``bar_date`` YYYY-MM-DD string column.

    Preserves every original row and column; only the new
    (non-nullable) ``bar_date_d`` field is appended at the end.
    """
    bar_dates = [
        date.fromisoformat(s)
        for s in arrow_tbl.column("bar_date").to_pylist()
    ]
    arr = pa.array(bar_dates, type=pa.date32())
    return arrow_tbl.append_column(
        pa.field("bar_date_d", pa.date32(), nullable=False), arr
    )


def migrate_table(catalog, canonical, schema_fn, sort_order_fn) -> dict:
    """Rebuild ``canonical`` onto the new partition spec via a ``_v2``
    table, verify row-count parity, then atomically rename-swap.

    On a row-count mismatch this RAISES before any rename, leaving the
    legacy table untouched and ``_v2`` in place for inspection — no
    data loss.

    Args:
        catalog: An open ``SqlCatalog``.
        canonical: ``namespace.table`` string identifier
            (e.g. ``"stocks.intraday_bars"``).
        schema_fn: Returns the new (``bar_date_d``-bearing) Schema.
        sort_order_fn: Takes the schema, returns its SortOrder.

    Returns:
        ``{"old_rows": int, "new_rows": int, "swapped": bool}``.
    """
    v2 = f"{canonical}_v2"
    old = f"{canonical}_old"
    schema = schema_fn()
    spec = _ticker_bucket_month_partition_spec(schema)
    sort_order = sort_order_fn(schema)

    src = catalog.load_table(canonical)
    old_rows = src.scan().to_arrow().num_rows
    _logger.info("[migrate] %s: %d rows to copy", canonical, old_rows)

    # Clean any aborted prior run before recreating _v2.
    try:
        catalog.drop_table(v2)
    except Exception:  # noqa: BLE001 - table simply may not exist
        pass

    catalog.create_table(
        identifier=v2,
        schema=schema,
        partition_spec=spec,
        sort_order=sort_order,
    )
    v2_tbl = catalog.load_table(v2)
    # Target arrow schema (includes bar_date_d) from the _v2 table's
    # own Iceberg schema — confirmed present in PyIceberg 0.11.1.
    target_arrow_schema = v2_tbl.schema().as_arrow()

    # Chunk the copy by year_month to bound peak memory: each month is
    # read, augmented, cast, and appended independently.
    months = sorted(
        set(
            src.scan(selected_fields=("year_month",))
            .to_arrow()
            .column("year_month")
            .to_pylist()
        )
    )

    new_rows = 0
    for ym in months:
        chunk = src.scan(row_filter=EqualTo("year_month", ym)).to_arrow()
        if chunk.num_rows == 0:
            continue
        chunk = _add_bar_date_d(chunk)
        # ``Table.cast`` matches fields *positionally* and errors on a
        # name-order mismatch, so reorder by name first (the legacy
        # table's column order need not match the _v2 schema), then
        # cast to normalise types (e.g. tz-naive timestamp, date32).
        chunk = chunk.select(target_arrow_schema.names).cast(
            target_arrow_schema
        )
        v2_tbl.append(chunk)
        new_rows += chunk.num_rows
        _logger.info(
            "[migrate] %s %s: +%d rows", canonical, ym, chunk.num_rows
        )

    if new_rows != old_rows:
        raise RuntimeError(
            f"[migrate] {canonical} row mismatch: old={old_rows} "
            f"new={new_rows} — aborting swap, _v2 left for inspection"
        )

    # Atomic swap: rename legacy out, rename _v2 in, drop legacy
    # catalog entry (files NOT purged — swept later by
    # cleanup_orphans_v2 per CLAUDE.md §4.3 #20).
    catalog.rename_table(canonical, old)
    catalog.rename_table(v2, canonical)
    catalog.drop_table(old)
    _logger.info("[migrate] %s: swapped, %d rows", canonical, new_rows)
    return {"old_rows": old_rows, "new_rows": new_rows, "swapped": True}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    catalog = _get_catalog()
    for canonical, schema_fn, sort_order_fn in _TABLES:
        # Fail-closed step 0: a per-table backup MUST succeed before we
        # touch the table (CLAUDE.md §6.4).
        _logger.info("[migrate] backing up %s", canonical)
        backup_table(canonical)
        res = migrate_table(catalog, canonical, schema_fn, sort_order_fn)
        _logger.info("[migrate] %s done: %s", canonical, res)


if __name__ == "__main__":
    main()

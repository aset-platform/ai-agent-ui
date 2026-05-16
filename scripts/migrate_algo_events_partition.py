"""One-shot migration: nuke + rebuild ``algo.events`` with the
optimal partition spec defined in
``backend/algo/iceberg_init.py``.

WHY
---
Per CLAUDE.md §4.3 #22 universal rule + the 2026-05-16 redesign:

* ``ts_date`` is moving from ``StringType("YYYY-MM-DD")`` to
  ``DateType`` so the partition spec can use ``MonthTransform``
  (which can't operate on a string column).
* Partition is changing from
  ``IdentityTransform(mode) + IdentityTransform(ts_date)``
  (unbounded date partition growth) to
  ``IdentityTransform(mode) + MonthTransform(ts_date)``
  (bounded at 7 modes × 12 months/year = 84 partitions).
* A SortOrder of ``(ts_ns, type)`` is added so compaction
  colocates events by time within each partition.

Type evolution from String to Date is NOT a supported in-place
Iceberg schema evolution path — drop + recreate is the only
mechanism.  This is acceptable here because ~96 % of the
existing 438k rows are backtest events that the new weekly
retention job would purge after 7 days anyway, and we're about
to enable that retention policy.

OPERATION
---------
1. Audit current row count.
2. ``catalog.drop_table('algo.events')`` — IRREVERSIBLE.
3. ``create_algo_tables()`` re-creates the table with the new
   schema + partition + sort order.

This script does NOT preserve any history.  If you need to
keep the live-mode placed-on-Zerodha rows, dump them with
DuckDB first (see "Optional pre-dump" below).

Run inside the backend container:

    docker compose exec backend python \\
        scripts/migrate_algo_events_partition.py

Author: Abhay Singh (2026-05-16, ASETPLTFRM hotfix for the
iceberg-table-design-checklist universal rule).
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_logger = logging.getLogger(__name__)


def main() -> int:
    from backend.algo.iceberg_init import create_algo_tables
    from stocks.create_tables import _get_catalog

    catalog = _get_catalog()
    try:
        tbl = catalog.load_table("algo.events")
        tbl.refresh()
        plans = list(tbl.scan().plan_files())
        rows = sum(p.file.record_count for p in plans)
        files = len(plans)
        _logger.warning(
            "About to drop algo.events: %s rows in %s files. "
            "This is IRREVERSIBLE.",
            f"{rows:,}", f"{files:,}",
        )
    except Exception as exc:  # noqa: BLE001
        _logger.info(
            "algo.events not loadable (likely already dropped "
            "or never created): %s", exc,
        )
    else:
        _logger.info("dropping algo.events ...")
        catalog.drop_table("algo.events")

    _logger.info("recreating algo.events with new schema ...")
    create_algo_tables()

    tbl = catalog.load_table("algo.events")
    spec = tbl.spec()
    sort = tbl.sort_order()
    _logger.info("NEW partition spec:")
    for f in spec.fields:
        _logger.info("  - %s: %s", f.name, f.transform)
    _logger.info("NEW sort order: %s", sort)
    _logger.info(
        "NEW ts_date type: %s",
        tbl.schema().find_field("ts_date").field_type,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

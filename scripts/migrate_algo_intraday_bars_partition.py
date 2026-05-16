"""One-shot migration: drop + recreate ``algo.intraday_bars``
with the optimal partition spec.

Safe to run — refuses if the table has any rows.  Used on
2026-05-16 to apply the universal Iceberg table design rule
(CLAUDE.md §4.3 #22) to ``algo.intraday_bars``: switch
``bar_date`` from ``StringType`` to ``DateType`` and the
partition spec from
``IdentityTransform(ticker) + IdentityTransform(bar_date)``
(the same identity-on-ticker pattern that nuked
``stocks.nse_delivery`` on 2026-05-15) to
``BucketTransform(16, ticker) + MonthTransform(bar_date)``
plus a ``SortOrder(ticker, bar_open_ts_ns)``.

Run inside the backend container:

    docker compose exec backend python \\
        scripts/migrate_algo_intraday_bars_partition.py
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
        tbl = catalog.load_table("algo.intraday_bars")
    except Exception:
        _logger.info("algo.intraday_bars not found — creating fresh")
        create_algo_tables()
        return 0

    tbl.refresh()
    plans = list(tbl.scan().plan_files())
    rows = sum(p.file.record_count for p in plans)
    _logger.info(
        "current state: %d active files / %d rows",
        len(plans), rows,
    )
    if rows > 0:
        _logger.error(
            "ABORT: table has data — won't drop. "
            "Use the algo.events nuke-rebuild pattern from "
            "``scripts/migrate_algo_events_partition.py`` if "
            "you need to redesign a non-empty table."
        )
        return 2

    _logger.info("dropping empty table ...")
    catalog.drop_table("algo.intraday_bars")
    _logger.info("recreating with new schema ...")
    create_algo_tables()

    tbl = catalog.load_table("algo.intraday_bars")
    spec = tbl.spec()
    sort = tbl.sort_order()
    _logger.info("NEW partition spec:")
    for f in spec.fields:
        _logger.info("  - %s: %s", f.name, f.transform)
    _logger.info("NEW sort order: %s", sort)
    _logger.info(
        "NEW bar_date type: %s",
        tbl.schema().find_field("bar_date").field_type,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

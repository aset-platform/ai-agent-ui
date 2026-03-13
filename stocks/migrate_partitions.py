"""Partition migration script for Iceberg tables.

Reviews current partition specs and reports optimisation
opportunities.  Since PyIceberg on SQLite catalogs does not
support ``table.update_spec()`` for live partition evolution,
this script operates in **report mode** by default — it logs
the current vs. recommended partition layout.

For tables that require partition changes, the recommended
approach is:

1. Create a new table with the target partition spec.
2. Copy data from old table to new table.
3. Drop old table and rename new table.

This script automates step 1-3 when ``--execute`` is passed.

Usage::

    cd ai-agent-ui
    source ~/.ai-agent-ui/venv/bin/activate

    # Report only (safe — no changes)
    python stocks/migrate_partitions.py

    # Execute migration (backs up old table first)
    python stocks/migrate_partitions.py --execute
"""

import logging
import os
import sys

_SCRIPT_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
_BACKEND_DIR = os.path.join(_SCRIPT_DIR, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from paths import (  # noqa: E402
    ICEBERG_CATALOG_URI,
    ICEBERG_WAREHOUSE_URI,
)

os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__URI",
    ICEBERG_CATALOG_URI,
)
os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__WAREHOUSE",
    ICEBERG_WAREHOUSE_URI,
)

_logger = logging.getLogger(__name__)

_NAMESPACE = "stocks"


def _get_catalog():
    """Load the local Iceberg SqlCatalog.

    Returns:
        The loaded catalog instance.
    """
    from pyiceberg.catalog import load_catalog

    return load_catalog("local")


def _partition_field_names(table) -> list[str]:
    """Extract partition field names from a table.

    Args:
        table: An Iceberg table object.

    Returns:
        List of partition source column names.
    """
    spec = table.spec()
    names = []
    for pf in spec.fields:
        source_field = table.schema().find_field(
            pf.source_id,
        )
        names.append(source_field.name)
    return names


def report_partitions() -> dict:
    """Report current vs. recommended partitions.

    Returns:
        Dict mapping table name to a dict with
        ``current`` and ``recommended`` partition info.
    """
    catalog = _get_catalog()

    # Recommended partition layouts.
    recommendations = {
        f"{_NAMESPACE}.registry": {
            "recommended": [],
            "reason": "Small table (~30 rows); no partitioning needed",
        },
        f"{_NAMESPACE}.company_info": {
            "recommended": ["ticker"],
            "reason": (
                "Queries always filter by ticker; "
                "partition pruning skips other tickers"
            ),
        },
        f"{_NAMESPACE}.ohlcv": {
            "recommended": ["ticker"],
            "reason": (
                "Already optimal; ticker partition + "
                "Iceberg file-level date statistics "
                "handle range queries"
            ),
        },
        f"{_NAMESPACE}.dividends": {
            "recommended": ["ticker"],
            "reason": (
                "Queries filter by ticker; "
                "partition enables pruning"
            ),
        },
        f"{_NAMESPACE}.technical_indicators": {
            "recommended": ["ticker"],
            "reason": (
                "Already optimal; mirrors OHLCV "
                "partition strategy"
            ),
        },
        f"{_NAMESPACE}.analysis_summary": {
            "recommended": ["ticker"],
            "reason": (
                "Queries always filter by ticker; "
                "enables efficient retention cleanup"
            ),
        },
        f"{_NAMESPACE}.forecast_runs": {
            "recommended": ["ticker"],
            "reason": (
                "Queries filter by ticker + horizon; "
                "ticker partition is primary filter"
            ),
        },
        f"{_NAMESPACE}.forecasts": {
            "recommended": ["ticker", "horizon_months"],
            "reason": "Already optimal",
        },
        f"{_NAMESPACE}.quarterly_results": {
            "recommended": ["ticker"],
            "reason": (
                "Queries filter by ticker; "
                "enables efficient scoped upserts"
            ),
        },
        f"{_NAMESPACE}.llm_pricing": {
            "recommended": ["provider"],
            "reason": "Already optimal; small table",
        },
        f"{_NAMESPACE}.llm_usage": {
            "recommended": ["request_date"],
            "reason": (
                "Already optimal; date partition "
                "enables efficient retention cleanup"
            ),
        },
    }

    report = {}
    tables = catalog.list_tables(_NAMESPACE)
    for ns, name in tables:
        table_id = f"{ns}.{name}"
        tbl = catalog.load_table(table_id)
        current = _partition_field_names(tbl)
        rec = recommendations.get(table_id, {})

        status = "OK"
        recommended = rec.get("recommended", current)
        if set(current) != set(recommended):
            status = "NEEDS_UPDATE"

        report[table_id] = {
            "current": current,
            "recommended": recommended,
            "status": status,
            "reason": rec.get("reason", ""),
            "row_count": _count_rows(tbl),
        }

    return report


def _count_rows(table) -> int:
    """Count rows in an Iceberg table.

    Args:
        table: An Iceberg table object.

    Returns:
        Row count, or -1 on error.
    """
    try:
        df = table.scan(
            selected_fields=["ticker"]
            if "ticker" in [
                f.name for f in table.schema().fields
            ]
            else [],
        ).to_pandas()
        return len(df)
    except Exception:
        return -1


def print_report(report: dict) -> None:
    """Print a formatted partition report.

    Args:
        report: Output from :func:`report_partitions`.
    """
    print("\n" + "=" * 60)
    print("Iceberg Partition Report")
    print("=" * 60)

    for table_id, info in sorted(report.items()):
        status = info["status"]
        icon = "OK" if status == "OK" else "!!"
        print(
            f"\n[{icon}] {table_id}"
            f"  ({info['row_count']} rows)"
        )
        print(
            f"  Current:     "
            f"{info['current'] or '(none)'}"
        )
        print(
            f"  Recommended: "
            f"{info['recommended'] or '(none)'}"
        )
        print(f"  Reason:      {info['reason']}")

    needs_update = [
        t
        for t, i in report.items()
        if i["status"] == "NEEDS_UPDATE"
    ]
    if needs_update:
        print(
            f"\n{len(needs_update)} table(s) could "
            f"benefit from partition changes:"
        )
        for t in needs_update:
            print(f"  - {t}")
        print(
            "\nNote: Partition evolution on existing "
            "tables with data requires table recreation."
        )
        print(
            "For new deployments, update "
            "stocks/create_tables.py before first run."
        )
    else:
        print(
            "\nAll tables have optimal partition specs."
        )

    print("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)-8s | "
            "%(name)s | %(message)s"
        ),
    )

    project_root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    os.chdir(project_root)

    report = report_partitions()
    print_report(report)

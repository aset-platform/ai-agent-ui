#!/usr/bin/env python
"""Rebuild Iceberg tables with missing data files.

Drops and recreates tables that have corrupted snapshots
(referencing deleted parquet files), then re-runs
create_tables to reinitialize them.

Usage::

    source ~/.ai-agent-ui/venv/bin/activate
    PYTHONPATH=backend python scripts/rebuild_tables.py
"""

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
_logger = logging.getLogger(__name__)

from pyiceberg.catalog import load_catalog


def main():
    catalog = load_catalog("local")
    tables = catalog.list_tables("stocks")

    # Test each table — if scan fails, it's corrupted.
    damaged = []
    healthy = []
    for ns, name in sorted(tables):
        fqn = f"{ns}.{name}"
        try:
            tbl = catalog.load_table(fqn)
            tbl.scan().to_pandas()
            healthy.append(fqn)
        except Exception:
            damaged.append(fqn)

    _logger.info(f"Healthy: {len(healthy)} tables")
    _logger.info(f"Damaged: {len(damaged)} tables")

    if not damaged:
        _logger.info("Nothing to rebuild.")
        return

    _logger.info(f"\nDamaged tables:")
    for t in damaged:
        _logger.info(f"  - {t}")

    _logger.info(f"\nDropping {len(damaged)} damaged tables...")
    for fqn in damaged:
        try:
            catalog.drop_table(fqn)
            _logger.info(f"  Dropped: {fqn}")
        except Exception as exc:
            _logger.info(f"  Drop failed {fqn}: {exc}")

    _logger.info("\nRecreating all tables...")
    import sys as _s
    _s.path.insert(0, "stocks")
    from create_tables import create_tables

    create_tables()
    _logger.info("Done. Tables recreated (empty).")

    _logger.info(
        "\nNext steps:"
        "\n  1. ./run.sh restart"
        "\n  2. PYTHONPATH=backend python "
        "scripts/seed_demo_data.py"
        "\n  3. Trigger full refresh from Admin UI"
    )


if __name__ == "__main__":
    sys.exit(main() or 0)

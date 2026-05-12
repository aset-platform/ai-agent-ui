"""Schema + idempotent registration for ``stocks.universe_snapshot``.

REGIME-7. Mirrors the pattern of
``backend/algo/regime/iceberg_init.py``. Registered into the
``stocks`` namespace via ``stocks/create_tables.py`` so the existing
init script picks it up.

One row per (rebalance_date, ticker). ``included_in_top_200`` flags
the survivorship-bias-free top-200 cohort by 60d ADTV; remaining
filtered tickers persisted with ``included_in_top_200=False`` so
follow-up filters / liquidity ratings can read them.
"""
from __future__ import annotations

from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import YearTransform
from pyiceberg.types import (
    BooleanType,
    DateType,
    DoubleType,
    NestedField,
    StringType,
)

UNIVERSE_SNAPSHOT_TABLE = "stocks.universe_snapshot"


def universe_snapshot_schema() -> Schema:
    """Schema for ``stocks.universe_snapshot``. ``rebalance_date``,
    ``ticker``, and ``included_in_top_200`` are required; the rest
    are nullable to tolerate missing market-cap / sector metadata
    gracefully (still emitted with ``included_in_top_200=False``).

    PR #2 (order-safety) adds ``liquidity_bucket`` +
    ``is_top100_mcap`` as nullable columns so live-order placement
    can look up a ticker-specific slippage cap.
    """
    return Schema(
        NestedField(1, "rebalance_date", DateType(), required=True),
        NestedField(2, "ticker", StringType(), required=True),
        NestedField(3, "adtv_inr_60d", DoubleType(), required=False),
        NestedField(4, "market_cap_inr", DoubleType(), required=False),
        NestedField(5, "sector", StringType(), required=False),
        NestedField(
            6, "included_in_top_200", BooleanType(), required=True,
        ),
        NestedField(
            7, "liquidity_bucket", StringType(), required=False,
        ),
        NestedField(
            8, "is_top100_mcap", BooleanType(), required=False,
        ),
    )


def evolve_universe_snapshot_buckets() -> None:
    """Add ``liquidity_bucket`` + ``is_top100_mcap`` to existing
    ``stocks.universe_snapshot`` if missing. Idempotent.

    PR #2 (order-safety). Run once after deploy; CLAUDE.md §5.1
    notes that Iceberg schema evolution requires a backend restart
    (DuckDB caches schema in-process) and a Redis FLUSHALL to clear
    cached snapshot reads.
    """
    import logging

    from stocks.create_tables import _get_catalog

    _logger = logging.getLogger(__name__)
    catalog = _get_catalog()
    tbl = catalog.load_table(UNIVERSE_SNAPSHOT_TABLE)
    existing = {f.name for f in tbl.schema().fields}
    to_add: list[tuple[str, object]] = []
    if "liquidity_bucket" not in existing:
        to_add.append(("liquidity_bucket", StringType()))
    if "is_top100_mcap" not in existing:
        to_add.append(("is_top100_mcap", BooleanType()))
    if not to_add:
        _logger.info(
            "universe_snapshot already has bucket columns — "
            "skipping evolution.",
        )
        return
    with tbl.update_schema() as update:
        for name, ftype in to_add:
            update.add_column(path=name, field_type=ftype)
    _logger.info(
        "Evolved universe_snapshot schema: added %s",
        [n for n, _t in to_add],
    )


def universe_snapshot_partition_spec() -> PartitionSpec:
    """Partition by ``year(rebalance_date)`` — keeps each year's
    snapshots colocated and prunes older years cheaply."""
    return PartitionSpec(
        PartitionField(
            source_id=1,
            field_id=1100,
            transform=YearTransform(),
            name="rebalance_date_year",
        )
    )


def register_tables() -> None:
    """Idempotent — creates ``stocks.universe_snapshot`` if absent.

    Re-uses the catalog + helper from ``stocks.create_tables``.
    """
    from stocks.create_tables import _create_table, _get_catalog

    catalog = _get_catalog()
    _create_table(
        catalog,
        UNIVERSE_SNAPSHOT_TABLE,
        universe_snapshot_schema(),
        universe_snapshot_partition_spec(),
    )

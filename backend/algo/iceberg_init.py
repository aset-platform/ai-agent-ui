"""One-time Iceberg table init for the ``algo`` namespace.

Creates the ``algo.events`` append-only event log table.
Idempotent — if the namespace and table exist, returns
silently. Mirrors ``stocks/create_tables.py:create_tables()``
pattern but scoped to the algo module.
"""
from __future__ import annotations

import logging

from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import (
    LongType,
    NestedField,
    StringType,
    TimestampType,
)

from stocks.create_tables import _create_table, _get_catalog

_logger = logging.getLogger(__name__)

_NAMESPACE = "algo"
_EVENTS_TABLE = f"{_NAMESPACE}.events"


def _events_schema() -> Schema:
    """Schema for ``algo.events`` — the canonical append-only log.

    Returns:
        Schema: every algo-trading state transition (live, paper,
            backtest) writes a row here. Partitioned by
            (mode, ts_date) so DuckDB scans stay tight.
    """
    return Schema(
        NestedField(
            field_id=1,
            name="event_id",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=2,
            name="ts_ns",
            field_type=LongType(),
            required=True,
        ),
        NestedField(
            field_id=3,
            name="ts_date",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=4,
            name="session_id",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=5,
            name="user_id",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=6,
            name="strategy_id",
            field_type=StringType(),
            required=False,
        ),
        NestedField(
            field_id=7,
            name="mode",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=8,
            name="type",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=9,
            name="payload_json",
            field_type=StringType(),
            required=True,
        ),
        NestedField(
            field_id=10,
            name="written_at",
            field_type=TimestampType(),
            required=True,
        ),
    )


def _events_partition_spec() -> PartitionSpec:
    """Partition by mode + date string (YYYY-MM-DD) for tight scans."""
    schema = _events_schema()
    mode_field = next(f for f in schema.fields if f.name == "mode")
    date_field = next(f for f in schema.fields if f.name == "ts_date")
    return PartitionSpec(
        PartitionField(
            source_id=mode_field.field_id,
            field_id=1000,
            transform=IdentityTransform(),
            name="mode",
        ),
        PartitionField(
            source_id=date_field.field_id,
            field_id=1001,
            transform=IdentityTransform(),
            name="ts_date",
        ),
    )


def create_algo_tables() -> None:
    """Create the ``algo`` namespace and event log table.

    Idempotent. Logs and returns silently if either already exists.
    """
    catalog = _get_catalog()

    try:
        catalog.create_namespace(_NAMESPACE)
        _logger.info("Created Iceberg namespace '%s'.", _NAMESPACE)
    except Exception:
        _logger.info(
            "Namespace '%s' already exists — skipping.", _NAMESPACE
        )

    _create_table(
        catalog,
        _EVENTS_TABLE,
        _events_schema(),
        _events_partition_spec(),
    )

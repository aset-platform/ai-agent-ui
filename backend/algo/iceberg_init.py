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
    DoubleType,
    LongType,
    NestedField,
    StringType,
    TimestampType,
)

from stocks.create_tables import _create_table, _get_catalog

_logger = logging.getLogger(__name__)

_NAMESPACE = "algo"
_EVENTS_TABLE = f"{_NAMESPACE}.events"
_INTRADAY_BARS_TABLE = f"{_NAMESPACE}.intraday_bars"


def _intraday_bars_schema() -> Schema:
    """Schema for ``algo.intraday_bars`` — append-only resampled
    OHLCV bars from the live tick stream.

    Bar open is the start of the bar's interval (e.g. for a 1m
    bar at 09:15:00, the bar holds ticks from
    [09:15:00, 09:16:00)).
    """
    return Schema(
        NestedField(
            field_id=1, name="ticker",
            field_type=StringType(), required=True,
        ),
        NestedField(
            field_id=2, name="bar_date",
            field_type=StringType(), required=True,
        ),
        NestedField(
            field_id=3, name="interval_sec",
            field_type=LongType(), required=True,
        ),
        NestedField(
            field_id=4, name="bar_open_ts_ns",
            field_type=LongType(), required=True,
        ),
        NestedField(
            field_id=5, name="open",
            field_type=DoubleType(), required=True,
        ),
        NestedField(
            field_id=6, name="high",
            field_type=DoubleType(), required=True,
        ),
        NestedField(
            field_id=7, name="low",
            field_type=DoubleType(), required=True,
        ),
        NestedField(
            field_id=8, name="close",
            field_type=DoubleType(), required=True,
        ),
        NestedField(
            field_id=9, name="volume",
            field_type=LongType(), required=True,
        ),
        NestedField(
            field_id=10, name="written_at",
            field_type=TimestampType(), required=True,
        ),
    )


def _intraday_bars_partition_spec() -> PartitionSpec:
    schema = _intraday_bars_schema()
    ticker_field = next(
        f for f in schema.fields if f.name == "ticker"
    )
    date_field = next(
        f for f in schema.fields if f.name == "bar_date"
    )
    return PartitionSpec(
        PartitionField(
            source_id=ticker_field.field_id,
            field_id=1000,
            transform=IdentityTransform(),
            name="ticker",
        ),
        PartitionField(
            source_id=date_field.field_id,
            field_id=1001,
            transform=IdentityTransform(),
            name="bar_date",
        ),
    )


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
    _create_table(
        catalog,
        _INTRADAY_BARS_TABLE,
        _intraday_bars_schema(),
        _intraday_bars_partition_spec(),
    )

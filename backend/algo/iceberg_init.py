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
from pyiceberg.table.sorting import (
    NullOrder,
    SortDirection,
    SortField,
    SortOrder,
)
from pyiceberg.transforms import (
    BucketTransform,
    IdentityTransform,
    MonthTransform,
)
from pyiceberg.types import (
    DateType,
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

    Storage design (per CLAUDE.md §4.3 #22 universal rule):

    * ``bar_date`` is ``DateType`` (not ``StringType("YYYY-MM-DD")``)
      so ``MonthTransform`` can prune at the partition level.
    * Partition spec is ``BucketTransform(16, ticker) +
      MonthTransform(bar_date)`` → 16 × 12 = 192 partitions/year
      regardless of universe size (vs the prior identity-by-ticker
      spec which would have produced ~700 × 252 = 176,400 partitions
      per year, exactly the file-explosion pattern that nuked
      ``stocks.nse_delivery`` on 2026-05-15).
    * Sort order is ``(ticker, bar_open_ts_ns)`` so compaction
      colocates a ticker's bars and the typical
      ``WHERE ticker IN (...) ORDER BY bar_open_ts_ns`` read in
      ``intraday_bar_warmup`` gets predicate pushdown.
    """
    return Schema(
        NestedField(
            field_id=1, name="ticker",
            field_type=StringType(), required=True,
        ),
        NestedField(
            field_id=2, name="bar_date",
            field_type=DateType(), required=True,
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


# Bucket count for ticker partitioning — see CLAUDE.md §4.3 #22.
# 16 keeps the partition count bounded at 16 × 12 = 192/year while
# still spreading writes across enough files for parallel scan.
_TICKER_BUCKETS = 16


def _intraday_bars_partition_spec() -> PartitionSpec:
    """``BucketTransform(16, ticker) + MonthTransform(bar_date)``.

    Bounded 1-year file count: 192 partitions × ~1 file/partition
    after daily compaction = ~200 active files (well under the
    5,000 budget in CLAUDE.md §4.3 #22.e).
    """
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
            transform=BucketTransform(_TICKER_BUCKETS),
            name="ticker_bucket",
        ),
        PartitionField(
            source_id=date_field.field_id,
            field_id=1001,
            transform=MonthTransform(),
            name="bar_month",
        ),
    )


def _intraday_bars_sort_order() -> SortOrder:
    """Sort by ``(ticker, bar_open_ts_ns)`` — drives compaction
    layout AND enables predicate pushdown for the
    ``WHERE ticker IN (...) ORDER BY bar_open_ts_ns`` read pattern
    in :mod:`backend.algo.live.intraday_bar_warmup`.
    """
    schema = _intraday_bars_schema()
    ticker_field = next(
        f for f in schema.fields if f.name == "ticker"
    )
    ts_field = next(
        f for f in schema.fields if f.name == "bar_open_ts_ns"
    )
    return SortOrder(
        SortField(
            source_id=ticker_field.field_id,
            transform=IdentityTransform(),
            direction=SortDirection.ASC,
            null_order=NullOrder.NULLS_FIRST,
        ),
        SortField(
            source_id=ts_field.field_id,
            transform=IdentityTransform(),
            direction=SortDirection.ASC,
            null_order=NullOrder.NULLS_FIRST,
        ),
    )


def _events_schema() -> Schema:
    """Schema for ``algo.events`` — the canonical append-only log.

    Storage design (CLAUDE.md §4.3 #22 universal rule, redesigned
    2026-05-16 alongside the algo.intraday_bars work):

    * ``ts_date`` is ``DateType`` (not ``StringType("YYYY-MM-DD")``)
      so ``MonthTransform`` partitions can prune at scan time and
      the weekly retention job can use proper date arithmetic
      instead of string ``substr()``.
    * Partition spec is ``IdentityTransform(mode) +
      MonthTransform(ts_date)`` — mode has 6 known values
      (backtest, paper, dryrun, live, live-ws, walkforward,
      pipeline) so identity partitions there are bounded;
      MonthTransform on ts_date caps date partitions at 12/year
      regardless of write frequency.
    * Steady-state partition count after retention:
      ~6 modes × 1 retained month (non-live) +
      1 mode × 12 retained months (live) ≈ 18 partitions.
    * Sort order ``(ts_ns, type)`` so within each partition the
      common ``ORDER BY ts_ns`` query gets pushdown + events of
      the same type are colocated for filter scans.

    Retention policy (see ``algo_events_retention`` job):

    * backtest, paper, dryrun, live-ws, walkforward, pipeline →
      7-day retention; mostly research / observability noise.
    * live → 365-day retention for events that confirm successful
      Zerodha order placement (``order_submitted_live``,
      ``order_filled_live``, ``kite_postback_received``,
      ``freeze_qty_fallback_applied``); the rest of live
      (signal_generated, signal_rejected, order_rejected_live)
      → 7-day retention.
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
            field_type=DateType(),
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
    """``IdentityTransform(mode) + MonthTransform(ts_date)`` —
    mode is bounded (≤ 7 values) so identity is safe; MonthTransform
    on the date column keeps total partitions at ≤ 7 × 12 = 84/year,
    well under the 5,000-file budget in CLAUDE.md §4.3 #22.e.
    """
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
            transform=MonthTransform(),
            name="ts_month",
        ),
    )


def _events_sort_order() -> SortOrder:
    """Sort by ``(ts_ns, type)`` — the common admin / observability
    query is ``WHERE mode=? AND ts_date BETWEEN ? AND ? ORDER BY
    ts_ns`` (filter modes is partition pushdown).  Sorting on
    ``ts_ns`` after partition pruning gives O(filesize) range
    scans; secondary sort on ``type`` groups e.g. all
    ``order_filled_live`` events together within each file for
    cheap type-filtered queries.
    """
    schema = _events_schema()
    ts_ns_field = next(
        f for f in schema.fields if f.name == "ts_ns"
    )
    type_field = next(
        f for f in schema.fields if f.name == "type"
    )
    return SortOrder(
        SortField(
            source_id=ts_ns_field.field_id,
            transform=IdentityTransform(),
            direction=SortDirection.ASC,
            null_order=NullOrder.NULLS_FIRST,
        ),
        SortField(
            source_id=type_field.field_id,
            transform=IdentityTransform(),
            direction=SortDirection.ASC,
            null_order=NullOrder.NULLS_FIRST,
        ),
    )


# ---------------------------------------------------------------
# Retention policy — used by the algo_events_retention scheduler
# job AND tests that assert on the policy.  Centralised here so
# the policy lives next to the schema.
# ---------------------------------------------------------------

# Live event types that mean "successfully placed on Zerodha"
# (whether filled or not).  These are the only live-mode events
# kept for the long retention window.  Everything else in live
# mode (signals, rejections, internal evaluations) ages out at
# the short window.
LIVE_PLACED_ZERODHA_TYPES: frozenset[str] = frozenset({
    "order_submitted_live",
    "order_filled_live",
    "kite_postback_received",
    # Internal Kite-API-side adjustment but still represents a
    # *placed* order — keep with the long retention.
    "freeze_qty_fallback_applied",
})

# Short retention — backtest / paper / dryrun / observability
# modes purge this often.  Live-mode events that are NOT in the
# placed-on-Zerodha allowlist also use this window.
SHORT_RETENTION_DAYS = 7

# Long retention — live-mode placed-on-Zerodha events.  Tunable
# upward later (audit / compliance ask).
LONG_RETENTION_DAYS = 365

# Modes whose entire payload purges at SHORT_RETENTION_DAYS.
# ``live`` is intentionally absent — it gets the split retention
# above.
SHORT_RETENTION_MODES: frozenset[str] = frozenset({
    "backtest",
    "paper",
    "dryrun",
    "live-ws",
    "walkforward",
    "pipeline",
})


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
        sort_order=_events_sort_order(),
    )
    _create_table(
        catalog,
        _INTRADAY_BARS_TABLE,
        _intraday_bars_schema(),
        _intraday_bars_partition_spec(),
        sort_order=_intraday_bars_sort_order(),
    )

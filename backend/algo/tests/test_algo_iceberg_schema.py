"""Verify ``algo.events`` + ``algo.intraday_bars`` schemas conform
to the universal Iceberg table design rule (CLAUDE.md §4.3 #22).

The 2026-05-16 redesign of ``algo.intraday_bars`` replaced an
``IdentityTransform(ticker) + IdentityTransform(bar_date)``
partition spec with ``BucketTransform(16, ticker) +
MonthTransform(bar_date)`` and switched ``bar_date`` from
``StringType`` to ``DateType``.  These tests pin those choices
so a future revert can't silently re-introduce the microfile-
explosion hazard documented in
``shared/architecture/iceberg-ticker-partition-file-explosion``.
"""
from __future__ import annotations

from pyiceberg.transforms import (
    BucketTransform,
    IdentityTransform,
    MonthTransform,
)
from pyiceberg.types import DateType, StringType

from backend.algo.iceberg_init import (
    LIVE_PLACED_ZERODHA_TYPES,
    LONG_RETENTION_DAYS,
    SHORT_RETENTION_DAYS,
    SHORT_RETENTION_MODES,
    _events_partition_spec,
    _events_schema,
    _events_sort_order,
    _intraday_bars_partition_spec,
    _intraday_bars_schema,
    _intraday_bars_sort_order,
    _TICKER_BUCKETS,
)


# ---------------------------------------------------------------
# algo.intraday_bars — redesigned 2026-05-16 (ASETPLTFRM-421
# + universal Iceberg design rule)
# ---------------------------------------------------------------

class TestIntradayBarsSchema:
    """Schema-level pins for ``algo.intraday_bars``."""

    def test_bar_date_is_date_not_string(self) -> None:
        """CLAUDE.md §4.3 #22.b — ``StringType("YYYY-MM-DD")``
        defeats Iceberg date pruning.  Must be ``DateType``.
        """
        s = _intraday_bars_schema()
        bar_date = next(
            f for f in s.fields if f.name == "bar_date"
        )
        assert isinstance(bar_date.field_type, DateType), (
            f"bar_date must be DateType, got "
            f"{type(bar_date.field_type).__name__}"
        )
        assert not isinstance(bar_date.field_type, StringType)

    def test_required_fields(self) -> None:
        s = _intraday_bars_schema()
        by_name = {f.name: f for f in s.fields}
        for required in (
            "ticker",
            "bar_date",
            "interval_sec",
            "bar_open_ts_ns",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "written_at",
        ):
            assert by_name[required].required is True, (
                f"{required} must be required"
            )


class TestIntradayBarsPartitionSpec:
    """The partition spec is the canonical example of the
    universal rule (CLAUDE.md §4.3 #22.a + #22.b).
    """

    def test_no_identity_transform_on_ticker(self) -> None:
        """High-cardinality identity partitions caused
        ``stocks.nse_delivery`` to balloon to 71k files for 71k
        rows.  Block the same pattern here.
        """
        spec = _intraday_bars_partition_spec()
        for f in spec.fields:
            if f.name in ("ticker", "ticker_bucket"):
                assert not isinstance(
                    f.transform, IdentityTransform,
                ), (
                    "IdentityTransform on ticker is forbidden — "
                    "use BucketTransform(N) per CLAUDE.md §4.3 #22.a"
                )

    def test_ticker_uses_bucket_transform(self) -> None:
        spec = _intraday_bars_partition_spec()
        ticker_field = next(
            f for f in spec.fields if f.name == "ticker_bucket"
        )
        assert isinstance(ticker_field.transform, BucketTransform)
        # Bucket count matches the documented value (16)
        assert _TICKER_BUCKETS == 16

    def test_bar_date_uses_month_transform(self) -> None:
        spec = _intraday_bars_partition_spec()
        date_field = next(
            f for f in spec.fields if f.name == "bar_month"
        )
        assert isinstance(date_field.transform, MonthTransform)

    def test_one_year_file_budget(self) -> None:
        """16 buckets × 12 months = 192 partitions/year.  Well
        under the 5,000-file budget in CLAUDE.md §4.3 #22.e.
        """
        annual_partitions = _TICKER_BUCKETS * 12
        assert annual_partitions <= 5_000, (
            f"1-year file budget exceeded: {annual_partitions} "
            "partitions projected"
        )


class TestIntradayBarsSortOrder:
    """Sort order is required by CLAUDE.md §4.3 #22.c."""

    def test_sort_order_declared(self) -> None:
        order = _intraday_bars_sort_order()
        assert len(order.fields) == 2

    def test_sorts_by_ticker_then_bar_open_ts_ns(self) -> None:
        order = _intraday_bars_sort_order()
        schema = _intraday_bars_schema()
        by_id = {f.field_id: f.name for f in schema.fields}
        names = [by_id[sf.source_id] for sf in order.fields]
        assert names == ["ticker", "bar_open_ts_ns"], (
            f"Expected (ticker, bar_open_ts_ns), got {names}"
        )


# ---------------------------------------------------------------
# algo.events — redesigned 2026-05-16 (universal Iceberg design
# rule + tiered-retention model)
# ---------------------------------------------------------------

class TestEventsSchema:
    """Schema-level pins for ``algo.events``."""

    def test_ts_date_is_date_not_string(self) -> None:
        """CLAUDE.md §4.3 #22.b — partition can't use
        MonthTransform on a StringType column.
        """
        s = _events_schema()
        ts_date = next(f for f in s.fields if f.name == "ts_date")
        assert isinstance(ts_date.field_type, DateType)
        assert not isinstance(ts_date.field_type, StringType)

    def test_required_columns(self) -> None:
        s = _events_schema()
        by_name = {f.name: f for f in s.fields}
        for col in (
            "event_id",
            "ts_ns",
            "ts_date",
            "session_id",
            "user_id",
            "mode",
            "type",
            "payload_json",
            "written_at",
        ):
            assert by_name[col].required is True


class TestEventsPartitionSpec:
    """``IdentityTransform(mode) + MonthTransform(ts_date)`` —
    mode is bounded (7 values) so identity is safe;
    MonthTransform on the date column caps partition growth.
    """

    def test_partition_spec_two_fields(self) -> None:
        spec = _events_partition_spec()
        assert len(spec.fields) == 2

    def test_mode_uses_identity_transform(self) -> None:
        spec = _events_partition_spec()
        mode_field = next(
            f for f in spec.fields if f.name == "mode"
        )
        assert isinstance(mode_field.transform, IdentityTransform)

    def test_ts_date_uses_month_transform(self) -> None:
        spec = _events_partition_spec()
        ts_field = next(
            f for f in spec.fields if f.name == "ts_month"
        )
        assert isinstance(ts_field.transform, MonthTransform)

    def test_one_year_file_budget(self) -> None:
        """7 modes × 12 months = 84 partitions/year — under the
        5,000-file budget in CLAUDE.md §4.3 #22.e.  In practice
        retention keeps non-live modes at 1 active month each
        (~7) and live at 12 months (~12) → ~19 active partitions.
        """
        annual_partitions = 7 * 12
        assert annual_partitions <= 5_000


class TestEventsSortOrder:
    def test_sort_order_declared(self) -> None:
        order = _events_sort_order()
        assert len(order.fields) == 2

    def test_sorts_by_ts_ns_then_type(self) -> None:
        order = _events_sort_order()
        schema = _events_schema()
        by_id = {f.field_id: f.name for f in schema.fields}
        names = [by_id[sf.source_id] for sf in order.fields]
        assert names == ["ts_ns", "type"]


class TestEventsRetentionPolicy:
    """Constants the retention job and the schema docstring
    agree on.
    """

    def test_short_window_is_one_week(self) -> None:
        assert SHORT_RETENTION_DAYS == 7

    def test_long_window_is_one_year(self) -> None:
        assert LONG_RETENTION_DAYS == 365

    def test_short_modes_match_audit_set(self) -> None:
        """The 2026-05-16 audit identified these modes as
        short-retention candidates.  ``live`` is intentionally
        absent because it gets tiered retention.
        """
        assert SHORT_RETENTION_MODES == frozenset({
            "backtest",
            "paper",
            "dryrun",
            "live-ws",
            "walkforward",
            "pipeline",
        })
        assert "live" not in SHORT_RETENTION_MODES

    def test_placed_on_zerodha_types(self) -> None:
        """User contract (2026-05-16): "from live trading events
        keep only successfully placed on zerodha, whether it
        filled or not".  These four types qualify.
        """
        assert LIVE_PLACED_ZERODHA_TYPES == frozenset({
            "order_submitted_live",
            "order_filled_live",
            "kite_postback_received",
            "freeze_qty_fallback_applied",
        })
        # Notably NOT in the allowlist: signal_generated,
        # signal_rejected (~98 % of historical live events,
        # not Zerodha-bound), order_rejected_live (Kite refused
        # the order = not "successfully placed").
        assert "signal_generated" not in LIVE_PLACED_ZERODHA_TYPES
        assert "signal_rejected" not in LIVE_PLACED_ZERODHA_TYPES
        assert "order_rejected_live" not in LIVE_PLACED_ZERODHA_TYPES

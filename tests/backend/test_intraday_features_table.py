"""Schema + maintenance-enrollment tests for
``stocks.intraday_features`` (ASETPLTFRM-402 / FE-1).

The #1 silent failure mode for new write-heavy Iceberg
tables is forgetting to enrol them in the maintenance lists
— the ``algo.events`` 11 GB metadata bloat incident on
2026-05-12 is the canonical example. These tests fail-loud
if either enrollment site drifts.
"""

from __future__ import annotations

from pyiceberg.transforms import BucketTransform, MonthTransform
from pyiceberg.types import (
    DoubleType,
    LongType,
    StringType,
    TimestampType,
)

from stocks.create_tables import (
    _INTRADAY_FEATURES_TABLE,
    _intraday_features_schema,
    _ticker_bucket_month_partition_spec,
    _ticker_year_month_partition_spec,
)


def test_table_identifier_in_stocks_namespace() -> None:
    """FE-1 lives in the ``stocks`` namespace alongside the
    historical bars it derives from."""
    assert _INTRADAY_FEATURES_TABLE == "stocks.intraday_features"


def test_intraday_features_schema_columns() -> None:
    """All 10 spec'd fields present with the right types
    (long format: ticker, bar_open_ts_ns, bar_date,
    year_month, interval_sec, feature_name, feature_value,
    feature_set_version, written_at, bar_date_d).
    ``bar_date_d`` (field_id 10) is DateType added for the
    bucket+month partition spec."""
    from pyiceberg.types import DateType

    schema = _intraday_features_schema()
    by_name = {f.name: f for f in schema.fields}

    expected = {
        "ticker": StringType,
        "bar_open_ts_ns": LongType,
        "bar_date": StringType,
        "year_month": StringType,
        "interval_sec": LongType,
        "feature_name": StringType,
        "feature_value": DoubleType,
        "feature_set_version": StringType,
        "written_at": TimestampType,
        "bar_date_d": DateType,
    }
    assert set(by_name.keys()) == set(expected.keys())
    for name, expected_type in expected.items():
        assert isinstance(
            by_name[name].field_type, expected_type
        ), f"{name} expected {expected_type.__name__}"


def test_intraday_features_schema_required_fields() -> None:
    """Long format = no NULLs anywhere. ``feature_value`` is
    DoubleType + required=True so NaN handling is enforced
    at the writer (filter / drop) rather than read time."""
    schema = _intraday_features_schema()
    for field in schema.fields:
        assert field.required, f"{field.name} must be required=True per spec"


def test_intraday_features_partition_spec() -> None:
    """Partition layout matches ``stocks.intraday_bars`` so
    a backtest reads the same (ticker, year_month) slab from
    both tables with maximum join-locality."""
    schema = _intraday_features_schema()
    spec = _ticker_year_month_partition_spec(schema)

    part_names = [pf.name for pf in spec.fields]
    assert part_names == ["ticker", "year_month"]


def test_intraday_features_enrolled_in_hot_iceberg_tables() -> None:
    """Daily compaction job must include the new table.
    Skipping this enrollment is the #1 silent regression
    (see algo.events 11 GB incident, 2026-05-12)."""
    from backend.jobs.executor import _HOT_ICEBERG_TABLES

    assert "stocks.intraday_features" in _HOT_ICEBERG_TABLES


def test_intraday_features_enrolled_in_all_tables() -> None:
    """Backup + monthly maintenance walks ``ALL_TABLES``."""
    from backend.maintenance.iceberg_maintenance import (
        ALL_TABLES,
    )

    assert "stocks.intraday_features" in ALL_TABLES


def test_intraday_features_has_bar_date_d_datetype() -> None:
    """New ``bar_date_d`` column must be ``DateType, required=True``
    so ``MonthTransform`` can partition on it (CLAUDE.md §4.3 #22.b
    forbids ``IdentityTransform`` on a string YYYY-MM-DD column)."""
    from pyiceberg.types import DateType

    schema = _intraday_features_schema()
    f = schema.find_field("bar_date_d")
    assert isinstance(f.field_type, DateType)
    assert f.required is True


def test_intraday_features_partition_spec_is_bucket_month() -> None:
    """Partition spec must use ``BucketTransform(16)`` on ``ticker``
    and ``MonthTransform`` on ``bar_date_d`` (CLAUDE.md §4.3 #22.a/b).
    The legacy ``(ticker, year_month)`` identity grid produced ~22.7k
    partition cells; the new spec yields ~700/yr — 32× fewer."""
    schema = _intraday_features_schema()
    spec = _ticker_bucket_month_partition_spec(schema)
    by_name = {pf.name: pf for pf in spec.fields}
    assert isinstance(by_name["ticker_bucket"].transform, BucketTransform)
    assert isinstance(by_name["bar_month"].transform, MonthTransform)
    tk = schema.find_field("ticker").field_id
    bd = schema.find_field("bar_date_d").field_id
    assert by_name["ticker_bucket"].source_id == tk
    assert by_name["bar_month"].source_id == bd

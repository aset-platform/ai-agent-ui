"""Schema + maintenance-enrollment tests for
``stocks.intraday_bars`` (ASETPLTFRM-400 slice 1b).

The #1 silent failure mode for new write-heavy Iceberg
tables is forgetting to enrol them in the maintenance lists
— the ``algo.events`` 11 GB metadata bloat incident on
2026-05-12 is the canonical example. These tests fail-loud
if either enrollment site drifts.
"""

from __future__ import annotations

from pyiceberg.types import (
    DoubleType,
    LongType,
    StringType,
    TimestampType,
)

from stocks.create_tables import (
    _INTRADAY_BARS_TABLE,
    _intraday_bars_schema,
    _ticker_bar_date_partition_spec,
)


def test_table_identifier_in_stocks_namespace() -> None:
    """Live ``algo.intraday_bars`` and historical
    ``stocks.intraday_bars`` are distinct tables; this
    guards against an accidental rename collapsing them."""
    assert _INTRADAY_BARS_TABLE == "stocks.intraday_bars"


def test_intraday_bars_schema_columns() -> None:
    """All 11 spec'd fields present with the right types."""
    schema = _intraday_bars_schema()
    by_name = {f.name: f for f in schema.fields}

    expected = {
        "ticker": StringType,
        "bar_date": StringType,
        "interval_sec": LongType,
        "bar_open_ts_ns": LongType,
        "open": DoubleType,
        "high": DoubleType,
        "low": DoubleType,
        "close": DoubleType,
        "volume": LongType,
        "written_at": TimestampType,
        "source": StringType,
    }
    assert set(by_name.keys()) == set(expected.keys())
    for name, expected_type in expected.items():
        assert isinstance(
            by_name[name].field_type, expected_type
        ), f"{name} expected {expected_type.__name__}"


def test_intraday_bars_all_fields_required() -> None:
    """Spec calls for ``required=True`` on every field — this
    blocks NaN rows at the Iceberg layer and mirrors
    ``algo.intraday_bars``."""
    schema = _intraday_bars_schema()
    for field in schema.fields:
        assert field.required, f"{field.name} must be required=True per spec"


def test_intraday_bars_partitioned_by_ticker_and_bar_date() -> None:
    """Partition spec matches ``algo.intraday_bars`` so DuckDB
    scans on either dimension stay tight."""
    schema = _intraday_bars_schema()
    spec = _ticker_bar_date_partition_spec(schema)

    part_names = [pf.name for pf in spec.fields]
    assert part_names == ["ticker", "bar_date"]


def test_enrolled_in_hot_iceberg_tables() -> None:
    """Daily compaction job must include the new table.
    Skipping this enrollment is the #1 silent regression
    (see algo.events 11 GB incident, 2026-05-12)."""
    from backend.jobs.executor import _HOT_ICEBERG_TABLES

    assert "stocks.intraday_bars" in _HOT_ICEBERG_TABLES


def test_enrolled_in_all_tables() -> None:
    """Backup + monthly maintenance walks ``ALL_TABLES``."""
    from backend.maintenance.iceberg_maintenance import ALL_TABLES

    assert "stocks.intraday_bars" in ALL_TABLES

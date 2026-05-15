"""Schema + maintenance-enrollment tests for
``stocks.index_intraday_bars`` (ASETPLTFRM-402 / FE-6).

The table mirrors ``stocks.intraday_bars`` field-for-field so
FE-8's cross-sectional features can be a structural join with
minimum schema friction. The #1 silent regression for new
write-heavy Iceberg tables is forgetting to enrol them in the
maintenance lists — the ``algo.events`` 11 GB metadata bloat
incident on 2026-05-12 is the canonical example. These tests
fail-loud if either enrollment site drifts.
"""

from __future__ import annotations

from pyiceberg.types import (
    DoubleType,
    LongType,
    StringType,
    TimestampType,
)

from stocks.create_tables import (
    _INDEX_INTRADAY_BARS_TABLE,
    _index_intraday_bars_schema,
    _ticker_year_month_partition_spec,
)


def test_table_identifier_in_stocks_namespace() -> None:
    """FE-6 lives in the ``stocks`` namespace alongside the
    per-ticker historical bars it joins against."""
    assert _INDEX_INTRADAY_BARS_TABLE == "stocks.index_intraday_bars"


def test_index_intraday_bars_schema_columns() -> None:
    """All 12 spec'd fields present with the right types — must
    mirror ``stocks.intraday_bars`` exactly so FE-8 can read both
    surfaces with the same shape."""
    schema = _index_intraday_bars_schema()
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
        "year_month": StringType,
    }
    assert set(by_name.keys()) == set(expected.keys())
    for name, expected_type in expected.items():
        assert isinstance(
            by_name[name].field_type, expected_type
        ), f"{name} expected {expected_type.__name__}"


def test_index_intraday_bars_all_fields_required() -> None:
    """Every field is ``required=True`` — even ``volume`` is
    LongType + required so the writer must coerce Kite's
    synthetic 0 (indices have no traded volume) into the column
    rather than leaving NULL."""
    schema = _index_intraday_bars_schema()
    for field in schema.fields:
        assert (
            field.required
        ), f"{field.name} must be required=True per spec"


def test_index_intraday_bars_partitioned_by_ticker_and_year_month() -> None:
    """Partition spec matches ``stocks.intraday_bars`` so FE-8
    reads (ticker, year_month) slabs from both tables out of
    neighbouring directories."""
    schema = _index_intraday_bars_schema()
    spec = _ticker_year_month_partition_spec(schema)

    part_names = [pf.name for pf in spec.fields]
    assert part_names == ["ticker", "year_month"]


def test_enrolled_in_hot_iceberg_tables() -> None:
    """Daily compaction job must include the new table.
    Skipping this enrollment is the #1 silent regression (see
    algo.events 11 GB incident, 2026-05-12)."""
    from backend.jobs.executor import _HOT_ICEBERG_TABLES

    assert "stocks.index_intraday_bars" in _HOT_ICEBERG_TABLES


def test_enrolled_in_all_tables() -> None:
    """Backup + monthly maintenance walks ``ALL_TABLES``."""
    from backend.maintenance.iceberg_maintenance import ALL_TABLES

    assert "stocks.index_intraday_bars" in ALL_TABLES

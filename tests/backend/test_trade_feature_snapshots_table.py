"""Schema + maintenance-enrollment tests for
``stocks.trade_feature_snapshots`` (ASETPLTFRM-402 / FE-5).

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
    _TRADE_FEATURE_SNAPSHOTS_TABLE,
    _trade_feature_snapshots_schema,
    _year_month_mode_partition_spec,
)


def test_table_identifier_in_stocks_namespace() -> None:
    """FE-5 lives in the ``stocks`` namespace alongside the
    feature-engine output it derives from."""
    assert _TRADE_FEATURE_SNAPSHOTS_TABLE == "stocks.trade_feature_snapshots"


def test_trade_feature_snapshots_schema_columns() -> None:
    """All 15 spec'd fields present with the right types
    (fill_id, run_id, strategy_id, ticker, side, qty,
    fill_price, fill_ts_ns, bar_date, year_month, mode,
    features_json, realised_pnl_inr, outcome_label,
    written_at)."""
    schema = _trade_feature_snapshots_schema()
    by_name = {f.name: f for f in schema.fields}

    expected = {
        "fill_id": StringType,
        "run_id": StringType,
        "strategy_id": StringType,
        "ticker": StringType,
        "side": StringType,
        "qty": LongType,
        "fill_price": DoubleType,
        "fill_ts_ns": LongType,
        "bar_date": StringType,
        "year_month": StringType,
        "mode": StringType,
        "features_json": StringType,
        "realised_pnl_inr": DoubleType,
        "outcome_label": StringType,
        "written_at": TimestampType,
    }
    assert set(by_name.keys()) == set(expected.keys())
    for name, expected_type in expected.items():
        assert isinstance(
            by_name[name].field_type, expected_type
        ), f"{name} expected {expected_type.__name__}"


def test_trade_feature_snapshots_required_fields() -> None:
    """13 fields are required=True. ``realised_pnl_inr`` and
    ``outcome_label`` are required=False — both are backfilled
    by Phase-3 jobs (FE-13 meta-labeller + realised-pnl
    backfill) and MUST be writable as NULL at fill time."""
    schema = _trade_feature_snapshots_schema()
    by_name = {f.name: f for f in schema.fields}

    optional = {"realised_pnl_inr", "outcome_label"}
    for field in schema.fields:
        if field.name in optional:
            assert (
                not field.required
            ), f"{field.name} must be required=False — backfilled"
        else:
            assert (
                field.required
            ), f"{field.name} must be required=True per spec §3.2"

    assert sum(1 for f in schema.fields if f.required) == 13
    assert sum(1 for f in schema.fields if not f.required) == 2
    # Defensive — also check the two optional names exist.
    assert {"realised_pnl_inr", "outcome_label"}.issubset(by_name)


def test_trade_feature_snapshots_partition_spec() -> None:
    """Partition layout is ``(year_month, mode)`` — the
    ``mode`` partition isolates live-trading snapshots from
    backtest snapshots so research queries filter cohorts
    cleanly (spec §3.2)."""
    schema = _trade_feature_snapshots_schema()
    spec = _year_month_mode_partition_spec(schema)

    part_names = [pf.name for pf in spec.fields]
    assert part_names == ["year_month", "mode"]


def test_trade_feature_snapshots_in_hot_iceberg_tables() -> None:
    """Daily compaction job must include the new table.
    Skipping this enrollment is the #1 silent regression
    (see algo.events 11 GB incident, 2026-05-12)."""
    from backend.jobs.executor import _HOT_ICEBERG_TABLES

    assert "stocks.trade_feature_snapshots" in _HOT_ICEBERG_TABLES


def test_trade_feature_snapshots_in_all_tables() -> None:
    """Backup + monthly maintenance walks ``ALL_TABLES``."""
    from backend.maintenance.iceberg_maintenance import (
        ALL_TABLES,
    )

    assert "stocks.trade_feature_snapshots" in ALL_TABLES

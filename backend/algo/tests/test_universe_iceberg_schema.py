"""Verify ``stocks.universe_snapshot`` schema (REGIME-7)."""
from __future__ import annotations

from backend.algo.universe.iceberg_init import (
    UNIVERSE_SNAPSHOT_TABLE,
    universe_snapshot_partition_spec,
    universe_snapshot_schema,
)


def test_columns() -> None:
    s = universe_snapshot_schema()
    names = {f.name for f in s.fields}
    assert {
        "rebalance_date",
        "ticker",
        "adtv_inr_60d",
        "market_cap_inr",
        "sector",
        "included_in_top_200",
    } <= names


def test_required_fields() -> None:
    s = universe_snapshot_schema()
    by_name = {f.name: f for f in s.fields}
    assert by_name["rebalance_date"].required is True
    assert by_name["ticker"].required is True
    assert by_name["included_in_top_200"].required is True
    # Nullable metadata fields
    assert by_name["adtv_inr_60d"].required is False
    assert by_name["market_cap_inr"].required is False
    assert by_name["sector"].required is False


def test_table_identifier() -> None:
    assert UNIVERSE_SNAPSHOT_TABLE == "stocks.universe_snapshot"


def test_partition_spec_uses_year_of_rebalance_date() -> None:
    spec = universe_snapshot_partition_spec()
    assert len(spec.fields) == 1
    field = spec.fields[0]
    assert field.source_id == 1
    assert field.name == "rebalance_date_year"

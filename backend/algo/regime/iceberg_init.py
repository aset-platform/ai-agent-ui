"""Schemas + idempotent registration for regime Iceberg tables.

Mirrors the pattern of ``backend/algo/iceberg_init.py``. Registered
into the ``stocks`` namespace via ``stocks/create_tables.py`` so the
existing init script picks them up.

Both tables are append-mostly:
  * ``stocks.regime_history`` — one row per trading day; nightly
    idempotent re-write supported via NaN-replaceable upsert
    (pre-delete the incoming bar_date keys, then append).
  * ``stocks.regime_hmm_state`` — one row per monthly refit
    (~12 rows / yr), upserted by ``trained_through`` date.
"""
from __future__ import annotations

from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import YearTransform
from pyiceberg.types import (
    DateType,
    DoubleType,
    IntegerType,
    NestedField,
    StringType,
)

REGIME_HISTORY_TABLE = "stocks.regime_history"
REGIME_HMM_STATE_TABLE = "stocks.regime_hmm_state"


def regime_history_schema() -> Schema:
    """One row per trading day. ``rule_inputs_json`` stores the
    inputs dict (vix, ret_30d, ret_60d, pct_above_50sma, etc.) as
    a JSON string — keeps the schema flat & forward-compatible."""
    return Schema(
        NestedField(1, "bar_date", DateType(), required=True),
        NestedField(2, "regime_label", StringType(), required=True),
        NestedField(3, "stress_prob", DoubleType(), required=False),
        NestedField(
            4, "rule_inputs_json", StringType(), required=True,
        ),
        NestedField(
            5, "classifier_version", StringType(), required=True,
        ),
    )


def regime_history_partition_spec() -> PartitionSpec:
    """Partition by year(bar_date)."""
    return PartitionSpec(
        PartitionField(
            source_id=1,
            field_id=1000,
            transform=YearTransform(),
            name="bar_date_year",
        )
    )


def regime_hmm_state_schema() -> Schema:
    """HMM persistence; ~12 rows/yr — keep unpartitioned."""
    return Schema(
        NestedField(1, "trained_through", DateType(), required=True),
        NestedField(2, "transmat_json", StringType(), required=True),
        NestedField(3, "means_json", StringType(), required=True),
        NestedField(4, "covars_json", StringType(), required=True),
        NestedField(
            5, "n_observations", IntegerType(), required=True,
        ),
    )


def regime_hmm_state_partition_spec() -> PartitionSpec:
    return PartitionSpec()


def register_tables() -> None:
    """Idempotent — calls ``_create_table`` for both regime tables.
    Re-uses the catalog + helper from ``stocks.create_tables``."""
    from stocks.create_tables import _create_table, _get_catalog

    catalog = _get_catalog()
    _create_table(
        catalog,
        REGIME_HISTORY_TABLE,
        regime_history_schema(),
        regime_history_partition_spec(),
    )
    _create_table(
        catalog,
        REGIME_HMM_STATE_TABLE,
        regime_hmm_state_schema(),
        regime_hmm_state_partition_spec(),
    )

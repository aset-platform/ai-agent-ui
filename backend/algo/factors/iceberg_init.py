"""Iceberg table registration for ``stocks.daily_factors``.

Mirrors ``backend/algo/regime/iceberg_init.py``. Append-only with
NaN-replaceable upsert in the repo layer.
"""
from __future__ import annotations

from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import YearTransform
from pyiceberg.types import (
    DateType,
    DoubleType,
    NestedField,
    StringType,
)

DAILY_FACTORS_TABLE = "stocks.daily_factors"

MOMENTUM_KEYS = ["mom_12_1", "mom_6_1", "mom_3_1", "prox_52w"]
QUALITY_KEYS = ["f_score"]
LOWVOL_KEYS = ["realized_vol_60d", "beta_to_nifty"]
TREND_KEYS = ["adx_14", "sma200_slope", "distance_from_sma200"]
VOLUME_KEYS = ["obv", "volume_x_avg_20", "up_down_vol_ratio_20"]
RS_KEYS = ["rs_vs_nifty_3m", "rs_vs_nifty_6m", "rs_vs_sector_3m"]
BREADTH_KEYS = [
    "pct_above_50sma", "pct_above_200sma", "midcap_largecap_ratio",
]
ALL_FACTOR_KEYS = (
    MOMENTUM_KEYS
    + QUALITY_KEYS
    + LOWVOL_KEYS
    + TREND_KEYS
    + VOLUME_KEYS
    + RS_KEYS
    + BREADTH_KEYS
)


def daily_factors_schema() -> Schema:
    fields = [
        NestedField(1, "ticker", StringType(), required=True),
        NestedField(2, "bar_date", DateType(), required=True),
    ]
    fid = 3
    for k in ALL_FACTOR_KEYS:
        fields.append(NestedField(fid, k, DoubleType(), required=False))
        fid += 1
    fields.append(NestedField(fid, "sector", StringType(), required=False))
    return Schema(*fields)


def daily_factors_partition_spec() -> PartitionSpec:
    return PartitionSpec(
        PartitionField(
            source_id=2,
            field_id=2000,
            transform=YearTransform(),
            name="bar_date_year",
        )
    )


def register_tables() -> None:
    """Idempotent — registers ``stocks.daily_factors`` with the
    Iceberg catalog. Safe to call multiple times."""
    from stocks.create_tables import _create_table, _get_catalog

    catalog = _get_catalog()
    _create_table(
        catalog,
        DAILY_FACTORS_TABLE,
        daily_factors_schema(),
        daily_factors_partition_spec(),
    )

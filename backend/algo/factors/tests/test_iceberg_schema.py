"""Verify stocks.daily_factors schema + table identifier."""
from __future__ import annotations

from backend.algo.factors.iceberg_init import (
    DAILY_FACTORS_TABLE,
    daily_factors_schema,
)


REQUIRED_KEYS = {
    "ticker", "bar_date",
    "mom_12_1", "mom_6_1", "mom_3_1", "prox_52w",
    "f_score",
    "realized_vol_60d", "beta_to_nifty",
    "adx_14", "sma200_slope", "distance_from_sma200",
    "obv", "volume_x_avg_20", "up_down_vol_ratio_20",
    "rs_vs_nifty_3m", "rs_vs_nifty_6m", "rs_vs_sector_3m",
    "pct_above_50sma", "pct_above_200sma", "midcap_largecap_ratio",
    "sector",
}


def test_daily_factors_columns() -> None:
    s = daily_factors_schema()
    names = {f.name for f in s.fields}
    missing = REQUIRED_KEYS - names
    assert not missing, f"Missing columns: {missing}"


def test_table_identifier() -> None:
    assert DAILY_FACTORS_TABLE == "stocks.daily_factors"


def test_required_columns_marked_nonnull() -> None:
    s = daily_factors_schema()
    by_name = {f.name: f for f in s.fields}
    assert by_name["ticker"].required
    assert by_name["bar_date"].required
    assert not by_name["mom_12_1"].required

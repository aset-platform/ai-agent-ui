"""Unit tests for the AA filter bundle module (Sprint 9 follow-on)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.advanced_analytics_filters import (
    FUND_KEYS,
    TECH_KEYS,
    parse_filter_csv,
    passes_bundle_filters,
)
from backend.advanced_analytics_models import AdvancedRow


def _row(**overrides) -> AdvancedRow:
    base = {"ticker": "TEST.NS"}
    base.update(overrides)
    return AdvancedRow(**base)


# ---- TECH PREDICATES ------------------------------------------------


@pytest.mark.parametrize(
    "days_ago,expect_recent,expect_established",
    [
        (None, False, False),
        (0, True, False),
        (10, True, False),
        (11, False, True),
        (999, False, True),
    ],
)
def test_golden_cross_predicates(
    days_ago,
    expect_recent,
    expect_established,
):
    r = _row(golden_cross_days_ago=days_ago)
    assert passes_bundle_filters(r, ["golden_recent"], []) is expect_recent
    assert (
        passes_bundle_filters(r, ["golden_established"], [])
        is expect_established
    )


def test_price_gt_sma_predicates():
    bullish = _row(today_ltp=110.0, sma_50=100.0, sma_200=90.0)
    bearish = _row(today_ltp=80.0, sma_50=100.0, sma_200=90.0)
    nan_row = _row(today_ltp=None, sma_50=100.0, sma_200=90.0)

    assert passes_bundle_filters(bullish, ["price_gt_sma50"], []) is True
    assert passes_bundle_filters(bullish, ["price_gt_sma200"], []) is True
    assert passes_bundle_filters(bearish, ["price_gt_sma50"], []) is False
    assert passes_bundle_filters(nan_row, ["price_gt_sma50"], []) is False


@pytest.mark.parametrize(
    "rsi,key,expected",
    [
        (15.0, "rsi_oversold", True),
        (30.0, "rsi_oversold", False),
        (30.0, "rsi_neutral", True),
        (70.0, "rsi_neutral", True),
        (70.01, "rsi_overbought", True),
        (None, "rsi_neutral", False),
        (float("nan"), "rsi_oversold", False),
    ],
)
def test_rsi_band_predicates(rsi, key, expected):
    r = _row(rsi=rsi)
    assert passes_bundle_filters(r, [key], []) is expected


def test_vol_surge_and_near_52w_high():
    r = _row(today_x_vol=2.0, away_from_52week_high=-3.5)
    assert passes_bundle_filters(r, ["vol_surge"], []) is True
    assert passes_bundle_filters(r, ["near_52w_high"], []) is True

    r2 = _row(today_x_vol=1.99, away_from_52week_high=-5.01)
    assert passes_bundle_filters(r2, ["vol_surge"], []) is False
    assert passes_bundle_filters(r2, ["near_52w_high"], []) is False


# ---- FUND PREDICATES ------------------------------------------------


@pytest.mark.parametrize(
    "pscore,key,expected",
    [
        (7, "fscore_ge_7", True),
        (6, "fscore_ge_7", False),
        (3, "fscore_le_3", True),
        (4, "fscore_le_3", False),
        (None, "fscore_ge_7", False),
    ],
)
def test_fscore_predicates(pscore, key, expected):
    r = _row(pscore=pscore)
    assert passes_bundle_filters(r, [], [key]) is expected


def test_fund_threshold_predicates():
    good = _row(
        debt_to_eq=0.3,
        roce=22.0,
        sales_growth_3yrs=18.0,
        prft_growth_3yrs=20.0,
        prom_hld=55.0,
        pledged=2.0,
    )
    for key in (
        "debt_lt_0_5",
        "roce_gt_20",
        "sales_3y_gt_15",
        "profit_3y_gt_15",
        "prom_hld_gt_50",
        "pledged_lt_5",
    ):
        assert passes_bundle_filters(good, [], [key]) is True

    nan_row = _row(roce=None)
    assert passes_bundle_filters(nan_row, [], ["roce_gt_20"]) is False


# ---- COMBINATION + PARSER ------------------------------------------


def test_and_within_bundle_and_across_bundles():
    r = _row(
        today_ltp=110.0,
        sma_50=100.0,
        golden_cross_days_ago=5,
        pscore=8,
        debt_to_eq=0.2,
    )
    assert (
        passes_bundle_filters(
            r,
            ["golden_recent", "price_gt_sma50"],
            ["fscore_ge_7", "debt_lt_0_5"],
        )
        is True
    )
    r_fail = _row(
        today_ltp=80.0,
        sma_50=100.0,
        golden_cross_days_ago=5,
        pscore=8,
        debt_to_eq=0.2,
    )
    assert (
        passes_bundle_filters(
            r_fail,
            ["golden_recent", "price_gt_sma50"],
            ["fscore_ge_7", "debt_lt_0_5"],
        )
        is False
    )


def test_parse_filter_csv_happy_path():
    out = parse_filter_csv(
        "golden_recent,price_gt_sma50",
        TECH_KEYS,
        "tech",
    )
    assert out == ["golden_recent", "price_gt_sma50"]


def test_parse_filter_csv_dedupes_and_sorts():
    out = parse_filter_csv(
        "price_gt_sma50,golden_recent,price_gt_sma50",
        TECH_KEYS,
        "tech",
    )
    assert out == ["golden_recent", "price_gt_sma50"]


def test_parse_filter_csv_rejects_unknown_key():
    with pytest.raises(HTTPException) as exc:
        parse_filter_csv(
            "golden_recent,not_a_filter",
            TECH_KEYS,
            "tech",
        )
    assert exc.value.status_code == 400
    assert "not_a_filter" in exc.value.detail


def test_parse_filter_csv_empty_returns_empty():
    assert parse_filter_csv("", TECH_KEYS, "tech") == []


def test_keys_are_disjoint():
    """Tech and fund key sets must not collide (URL clarity)."""
    assert TECH_KEYS.isdisjoint(FUND_KEYS)

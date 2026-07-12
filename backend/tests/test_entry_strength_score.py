from __future__ import annotations

import pandas as pd

from entry_strength_score import (
    absorption_volume_score,
    check_hard_gates,
    close_location_value,
    lower_wick_ratio,
    relative_volume_ratio,
    selling_absorption_score,
    sma50_proximity_score,
    trend_stability_score,
)


def test_clv_close_at_high_is_one():
    assert close_location_value(100, 120, 100, 120) == 1.0


def test_clv_close_at_low_is_zero():
    assert close_location_value(120, 120, 100, 100) == 0.0


def test_clv_no_range_defaults_half():
    assert close_location_value(100, 100, 100, 100) == 0.5


def test_lower_wick_ratio_strong_reversal():
    # Open 120, Low 112, Close 119 — buyers absorbed selling.
    ratio = lower_wick_ratio(120, 120, 112, 119)
    assert round(ratio, 3) == round((119 - 112) / (120 - 112), 3)


def test_lower_wick_ratio_marubozu_close_at_low():
    # Open 120, Low 112, Close 113 — still closing near the low.
    ratio = lower_wick_ratio(120, 120, 112, 113)
    assert round(ratio, 3) == round((113 - 112) / (120 - 112), 3)


def test_selling_absorption_blends_60_40():
    clv = close_location_value(120, 120, 112, 119)
    wick = lower_wick_ratio(120, 120, 112, 119)
    expected = round((0.6 * clv + 0.4 * wick) * 100, 4)
    assert selling_absorption_score(120, 120, 112, 119) == expected


def test_selling_absorption_marubozu_scores_low():
    strong = selling_absorption_score(120, 120, 112, 119)
    weak = selling_absorption_score(120, 120, 112, 113)
    assert weak < strong


def test_relative_volume_ratio_basic():
    # Last value is "today"; window average excludes it.
    vols = pd.Series([100.0] * 20 + [150.0])
    assert relative_volume_ratio(vols, window=20) == 1.5


def test_relative_volume_ratio_insufficient_history_returns_none():
    vols = pd.Series([100.0, 110.0])
    assert relative_volume_ratio(vols, window=20) is None


def test_absorption_volume_strong_and_elevated_scores_best():
    score = absorption_volume_score(absorption_score=85, rel_volume=2.0)
    assert score == 95


def test_absorption_volume_weak_and_elevated_scores_worst():
    score = absorption_volume_score(absorption_score=20, rel_volume=2.0)
    assert score == 25


def test_absorption_volume_none_rel_volume_returns_none():
    assert (
        absorption_volume_score(absorption_score=85, rel_volume=None) is None
    )


def test_sma50_proximity_peaks_near_ideal_range():
    assert sma50_proximity_score(-3.0) == 100
    assert sma50_proximity_score(-2.0) == 95


def test_sma50_proximity_drops_at_extension():
    assert sma50_proximity_score(-10.0) == 40


def test_sma50_proximity_none_when_missing():
    assert sma50_proximity_score(None) is None


def test_sma50_proximity_extrapolates_below_lowest_point():
    # Beyond -10.0, the piecewise function continues the downward slope
    # from the (-10.0, 40) → (-7.0, 70) segment. Verify extrapolation
    # works (result < value at -10.0) rather than flat/clamped early.
    result = sma50_proximity_score(-15.0)
    assert result < sma50_proximity_score(-10.0)
    assert 0.0 <= result <= 100.0


def test_sma50_proximity_extrapolates_above_highest_point_and_clamps():
    # Above 0.0 (price far above SMA50), linear extrapolation from
    # (-2.0, 95) → (0.0, 60) segment goes deeply negative. Clamp to 0
    # verifies the boundary condition (not just within-range behavior).
    assert sma50_proximity_score(50.0) == 0.0


def test_hard_gate_rejects_price_below_sma200():
    passed, reason = check_hard_gates(close=90, sma200=100, dist_sma50_pct=-1)
    assert passed is False
    assert reason == "price_below_sma200"


def test_hard_gate_rejects_extended_below_sma50():
    passed, reason = check_hard_gates(close=100, sma200=90, dist_sma50_pct=-12)
    assert passed is False
    assert reason == "sma50_extended_beyond_10pct"


def test_hard_gate_passes_healthy_pullback():
    passed, reason = check_hard_gates(close=100, sma200=90, dist_sma50_pct=-3)
    assert passed is True
    assert reason is None


def test_hard_gate_missing_data_defaults_pass():
    # Can't evaluate a gate without data — don't reject on missing inputs.
    passed, reason = check_hard_gates(
        close=100, sma200=None, dist_sma50_pct=None
    )
    assert passed is True
    assert reason is None


def test_trend_stability_rising_sma50_scores_high():
    sma50 = pd.Series([100.0 + i * 0.10 for i in range(11)])
    score = trend_stability_score(sma50, lookback=10)
    assert score > 70


def test_trend_stability_flat_sma50_scores_mid():
    sma50 = pd.Series([100.0] * 11)
    assert trend_stability_score(sma50, lookback=10) == 50


def test_trend_stability_declining_sma50_scores_low():
    sma50 = pd.Series([100.0 - i * 0.4 for i in range(11)])
    score = trend_stability_score(sma50, lookback=10)
    assert score < 30


def test_trend_stability_insufficient_history_returns_none():
    sma50 = pd.Series([100.0, 101.0])
    assert trend_stability_score(sma50, lookback=10) is None

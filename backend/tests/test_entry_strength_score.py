from __future__ import annotations

import pandas as pd

from entry_strength_score import (
    absorption_volume_score,
    close_location_value,
    lower_wick_ratio,
    relative_volume_ratio,
    selling_absorption_score,
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

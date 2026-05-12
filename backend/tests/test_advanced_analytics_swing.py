"""Tests for swing-setups feature."""

from __future__ import annotations

from advanced_analytics_models import AdvancedRow


def test_advanced_row_swing_fields_default_none() -> None:
    """New swing fields default to None for back-compat with the
    seven existing AA reports that don't populate them.
    """
    row = AdvancedRow(ticker="TCS.NS")
    assert row.death_cross_days_ago is None
    assert row.rolling_low_20d_prev is None
    assert row.rolling_high_20d_prev is None
    assert row.rsi_3d_ago is None
    assert row.rsi_max_10d is None
    assert row.rec_category is None
    assert row.rec_severity is None
    assert row.rec_expected_return_pct is None

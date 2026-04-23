"""Tests for XGBoost ensemble feature list."""
import pytest


class TestXGBoostFeatures:
    def test_features_list_includes_technicals(self):
        from tools._forecast_ensemble import _FEATURES
        technicals = [
            "rsi_14", "macd", "bb_upper",
            "bb_lower", "atr_14",
        ]
        for f in technicals:
            assert f in _FEATURES, (
                f"{f} missing from _FEATURES"
            )

    def test_feature_count_at_least_13(self):
        from tools._forecast_ensemble import _FEATURES
        assert len(_FEATURES) >= 13

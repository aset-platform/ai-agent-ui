"""Tests for backend.algo.live.slippage — PR #2 of order safety.

Covers:
- ``bps_for(bucket)`` returns the matrix value, falls back to
  unknown for None / garbage, picks up env overrides.
- ``classify(mcap, adtv)`` 4x4 matrix:
  * both missing → "unknown"
  * either missing → "smallcap" (conservative)
  * both present, agree → that bucket
  * both present, disagree → more conservative (= higher bps cap)
"""
from __future__ import annotations

import pytest

from backend.algo.live import slippage


# ----------------------------------------------------------------
# bps_for(bucket)
# ----------------------------------------------------------------


class TestBpsFor:
    def test_largecap_default(self, monkeypatch) -> None:
        monkeypatch.delenv(
            "ALGO_SLIPPAGE_LARGECAP_BPS", raising=False,
        )
        assert slippage.bps_for("largecap") == 20

    def test_midcap_default(self, monkeypatch) -> None:
        monkeypatch.delenv(
            "ALGO_SLIPPAGE_MIDCAP_BPS", raising=False,
        )
        assert slippage.bps_for("midcap") == 50

    def test_smallcap_default(self, monkeypatch) -> None:
        monkeypatch.delenv(
            "ALGO_SLIPPAGE_SMALLCAP_BPS", raising=False,
        )
        assert slippage.bps_for("smallcap") == 100

    def test_unknown_default(self, monkeypatch) -> None:
        monkeypatch.delenv(
            "ALGO_SLIPPAGE_UNKNOWN_BPS", raising=False,
        )
        assert slippage.bps_for("unknown") == 30

    def test_none_falls_back_to_unknown(self, monkeypatch) -> None:
        monkeypatch.delenv(
            "ALGO_SLIPPAGE_UNKNOWN_BPS", raising=False,
        )
        assert slippage.bps_for(None) == 30

    def test_garbage_string_falls_back_to_unknown(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv(
            "ALGO_SLIPPAGE_UNKNOWN_BPS", raising=False,
        )
        assert slippage.bps_for("garbage") == 30

    def test_case_insensitive(self, monkeypatch) -> None:
        monkeypatch.delenv(
            "ALGO_SLIPPAGE_LARGECAP_BPS", raising=False,
        )
        assert slippage.bps_for("LargeCap") == 20

    def test_env_override_largecap(self, monkeypatch) -> None:
        monkeypatch.setenv("ALGO_SLIPPAGE_LARGECAP_BPS", "15")
        assert slippage.bps_for("largecap") == 15

    def test_env_override_midcap(self, monkeypatch) -> None:
        monkeypatch.setenv("ALGO_SLIPPAGE_MIDCAP_BPS", "75")
        assert slippage.bps_for("midcap") == 75

    def test_env_override_smallcap(self, monkeypatch) -> None:
        monkeypatch.setenv("ALGO_SLIPPAGE_SMALLCAP_BPS", "200")
        assert slippage.bps_for("smallcap") == 200

    def test_env_override_unknown(self, monkeypatch) -> None:
        monkeypatch.setenv("ALGO_SLIPPAGE_UNKNOWN_BPS", "40")
        assert slippage.bps_for(None) == 40
        assert slippage.bps_for("garbage") == 40

    def test_env_override_garbage_value_falls_back(
        self, monkeypatch, caplog,
    ) -> None:
        """Non-int env value → warn + use default."""
        monkeypatch.setenv(
            "ALGO_SLIPPAGE_LARGECAP_BPS", "not_an_int",
        )
        assert slippage.bps_for("largecap") == 20


# ----------------------------------------------------------------
# classify(mcap_cr, adtv_cr)
# ----------------------------------------------------------------


# mcap thresholds (in crore): large >= 20000, mid 5000-20000,
# small < 5000.  adtv thresholds (in crore/day): large >= 50,
# mid 20-50, small < 20.

_MCAP_LARGE = 25000.0   # crore
_MCAP_MID = 10000.0
_MCAP_SMALL = 2000.0

_ADTV_LARGE = 75.0      # crore/day
_ADTV_MID = 30.0
_ADTV_SMALL = 5.0


class TestClassifyBothMissing:
    def test_none_none_is_unknown(self) -> None:
        assert slippage.classify(None, None) == "unknown"


class TestClassifyEitherMissing:
    """Either-side missing collapses to smallcap (conservative)."""

    def test_mcap_missing_adtv_largecap_is_smallcap(self) -> None:
        assert slippage.classify(None, _ADTV_LARGE) == "smallcap"

    def test_mcap_missing_adtv_midcap_is_smallcap(self) -> None:
        assert slippage.classify(None, _ADTV_MID) == "smallcap"

    def test_mcap_missing_adtv_smallcap_is_smallcap(self) -> None:
        assert slippage.classify(None, _ADTV_SMALL) == "smallcap"

    def test_mcap_largecap_adtv_missing_is_smallcap(self) -> None:
        assert slippage.classify(_MCAP_LARGE, None) == "smallcap"

    def test_mcap_midcap_adtv_missing_is_smallcap(self) -> None:
        assert slippage.classify(_MCAP_MID, None) == "smallcap"

    def test_mcap_smallcap_adtv_missing_is_smallcap(self) -> None:
        assert slippage.classify(_MCAP_SMALL, None) == "smallcap"


class TestClassifyAgree:
    """Both signals agree → that bucket."""

    def test_both_large(self) -> None:
        assert slippage.classify(
            _MCAP_LARGE, _ADTV_LARGE,
        ) == "largecap"

    def test_both_mid(self) -> None:
        assert slippage.classify(_MCAP_MID, _ADTV_MID) == "midcap"

    def test_both_small(self) -> None:
        assert slippage.classify(
            _MCAP_SMALL, _ADTV_SMALL,
        ) == "smallcap"


class TestClassifyDisagree:
    """Conservative-wins: take whichever yields the HIGHER bps cap.

    Order: largecap (20) < midcap (50) < smallcap (100).
    So "more conservative" = "smaller bucket".
    """

    def test_mcap_large_adtv_mid_is_midcap(self) -> None:
        assert slippage.classify(
            _MCAP_LARGE, _ADTV_MID,
        ) == "midcap"

    def test_mcap_large_adtv_small_is_smallcap(self) -> None:
        assert slippage.classify(
            _MCAP_LARGE, _ADTV_SMALL,
        ) == "smallcap"

    def test_mcap_mid_adtv_large_is_midcap(self) -> None:
        assert slippage.classify(
            _MCAP_MID, _ADTV_LARGE,
        ) == "midcap"

    def test_mcap_mid_adtv_small_is_smallcap(self) -> None:
        assert slippage.classify(
            _MCAP_MID, _ADTV_SMALL,
        ) == "smallcap"

    def test_mcap_small_adtv_large_is_smallcap(self) -> None:
        assert slippage.classify(
            _MCAP_SMALL, _ADTV_LARGE,
        ) == "smallcap"

    def test_mcap_small_adtv_mid_is_smallcap(self) -> None:
        assert slippage.classify(
            _MCAP_SMALL, _ADTV_MID,
        ) == "smallcap"


class TestClassifyBoundaries:
    """Boundary cases: thresholds are inclusive on the upper side."""

    def test_mcap_exactly_20000_is_large_when_adtv_large(
        self,
    ) -> None:
        assert slippage.classify(20000.0, 50.0) == "largecap"

    def test_mcap_just_below_20000_is_mid(self) -> None:
        assert slippage.classify(19999.99, 49.99) == "midcap"

    def test_mcap_exactly_5000_is_mid_when_adtv_mid(self) -> None:
        assert slippage.classify(5000.0, 20.0) == "midcap"

    def test_mcap_just_below_5000_is_small(self) -> None:
        assert slippage.classify(4999.99, 19.99) == "smallcap"


class TestClassifyNaN:
    """NaN handling — must behave like missing (smallcap/unknown)."""

    def test_nan_mcap_treated_as_missing(self) -> None:
        nan = float("nan")
        assert slippage.classify(nan, _ADTV_LARGE) == "smallcap"

    def test_nan_adtv_treated_as_missing(self) -> None:
        nan = float("nan")
        assert slippage.classify(_MCAP_LARGE, nan) == "smallcap"

    def test_both_nan_is_unknown(self) -> None:
        nan = float("nan")
        assert slippage.classify(nan, nan) == "unknown"

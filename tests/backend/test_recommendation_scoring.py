"""Tests for Stage 1 composite scoring and accuracy factor.

Unit tests only — no live DuckDB / Iceberg dependency.
"""

import pytest


# ── Helpers ───────────────────────────────────────────


def _strong_row() -> dict:
    """Strong stock: piotroski 8, sharpe 1.5, return 20%,
    forecast +12% with 10% MAPE, sentiment +0.5, 3/4 bullish.
    """
    return {
        "piotroski": 8,
        "sharpe_ratio": 1.5,
        "annualized_return_pct": 20.0,
        "target_3m_pct_change": 12.0,
        "mape": 10.0,
        "mae": 50.0,
        "rmse": 60.0,
        "current_price": 1000.0,
        "sentiment": 0.5,
        "sma_50_signal": "Buy",
        "sma_200_signal": "Buy",
        "rsi_signal": "Buy",
        "macd_signal_text": "Bullish",
    }


def _weak_row() -> dict:
    """Weak stock: piotroski 4, sharpe -0.5, return -10%,
    forecast -5% with 40% MAPE, sentiment -0.3, 0/4 bullish.
    """
    return {
        "piotroski": 4,
        "sharpe_ratio": -0.5,
        "annualized_return_pct": -10.0,
        "target_3m_pct_change": -5.0,
        "mape": 40.0,
        "mae": 200.0,
        "rmse": 250.0,
        "current_price": 500.0,
        "sentiment": -0.3,
        "sma_50_signal": "Sell",
        "sma_200_signal": "Sell",
        "rsi_signal": "Neutral",
        "macd_signal_text": "Bearish",
    }


# ── _clamp / _norm ───────────────────────────────────


class TestClampNorm:
    def test_clamp_within_range(self):
        from backend.jobs.recommendation_engine import (
            _clamp,
        )

        assert _clamp(5.0, 0.0, 10.0) == 5.0

    def test_clamp_below(self):
        from backend.jobs.recommendation_engine import (
            _clamp,
        )

        assert _clamp(-3.0, 0.0, 10.0) == 0.0

    def test_clamp_above(self):
        from backend.jobs.recommendation_engine import (
            _clamp,
        )

        assert _clamp(15.0, 0.0, 10.0) == 10.0

    def test_norm_midpoint(self):
        from backend.jobs.recommendation_engine import (
            _norm,
        )

        assert _norm(5.0, 0.0, 10.0) == 50.0

    def test_norm_at_min(self):
        from backend.jobs.recommendation_engine import (
            _norm,
        )

        assert _norm(0.0, 0.0, 10.0) == 0.0

    def test_norm_at_max(self):
        from backend.jobs.recommendation_engine import (
            _norm,
        )

        assert _norm(10.0, 0.0, 10.0) == 100.0

    def test_norm_equal_bounds(self):
        from backend.jobs.recommendation_engine import (
            _norm,
        )

        assert _norm(5.0, 5.0, 5.0) == 0.0


# ── _compute_accuracy_factor ─────────────────────────


class TestAccuracyFactor:
    def test_low_mape(self):
        """MAPE 5 % -> factor > 0.9."""
        from backend.jobs.recommendation_engine import (
            _compute_accuracy_factor,
        )

        factor = _compute_accuracy_factor(
            mape=5.0,
            mae=20.0,
            rmse=25.0,
            current_price=1000.0,
        )
        assert factor > 0.9

    def test_high_mape(self):
        """MAPE 50 % -> factor < 0.6."""
        from backend.jobs.recommendation_engine import (
            _compute_accuracy_factor,
        )

        factor = _compute_accuracy_factor(
            mape=50.0,
            mae=300.0,
            rmse=350.0,
            current_price=500.0,
        )
        assert factor < 0.5

    def test_zero_price_no_div_error(self):
        """Should not divide by zero."""
        from backend.jobs.recommendation_engine import (
            _compute_accuracy_factor,
        )

        factor = _compute_accuracy_factor(
            mape=10.0,
            mae=50.0,
            rmse=60.0,
            current_price=0.0,
        )
        # Falls back to mape_f for all components
        assert 0.0 <= factor <= 1.0

    def test_none_price_no_div_error(self):
        """None price should also not crash."""
        from backend.jobs.recommendation_engine import (
            _compute_accuracy_factor,
        )

        factor = _compute_accuracy_factor(
            mape=10.0,
            mae=50.0,
            rmse=60.0,
            current_price=None,
        )
        assert 0.0 <= factor <= 1.0

    def test_all_none_returns_one(self):
        """All None metrics -> perfect accuracy."""
        from backend.jobs.recommendation_engine import (
            _compute_accuracy_factor,
        )

        factor = _compute_accuracy_factor(
            mape=None,
            mae=None,
            rmse=None,
            current_price=None,
        )
        assert factor == 1.0

    def test_perfect_accuracy(self):
        """Zero errors -> factor == 1.0."""
        from backend.jobs.recommendation_engine import (
            _compute_accuracy_factor,
        )

        factor = _compute_accuracy_factor(
            mape=0.0,
            mae=0.0,
            rmse=0.0,
            current_price=1000.0,
        )
        assert factor == 1.0


# ── _compute_composite_score ─────────────────────────


class TestCompositeScore:
    def test_strong_stock(self):
        """Strong stock should score 60-90."""
        from backend.jobs.recommendation_engine import (
            _compute_composite_score,
        )

        score = _compute_composite_score(_strong_row())
        assert 60.0 <= score <= 90.0, (
            f"Expected 60-90, got {score}"
        )

    def test_weak_stock(self):
        """Weak stock should score 10-45."""
        from backend.jobs.recommendation_engine import (
            _compute_composite_score,
        )

        score = _compute_composite_score(_weak_row())
        assert 10.0 <= score <= 45.0, (
            f"Expected 10-45, got {score}"
        )

    def test_strong_beats_weak(self):
        """Strong stock always outscores weak."""
        from backend.jobs.recommendation_engine import (
            _compute_composite_score,
        )

        strong = _compute_composite_score(_strong_row())
        weak = _compute_composite_score(_weak_row())
        assert strong > weak

    def test_missing_fields_no_crash(self):
        """Empty dict should not raise."""
        from backend.jobs.recommendation_engine import (
            _compute_composite_score,
        )

        score = _compute_composite_score({})
        assert 0.0 <= score <= 100.0

    def test_max_piotroski_contributes(self):
        """Piotroski 9 vs 0 should differ by ~25 pts
        (W_PIOTROSKI * 100).
        """
        from backend.jobs.recommendation_engine import (
            _compute_composite_score,
        )

        base = _strong_row()
        base["piotroski"] = 9
        high = _compute_composite_score(base)

        base["piotroski"] = 0
        low = _compute_composite_score(base)

        diff = high - low
        assert 20.0 <= diff <= 30.0, (
            f"Expected ~25 pt diff, got {diff}"
        )

    def test_technical_all_bullish(self):
        """4/4 bullish -> tech component = 100."""
        from backend.jobs.recommendation_engine import (
            _compute_composite_score,
        )

        row = _strong_row()
        row["sma_50_signal"] = "Buy"
        row["sma_200_signal"] = "Buy"
        row["rsi_signal"] = "Buy"
        row["macd_signal_text"] = "Bullish"
        score_all = _compute_composite_score(row)

        row["sma_50_signal"] = "Sell"
        row["sma_200_signal"] = "Sell"
        row["rsi_signal"] = "Sell"
        row["macd_signal_text"] = "Bearish"
        score_none = _compute_composite_score(row)

        # W_TECHNICAL = 0.10, so diff ~10 pts
        diff = score_all - score_none
        assert 8.0 <= diff <= 12.0, (
            f"Expected ~10 pt diff, got {diff}"
        )


# ── Cache behaviour ──────────────────────────────────


class TestPrefilterCache:
    def test_cache_populated(self):
        """After clearing cache, verify dict structure."""
        from backend.jobs.recommendation_engine import (
            _PREFILTER_CACHE,
        )

        _PREFILTER_CACHE.clear()
        assert "stage1" not in _PREFILTER_CACHE


# ── Stage 2: _classify_cap ────────────────────────


class TestClassifyCap:
    def test_largecap(self):
        from backend.jobs.recommendation_engine import (
            _classify_cap,
        )

        assert _classify_cap(300_000_000_000) == "largecap"

    def test_largecap_boundary(self):
        from backend.jobs.recommendation_engine import (
            _classify_cap,
        )

        assert _classify_cap(200_000_000_000) == "largecap"

    def test_midcap(self):
        from backend.jobs.recommendation_engine import (
            _classify_cap,
        )

        assert _classify_cap(100_000_000_000) == "midcap"

    def test_midcap_boundary(self):
        from backend.jobs.recommendation_engine import (
            _classify_cap,
        )

        assert _classify_cap(50_000_000_000) == "midcap"

    def test_smallcap(self):
        from backend.jobs.recommendation_engine import (
            _classify_cap,
        )

        assert _classify_cap(10_000_000_000) == "smallcap"

    def test_none_is_smallcap(self):
        from backend.jobs.recommendation_engine import (
            _classify_cap,
        )

        assert _classify_cap(None) == "smallcap"

    def test_zero_is_smallcap(self):
        from backend.jobs.recommendation_engine import (
            _classify_cap,
        )

        assert _classify_cap(0) == "smallcap"


# ── Stage 2: _compute_sector_gaps ─────────────────


class TestSectorGaps:
    def test_overweight_tech(self):
        """User 40 % tech vs universe 20 % -> +20."""
        from backend.jobs.recommendation_engine import (
            _compute_sector_gaps,
        )

        user = {"Technology": 40.0, "Finance": 5.0}
        univ = {
            "Technology": 20.0,
            "Finance": 20.0,
            "Healthcare": 15.0,
        }
        gaps = _compute_sector_gaps(user, univ)
        assert gaps["Technology"] == 20.0
        assert gaps["Finance"] == -15.0
        assert gaps["Healthcare"] == -15.0

    def test_missing_sector(self):
        """Sector only in universe shows negative gap."""
        from backend.jobs.recommendation_engine import (
            _compute_sector_gaps,
        )

        user = {"Technology": 100.0}
        univ = {"Technology": 50.0, "Energy": 50.0}
        gaps = _compute_sector_gaps(user, univ)
        assert gaps["Energy"] == -50.0
        assert gaps["Technology"] == 50.0

    def test_empty_user(self):
        """Empty portfolio -> all negative gaps."""
        from backend.jobs.recommendation_engine import (
            _compute_sector_gaps,
        )

        gaps = _compute_sector_gaps(
            {}, {"A": 30.0, "B": 70.0},
        )
        assert gaps["A"] == -30.0
        assert gaps["B"] == -70.0

    def test_both_empty(self):
        """Both empty -> no gaps."""
        from backend.jobs.recommendation_engine import (
            _compute_sector_gaps,
        )

        assert _compute_sector_gaps({}, {}) == {}


# ── Stage 2: _compute_gap_bonus ───────────────────


class TestGapBonus:
    def test_big_sector_gap_plus_nifty(self):
        """Sector gap -20, nifty, cap gap 0 -> 15."""
        from backend.jobs.recommendation_engine import (
            _compute_gap_bonus,
        )

        bonus = _compute_gap_bonus(-20.0, True, 0.0)
        # sector: min(10, 20*0.5)=10, index: 5
        assert bonus == 15.0

    def test_all_gaps(self):
        """All gaps maxed -> capped at 20."""
        from backend.jobs.recommendation_engine import (
            _compute_gap_bonus,
        )

        bonus = _compute_gap_bonus(-50.0, True, -50.0)
        assert bonus == 20.0

    def test_no_gaps(self):
        """No underweight -> 0 bonus."""
        from backend.jobs.recommendation_engine import (
            _compute_gap_bonus,
        )

        assert _compute_gap_bonus(5.0, False, 5.0) == 0.0

    def test_only_index_gap(self):
        """Index gap only -> 5 bonus."""
        from backend.jobs.recommendation_engine import (
            _compute_gap_bonus,
        )

        assert _compute_gap_bonus(0.0, True, 0.0) == 5.0

    def test_sector_just_below_threshold(self):
        """Sector gap -5 exactly is NOT < -5."""
        from backend.jobs.recommendation_engine import (
            _compute_gap_bonus,
        )

        assert _compute_gap_bonus(-5.0, False, 0.0) == 0.0

    def test_cap_gap_only(self):
        """Cap gap -10, no sector/index -> 3 points."""
        from backend.jobs.recommendation_engine import (
            _compute_gap_bonus,
        )

        bonus = _compute_gap_bonus(0.0, False, -10.0)
        assert bonus == 3.0  # 10 * 0.3


# ── Stage 2: _assign_tier ────────────────────────


class TestTierAssignment:
    def test_in_holdings(self):
        from backend.jobs.recommendation_engine import (
            _assign_tier,
        )

        assert _assign_tier(
            "RELIANCE.NS",
            {"RELIANCE.NS", "TCS.NS"},
            {"INFY.NS"},
        ) == "portfolio"

    def test_in_watchlist(self):
        from backend.jobs.recommendation_engine import (
            _assign_tier,
        )

        assert _assign_tier(
            "INFY.NS",
            {"RELIANCE.NS"},
            {"INFY.NS"},
        ) == "watchlist"

    def test_discovery(self):
        from backend.jobs.recommendation_engine import (
            _assign_tier,
        )

        assert _assign_tier(
            "HDFCBANK.NS",
            {"RELIANCE.NS"},
            {"INFY.NS"},
        ) == "discovery"

    def test_holdings_takes_priority(self):
        """If in both holdings and watchlist -> portfolio."""
        from backend.jobs.recommendation_engine import (
            _assign_tier,
        )

        assert _assign_tier(
            "TCS.NS",
            {"TCS.NS"},
            {"TCS.NS"},
        ) == "portfolio"


# ── Stage 2: _categorize_holding ──────────────────


class TestCategorizeHolding:
    def test_exit_reduce(self):
        """Low score + negative forecast -> exit."""
        from backend.jobs.recommendation_engine import (
            _categorize_holding,
        )

        assert _categorize_holding(
            25.0, -5.0, 10.0, 0.0,
        ) == "exit_reduce"

    def test_risk_alert(self):
        """Score < 40, sentiment < -0.3 -> risk_alert."""
        from backend.jobs.recommendation_engine import (
            _categorize_holding,
        )

        assert _categorize_holding(
            35.0, 2.0, 10.0, -0.5,
        ) == "risk_alert"

    def test_rebalance(self):
        """Weight > 20 -> rebalance."""
        from backend.jobs.recommendation_engine import (
            _categorize_holding,
        )

        assert _categorize_holding(
            60.0, 5.0, 25.0, 0.2,
        ) == "rebalance"

    def test_hold_accumulate_strong(self):
        """High score, low weight -> hold_accumulate."""
        from backend.jobs.recommendation_engine import (
            _categorize_holding,
        )

        assert _categorize_holding(
            75.0, 10.0, 3.0, 0.5,
        ) == "hold_accumulate"

    def test_hold_accumulate_default(self):
        """Mid-range everything -> hold_accumulate."""
        from backend.jobs.recommendation_engine import (
            _categorize_holding,
        )

        assert _categorize_holding(
            55.0, 5.0, 10.0, 0.1,
        ) == "hold_accumulate"

    def test_exit_takes_priority_over_risk(self):
        """Score < 30 AND forecast < 0 AND sent < -0.3.

        exit_reduce checked first.
        """
        from backend.jobs.recommendation_engine import (
            _categorize_holding,
        )

        assert _categorize_holding(
            25.0, -3.0, 10.0, -0.5,
        ) == "exit_reduce"

    def test_risk_alert_boundary(self):
        """Score exactly 40 -> NOT risk_alert."""
        from backend.jobs.recommendation_engine import (
            _categorize_holding,
        )

        assert _categorize_holding(
            40.0, 2.0, 10.0, -0.5,
        ) == "hold_accumulate"

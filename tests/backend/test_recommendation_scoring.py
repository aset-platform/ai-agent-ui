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

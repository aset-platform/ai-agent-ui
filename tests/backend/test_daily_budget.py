"""Tests for TokenBudget.get_daily_budget()."""

from token_budget import TokenBudget


class TestDailyBudget:
    """Verify daily budget aggregation."""

    def test_zero_usage_returns_correct_shape(self):
        """Fresh budget returns all zeros."""
        tb = TokenBudget()
        result = tb.get_daily_budget()

        assert "date" in result
        assert "daily_limit" in result
        assert "total_tokens" in result
        assert result["total_tokens"] == 0
        assert result["usage_pct"] == 0
        assert result["remaining_tokens"] == result[
            "daily_limit"
        ]
        assert "by_model" in result
        assert "estimated_queries_remaining" in result
        assert "reset_time_utc" in result

    def test_usage_reflected_after_record(self):
        """After recording tokens, budget reflects usage."""
        tb = TokenBudget()
        model = "llama-3.3-70b-versatile"

        # Reserve and record
        tb.reserve(model, 500)
        tb.record(model, 500)

        result = tb.get_daily_budget()
        assert result["total_tokens"] >= 500
        model_info = result["by_model"][model]
        assert model_info["total"] >= 500
        assert result["remaining_tokens"] < result[
            "daily_limit"
        ]

    def test_by_model_has_all_configured_models(self):
        """Every configured model appears in by_model."""
        tb = TokenBudget()
        result = tb.get_daily_budget()

        assert len(result["by_model"]) > 0
        for model, info in result["by_model"].items():
            assert "total" in info
            assert "requests" in info
            assert "limit" in info

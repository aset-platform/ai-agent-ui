"""Tests for Phase 4: risk metrics, rebalancing,
query cache, and gap filler."""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------
# Risk Metrics
# ---------------------------------------------------------------


class TestRiskMetrics:
    """Tests for portfolio_tools.get_risk_metrics."""

    @patch("tools.portfolio_tools._require_repo")
    @patch("tools.portfolio_tools.get_current_user")
    def test_needs_2_holdings(
        self, mock_user, mock_repo,
    ):
        from tools.portfolio_tools import (
            get_risk_metrics,
        )

        mock_user.return_value = "user-1"
        repo = MagicMock()
        mock_repo.return_value = repo

        # Only 1 holding
        repo.get_portfolio_holdings.return_value = (
            pd.DataFrame([{
                "ticker": "AAPL",
                "quantity": 10,
                "avg_price": 150,
                "currency": "USD",
                "market": "us",
            }])
        )
        repo.get_ohlcv.return_value = pd.DataFrame(
            {"date": pd.date_range(
                "2025-01-01", periods=5,
            ), "close": [150, 151, 152, 153, 154]},
        )

        result = get_risk_metrics.invoke({})
        assert "at least 2" in result.lower()


# ---------------------------------------------------------------
# Rebalancing with correlation
# ---------------------------------------------------------------


class TestRebalancingCorrelation:
    """Tests for suggest_rebalancing correlation check."""

    @patch("tools.portfolio_tools._require_repo")
    @patch("tools.portfolio_tools.get_current_user")
    def test_sector_concentration_detected(
        self, mock_user, mock_repo,
    ):
        from tools.portfolio_tools import (
            suggest_rebalancing,
        )

        mock_user.return_value = "user-1"
        repo = MagicMock()
        mock_repo.return_value = repo

        # 2 holdings, both IT sector
        repo.get_portfolio_holdings.return_value = (
            pd.DataFrame([
                {
                    "ticker": "TCS.NS",
                    "quantity": 100,
                    "avg_price": 3800,
                    "currency": "INR",
                    "market": "india",
                },
                {
                    "ticker": "INFY.NS",
                    "quantity": 50,
                    "avg_price": 1500,
                    "currency": "INR",
                    "market": "india",
                },
            ])
        )

        # Both same sector
        repo.get_latest_company_info.return_value = {
            "sector": "Information Technology",
        }

        # Current prices
        def mock_ohlcv(ticker, **kw):
            prices = {
                "TCS.NS": 4000, "INFY.NS": 1600,
            }
            p = prices.get(ticker, 100)
            n = 30
            return pd.DataFrame({
                "date": pd.date_range(
                    "2025-01-01", periods=n,
                ),
                "close": [p + i for i in range(n)],
            })

        repo.get_ohlcv.side_effect = mock_ohlcv

        result = suggest_rebalancing.invoke({})
        assert "Information Technology" in result
        assert "30" in result or "%" in result


# ---------------------------------------------------------------
# Query Cache
# ---------------------------------------------------------------


class TestQueryCache:
    """Tests for agents.nodes.query_cache."""

    def test_normalize_removes_stop_words(self):
        from agents.nodes.query_cache import (
            _normalize_query,
        )

        result = _normalize_query(
            "What is the current price of AAPL?"
        )
        assert "what" not in result
        assert "aapl" in result
        assert "price" in result

    def test_normalize_order_independent(self):
        from agents.nodes.query_cache import (
            _normalize_query,
        )

        q1 = _normalize_query("Analyse AAPL stock")
        q2 = _normalize_query("AAPL stock analyse")
        assert q1 == q2

    @patch("agents.nodes.query_cache._get_redis")
    def test_cache_miss(self, mock_redis):
        from agents.nodes.query_cache import (
            check_cache,
        )

        svc = MagicMock()
        svc.get.return_value = None
        mock_redis.return_value = svc

        result = check_cache("Analyse AAPL")
        assert result is None

    @patch("agents.nodes.query_cache._get_redis")
    def test_cache_hit(self, mock_redis):
        import json

        from agents.nodes.query_cache import (
            check_cache,
        )

        svc = MagicMock()
        svc.get.return_value = json.dumps({
            "response": "AAPL analysis result",
            "intent": "stock_analysis",
        })
        mock_redis.return_value = svc

        result = check_cache("Analyse AAPL")
        assert result == "AAPL analysis result"

    @patch("agents.nodes.query_cache._get_redis")
    def test_store_cache(self, mock_redis):
        from agents.nodes.query_cache import (
            store_cache,
        )

        svc = MagicMock()
        mock_redis.return_value = svc

        store_cache(
            "Analyse AAPL",
            "Result here",
            "stock_analysis",
        )
        svc.set.assert_called_once()


# ---------------------------------------------------------------
# Gap Filler
# ---------------------------------------------------------------


class TestGapFiller:
    """Tests for jobs.gap_filler."""

    @patch("jobs.gap_filler._fetch_ohlcv")
    def test_fill_resolves_gap(self, mock_fetch):
        from jobs.gap_filler import fill_data_gaps

        with patch(
            "tools._stock_shared._get_repo",
        ) as mock_get:
            repo = MagicMock()
            mock_get.return_value = repo
            repo.get_unfilled_data_gaps.return_value = [
                {
                    "id": "gap-1",
                    "ticker": "AAPL",
                    "data_type": "ohlcv",
                },
            ]

            resolved = fill_data_gaps()
            assert resolved == 1
            repo.resolve_data_gap.assert_called_once_with(
                "gap-1", "yfinance_fetch",
            )

    @patch("jobs.gap_filler._fetch_ohlcv")
    def test_fill_handles_failure(self, mock_fetch):
        from jobs.gap_filler import fill_data_gaps

        mock_fetch.side_effect = RuntimeError("fail")

        with patch(
            "tools._stock_shared._get_repo",
        ) as mock_get:
            repo = MagicMock()
            mock_get.return_value = repo
            repo.get_unfilled_data_gaps.return_value = [
                {
                    "id": "gap-1",
                    "ticker": "AAPL",
                    "data_type": "ohlcv",
                },
            ]

            resolved = fill_data_gaps()
            assert resolved == 0
            repo.resolve_data_gap.assert_not_called()

    def test_no_gaps_returns_zero(self):
        from jobs.gap_filler import fill_data_gaps

        with patch(
            "tools._stock_shared._get_repo",
        ) as mock_get:
            repo = MagicMock()
            mock_get.return_value = repo
            repo.get_unfilled_data_gaps.return_value = []

            assert fill_data_gaps() == 0


# ---------------------------------------------------------------
# Dividend Projection (S6-3 — already implemented)
# ---------------------------------------------------------------


class TestDividendProjection:
    """Tests for get_dividend_projection."""

    @patch("tools.portfolio_tools._require_repo")
    @patch("tools.portfolio_tools.get_current_user")
    def test_no_holdings(
        self, mock_user, mock_repo,
    ):
        from tools.portfolio_tools import (
            get_dividend_projection,
        )

        mock_user.return_value = "user-1"
        repo = MagicMock()
        mock_repo.return_value = repo
        repo.get_portfolio_holdings.return_value = (
            pd.DataFrame()
        )

        result = get_dividend_projection.invoke({})
        assert "no portfolio" in result.lower()

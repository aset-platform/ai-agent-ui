"""Unit tests for tiered financial news tools."""

from unittest.mock import MagicMock, patch

import pytest


class TestGetTickerNews:
    """Tests for tools.news_tools.get_ticker_news."""

    @patch("tools.news_tools._cache_get")
    def test_cache_hit(self, mock_cache):
        from tools.news_tools import get_ticker_news

        mock_cache.return_value = "Cached AAPL news"
        result = get_ticker_news.invoke(
            {"ticker": "AAPL"},
        )
        assert "cache" in result.lower()
        assert "Cached AAPL news" in result

    @patch("tools.news_tools._cache_set")
    @patch("tools.news_tools._cache_get")
    @patch("tools.news_tools.yf")
    def test_yfinance_news(
        self, mock_yf, mock_cache_get, mock_cache_set,
    ):
        from tools.news_tools import get_ticker_news

        mock_cache_get.return_value = None
        mock_ticker = MagicMock()
        mock_ticker.news = [
            {
                "title": "AAPL hits new high",
                "publisher": "Reuters",
                "link": "https://example.com",
                "providerPublishTime": "2026-03-21",
            },
        ]
        mock_yf.Ticker.return_value = mock_ticker

        result = get_ticker_news.invoke(
            {"ticker": "AAPL"},
        )
        assert "AAPL hits new high" in result
        assert "yfinance" in result.lower()

    @patch("tools.news_tools.feedparser")
    @patch("tools.news_tools._cache_set")
    @patch("tools.news_tools._cache_get")
    @patch("tools.news_tools.yf")
    def test_no_news_found(
        self, mock_yf, mock_cache_get,
        mock_cache_set, mock_fp,
    ):
        from tools.news_tools import get_ticker_news

        mock_cache_get.return_value = None
        mock_ticker = MagicMock()
        mock_ticker.news = []
        mock_yf.Ticker.return_value = mock_ticker
        # Mock feedparser to return no entries
        mock_fp.parse.return_value = MagicMock(
            entries=[],
        )

        result = get_ticker_news.invoke(
            {"ticker": "XYZXYZ"},
        )
        assert "no news" in result.lower()


class TestGetAnalystRecommendations:
    """Tests for get_analyst_recommendations."""

    @patch("tools.news_tools._cache_get")
    def test_cache_hit(self, mock_cache):
        from tools.news_tools import (
            get_analyst_recommendations,
        )

        mock_cache.return_value = "Cached recs"
        result = get_analyst_recommendations.invoke(
            {"ticker": "AAPL"},
        )
        assert "cache" in result.lower()

    @patch("tools.news_tools._cache_set")
    @patch("tools.news_tools._cache_get")
    @patch("tools.news_tools.yf")
    def test_no_recommendations(
        self, mock_yf, mock_cache_get, mock_cache_set,
    ):
        from tools.news_tools import (
            get_analyst_recommendations,
        )

        mock_cache_get.return_value = None
        mock_ticker = MagicMock()
        mock_ticker.recommendations = None
        mock_yf.Ticker.return_value = mock_ticker

        result = get_analyst_recommendations.invoke(
            {"ticker": "AAPL"},
        )
        assert "no analyst" in result.lower()


class TestSearchFinancialNews:
    """Tests for search_financial_news."""

    @patch("tools.news_tools._cache_get")
    def test_cache_hit(self, mock_cache):
        from tools.news_tools import (
            search_financial_news,
        )

        mock_cache.return_value = "Cached search"
        result = search_financial_news.invoke(
            {"query": "AAPL earnings"},
        )
        assert "cache" in result.lower()

    @patch("tools.news_tools._cache_set")
    @patch("tools.news_tools._cache_get")
    @patch("tools.news_tools.yf")
    def test_yfinance_sufficient(
        self, mock_yf, mock_cache_get, mock_cache_set,
    ):
        """Free sources return enough results —
        SerpAPI NOT called."""
        from tools.news_tools import (
            search_financial_news,
        )

        mock_cache_get.return_value = None
        mock_ticker = MagicMock()
        mock_ticker.news = [
            {"title": f"News {i}", "publisher": "X",
             "providerPublishTime": "2026-03-21"}
            for i in range(5)
        ]
        mock_yf.Ticker.return_value = mock_ticker

        result = search_financial_news.invoke(
            {"query": "AAPL earnings report"},
        )
        assert "serpapi" not in result.lower()
        assert "News 0" in result

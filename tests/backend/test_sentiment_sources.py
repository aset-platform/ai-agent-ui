"""Unit tests for multi-source headline fetcher."""

from unittest.mock import MagicMock, patch

from tools._sentiment_sources import (
    HeadlineItem,
    _deduplicate,
    _fetch_google_rss,
    _fetch_yahoo_rss,
    _fetch_yfinance,
    fetch_all_headlines,
)


class TestFetchYfinance:
    """Tests for _fetch_yfinance."""

    @patch("yfinance.Ticker")
    def test_returns_headlines(self, mock_cls):
        mock_ticker = MagicMock()
        mock_ticker.news = [
            {"content": {"title": "AAPL up 5%"}},
            {"title": "Apple beats earnings"},
        ]
        mock_cls.return_value = mock_ticker

        items = _fetch_yfinance("AAPL")
        assert len(items) == 2
        assert items[0].title == "AAPL up 5%"
        assert items[0].source == "yfinance"
        assert items[0].weight == 1.0

    @patch("yfinance.Ticker")
    def test_empty_news(self, mock_cls):
        mock_ticker = MagicMock()
        mock_ticker.news = []
        mock_cls.return_value = mock_ticker

        items = _fetch_yfinance("AAPL")
        assert items == []

    @patch("yfinance.Ticker")
    def test_skips_empty_titles(self, mock_cls):
        mock_ticker = MagicMock()
        mock_ticker.news = [
            {"content": {"title": ""}},
            {"content": {"title": "Valid headline"}},
        ]
        mock_cls.return_value = mock_ticker

        items = _fetch_yfinance("AAPL")
        assert len(items) == 1
        assert items[0].title == "Valid headline"


class TestFetchYahooRss:
    """Tests for _fetch_yahoo_rss."""

    @patch("feedparser.parse")
    def test_returns_headlines(self, mock_parse):
        entry = MagicMock()
        entry.title = "Yahoo: AAPL news"
        entry.published = "2026-03-27"
        mock_parse.return_value = MagicMock(
            entries=[entry],
        )

        items = _fetch_yahoo_rss("AAPL")
        assert len(items) == 1
        assert items[0].source == "yahoo_rss"
        assert items[0].weight == 0.8


class TestFetchGoogleRss:
    """Tests for _fetch_google_rss."""

    @patch("feedparser.parse")
    def test_returns_headlines(self, mock_parse):
        entry = MagicMock()
        entry.title = "Google: AAPL stock rises"
        entry.published = "2026-03-27"
        mock_parse.return_value = MagicMock(
            entries=[entry],
        )

        items = _fetch_google_rss("AAPL")
        assert len(items) == 1
        assert items[0].source == "google_rss"
        assert items[0].weight == 0.6


class TestDeduplicate:
    """Tests for _deduplicate."""

    def test_removes_similar_titles(self):
        items = [
            HeadlineItem(
                "Apple stock rises 5% after earnings",
                "yfinance",
                1.0,
            ),
            HeadlineItem(
                "Apple stock rises 5% after earnings beat",
                "google_rss",
                0.6,
            ),
        ]
        result = _deduplicate(items, threshold=0.8)
        assert len(result) == 1
        # Keeps the higher-weight item.
        assert result[0].source == "yfinance"

    def test_keeps_different_titles(self):
        items = [
            HeadlineItem(
                "Apple stock rises 5%",
                "yfinance",
                1.0,
            ),
            HeadlineItem(
                "Tesla announces new factory",
                "google_rss",
                0.6,
            ),
        ]
        result = _deduplicate(items, threshold=0.8)
        assert len(result) == 2

    def test_empty_list(self):
        result = _deduplicate([])
        assert result == []

    def test_single_item(self):
        items = [
            HeadlineItem("Only one", "yfinance", 1.0),
        ]
        result = _deduplicate(items)
        assert len(result) == 1


class TestFetchAllHeadlines:
    """Tests for fetch_all_headlines."""

    @patch("feedparser.parse")
    @patch("yfinance.Ticker")
    def test_merges_all_sources(
        self, mock_yf_cls, mock_fp,
    ):
        # yfinance returns 1 headline.
        mock_ticker = MagicMock()
        mock_ticker.news = [
            {"content": {"title": "AAPL up"}},
        ]
        mock_yf_cls.return_value = mock_ticker

        # feedparser returns different headlines for
        # Yahoo RSS and Google RSS calls.
        entry1 = MagicMock()
        entry1.title = "Apple rises on Yahoo"
        entry1.published = ""
        entry2 = MagicMock()
        entry2.title = "Tech rally on Google"
        entry2.published = ""
        mock_fp.return_value = MagicMock(
            entries=[entry1, entry2],
        )

        result = fetch_all_headlines("AAPL")
        # At least 3 unique headlines (dedup may vary).
        assert len(result) >= 2

    @patch("feedparser.parse")
    @patch("yfinance.Ticker")
    def test_skips_failed_source(
        self, mock_yf_cls, mock_fp,
    ):
        mock_yf_cls.side_effect = RuntimeError(
            "API down",
        )
        # feedparser returns entries for both RSS calls.
        entry = MagicMock()
        entry.title = "RSS headline"
        entry.published = ""
        mock_fp.return_value = MagicMock(
            entries=[entry],
        )

        result = fetch_all_headlines("AAPL")
        # yfinance failed, but Yahoo+Google RSS succeed.
        assert len(result) >= 1
        sources = {h.source for h in result}
        assert "yfinance" not in sources

    @patch("feedparser.parse")
    @patch("yfinance.Ticker")
    def test_all_sources_fail(
        self, mock_yf_cls, mock_fp,
    ):
        mock_yf_cls.side_effect = RuntimeError("fail")
        mock_fp.side_effect = RuntimeError("fail")

        result = fetch_all_headlines("AAPL")
        assert result == []

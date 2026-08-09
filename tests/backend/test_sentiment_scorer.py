"""Unit tests for sentiment scorer pipeline."""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from tools._sentiment_sources import HeadlineItem


class TestScoreHeadlines:
    """Tests for score_headlines weighted average.

    score_headlines_with_source tries FinBERT FIRST (config default
    sentiment_scorer="finbert" — zero API cost, real local model) and
    only falls through to the LLM path when FinBERT is unavailable or
    returns no score. These tests are specifically about the LLM
    path's behavior (weighted average, no-llm, llm-failure), so
    every test here force-disables FinBERT via autouse fixture rather
    than letting the real (correct, but non-deterministic-feeling
    for a unit test) local model run and mask the LLM mock.
    """

    @pytest.fixture(autouse=True)
    def _disable_finbert(self, monkeypatch):
        monkeypatch.setattr(
            "tools._sentiment_finbert.score_headlines_finbert",
            lambda titles: None,
        )

    def test_weighted_average(self):
        from tools._sentiment_scorer import (
            score_headlines,
        )

        items = [
            HeadlineItem(
                "Bullish headline",
                "yfinance",
                1.0,
            ),
            HeadlineItem(
                "Bearish headline",
                "google_rss",
                0.6,
            ),
        ]

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content="[0.8, -0.4]",
        )

        result = score_headlines(items, llm=mock_llm)
        # Expected: (0.8*1.0 + -0.4*0.6) / (1.0+0.6)
        # = (0.8 - 0.24) / 1.6 = 0.56 / 1.6 = 0.35
        assert result is not None
        assert abs(result - 0.35) < 0.01

    def test_no_headlines_returns_none(self):
        from tools._sentiment_scorer import (
            score_headlines,
        )

        result = score_headlines([], llm=MagicMock())
        assert result is None

    def test_no_llm_returns_none(self):
        from tools._sentiment_scorer import (
            score_headlines,
        )

        items = [
            HeadlineItem("Test", "yfinance", 1.0),
        ]
        result = score_headlines(items, llm=None)
        assert result is None

    def test_llm_failure_returns_none(self):
        from tools._sentiment_scorer import (
            score_headlines,
        )

        items = [
            HeadlineItem("Test", "yfinance", 1.0),
        ]
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError(
            "LLM down",
        )

        result = score_headlines(items, llm=mock_llm)
        assert result is None


class TestRefreshTickerSentiment:
    """Tests for refresh_ticker_sentiment."""

    @patch(
        "tools._stock_shared._require_repo",
    )
    @patch(
        "tools._sentiment_scorer.fetch_all_headlines",
    )
    def test_idempotent_skips_today(
        self, mock_fetch, mock_repo_fn,
    ):
        from tools._sentiment_scorer import (
            refresh_ticker_sentiment,
        )

        mock_repo = MagicMock()
        mock_repo.get_sentiment_series.return_value = (
            pd.DataFrame(
                {
                    "score_date": [date.today()],
                    "avg_score": [0.5],
                }
            )
        )
        mock_repo_fn.return_value = mock_repo

        result = refresh_ticker_sentiment(
            "AAPL", llm=MagicMock(),
        )
        assert result == 0.5
        mock_fetch.assert_not_called()

    @patch(
        "tools._stock_shared._require_repo",
    )
    @patch(
        "tools._sentiment_scorer.fetch_all_headlines",
    )
    @patch(
        "tools._sentiment_scorer.score_headlines_with_source",
    )
    def test_scores_and_persists(
        self, mock_score, mock_fetch, mock_repo_fn,
    ):
        """refresh_ticker_sentiment calls score_headlines_with_
        source directly (for provenance) — NOT the score_headlines
        backward-compat wrapper."""
        from tools._sentiment_scorer import (
            refresh_ticker_sentiment,
        )

        mock_repo = MagicMock()
        mock_repo.get_sentiment_series.return_value = (
            pd.DataFrame()
        )
        mock_repo_fn.return_value = mock_repo

        mock_fetch.return_value = [
            HeadlineItem(
                "Good news", "yfinance", 1.0,
            ),
        ]
        mock_score.return_value = (0.7, "llm")

        result = refresh_ticker_sentiment(
            "AAPL", llm=MagicMock(),
        )
        assert result == 0.7
        mock_repo.insert_sentiment_score.assert_called_once()
        call_args = (
            mock_repo.insert_sentiment_score.call_args
        )
        assert call_args[0][0] == "AAPL"
        assert call_args[0][2] == 0.7
        assert call_args[1]["source"] == "llm"

    @patch(
        "tools._stock_shared._require_repo",
    )
    @patch(
        "tools._sentiment_scorer.fetch_all_headlines",
    )
    def test_no_headlines_returns_none(
        self, mock_fetch, mock_repo_fn,
    ):
        from tools._sentiment_scorer import (
            refresh_ticker_sentiment,
        )

        mock_repo = MagicMock()
        mock_repo.get_sentiment_series.return_value = (
            pd.DataFrame()
        )
        mock_repo_fn.return_value = mock_repo
        mock_fetch.return_value = []

        result = refresh_ticker_sentiment(
            "AAPL", llm=MagicMock(),
        )
        assert result is None

    @patch(
        "tools._stock_shared._require_repo",
    )
    @patch(
        "tools._sentiment_scorer.fetch_all_headlines",
    )
    @patch(
        "tools._sentiment_scorer.score_headlines_with_source",
    )
    def test_llm_none_writes_zero(
        self, mock_score, mock_fetch, mock_repo_fn,
    ):
        from tools._sentiment_scorer import (
            refresh_ticker_sentiment,
        )

        mock_repo = MagicMock()
        mock_repo.get_sentiment_series.return_value = (
            pd.DataFrame()
        )
        mock_repo_fn.return_value = mock_repo
        mock_fetch.return_value = [
            HeadlineItem("News", "yfinance", 1.0),
        ]
        # (None, "none") when neither FinBERT nor LLM available.
        mock_score.return_value = (None, "none")

        result = refresh_ticker_sentiment(
            "AAPL", llm=None,
        )
        assert result == 0.0
        call_args = (
            mock_repo.insert_sentiment_score.call_args
        )
        assert call_args[1]["source"] == "none"


class TestLegacyWrappers:
    """Tests for backward-compatible wrappers."""

    @patch("tools._sentiment_scorer.fetch_all_headlines")
    def test_fetch_news_headlines(self, mock_fetch):
        from tools._sentiment_scorer import (
            fetch_news_headlines,
        )

        mock_fetch.return_value = [
            HeadlineItem("Title 1", "yfinance", 1.0),
            HeadlineItem("Title 2", "yahoo_rss", 0.8),
        ]

        result = fetch_news_headlines("AAPL")
        assert result == ["Title 1", "Title 2"]

    def test_score_headlines_llm_no_llm(self):
        from tools._sentiment_scorer import (
            score_headlines_llm,
        )

        result = score_headlines_llm(
            ["headline 1"], llm=None,
        )
        assert result == [0.0]

    def test_score_headlines_llm_empty(self):
        from tools._sentiment_scorer import (
            score_headlines_llm,
        )

        result = score_headlines_llm([], llm=MagicMock())
        assert result == []


class TestParseScores:
    """Tests for _parse_scores."""

    def test_valid_json(self):
        from tools._sentiment_scorer import (
            _parse_scores,
        )

        result = _parse_scores("[0.5, -0.3, 0.0]", 3)
        assert result == [0.5, -0.3, 0.0]

    def test_clamped(self):
        from tools._sentiment_scorer import (
            _parse_scores,
        )

        result = _parse_scores("[2.0, -5.0]", 2)
        assert result == [1.0, -1.0]

    def test_fallback_regex(self):
        from tools._sentiment_scorer import (
            _parse_scores,
        )

        result = _parse_scores(
            "Rating: 0.5 and then -0.3", 2,
        )
        assert result == [0.5, -0.3]

    def test_unparseable_returns_zeros(self):
        from tools._sentiment_scorer import (
            _parse_scores,
        )

        result = _parse_scores("no numbers here", 3)
        assert result == [0.0, 0.0, 0.0]

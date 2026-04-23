"""Unit tests for FinBERT batch sentiment scorer.

Requires ``transformers`` and ``torch`` to be installed.
Tests are skipped gracefully if those packages are absent.
"""

from unittest.mock import MagicMock, patch

import pytest

transformers = pytest.importorskip("transformers")


class TestScoreHeadlinesFinbert:
    """Tests for score_headlines_finbert."""

    def test_positive_headline(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )

        mock_result = [[{"label": "positive", "score": 0.95}]]
        with patch(
            "tools._sentiment_finbert._get_pipeline"
        ) as mock_pipe_fn:
            mock_pipe = MagicMock(return_value=mock_result)
            mock_pipe_fn.return_value = mock_pipe

            results = score_headlines_finbert(
                ["Strong earnings beat expectations"]
            )

        assert len(results) == 1
        assert results[0]["label"] == "positive"
        assert abs(results[0]["score"] - 0.95) < 1e-6
        assert abs(results[0]["mapped"] - 0.95) < 1e-6

    def test_negative_headline(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )

        mock_result = [[{"label": "negative", "score": 0.88}]]
        with patch(
            "tools._sentiment_finbert._get_pipeline"
        ) as mock_pipe_fn:
            mock_pipe = MagicMock(return_value=mock_result)
            mock_pipe_fn.return_value = mock_pipe

            results = score_headlines_finbert(
                ["Company misses revenue targets"]
            )

        assert len(results) == 1
        assert results[0]["label"] == "negative"
        assert abs(results[0]["mapped"] - (-0.88)) < 1e-6

    def test_batch_scoring(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )

        mock_result = [
            [{"label": "positive", "score": 0.90}],
            [{"label": "neutral", "score": 0.75}],
            [{"label": "negative", "score": 0.80}],
        ]
        with patch(
            "tools._sentiment_finbert._get_pipeline"
        ) as mock_pipe_fn:
            mock_pipe = MagicMock(return_value=mock_result)
            mock_pipe_fn.return_value = mock_pipe

            headlines = [
                "Stock hits all-time high",
                "Company holds analyst day",
                "Profit warning issued",
            ]
            results = score_headlines_finbert(headlines)

        assert len(results) == 3
        assert results[0]["mapped"] > 0
        assert results[1]["mapped"] == 0.0
        assert results[2]["mapped"] < 0

    def test_empty_list(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )

        results = score_headlines_finbert([])
        assert results == []

    def test_result_structure(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )

        mock_result = [[{"label": "neutral", "score": 0.60}]]
        with patch(
            "tools._sentiment_finbert._get_pipeline"
        ) as mock_pipe_fn:
            mock_pipe = MagicMock(return_value=mock_result)
            mock_pipe_fn.return_value = mock_pipe

            results = score_headlines_finbert(["Market flat today"])

        assert len(results) == 1
        item = results[0]
        assert set(item.keys()) == {"label", "score", "mapped"}
        assert isinstance(item["label"], str)
        assert isinstance(item["score"], float)
        assert isinstance(item["mapped"], float)

    def test_pipeline_failure_returns_neutrals(self):
        from tools._sentiment_finbert import (
            score_headlines_finbert,
        )

        with patch(
            "tools._sentiment_finbert._get_pipeline"
        ) as mock_pipe_fn:
            mock_pipe_fn.return_value = None

            results = score_headlines_finbert(
                ["Headline one", "Headline two"]
            )

        assert len(results) == 2
        for item in results:
            assert item["label"] == "neutral"
            assert item["mapped"] == 0.0


class TestComputeWeightedScore:
    """Tests for compute_weighted_score."""

    def test_weighted_average(self):
        from tools._sentiment_finbert import (
            compute_weighted_score,
        )

        scored = [
            {"label": "positive", "score": 0.9, "mapped": 0.9},
            {"label": "negative", "score": 0.6, "mapped": -0.6},
        ]
        weights = [1.0, 0.5]
        result = compute_weighted_score(scored, weights)

        # (0.9*1.0 + -0.6*0.5) / (1.0 + 0.5) = (0.9 - 0.3) / 1.5
        expected = 0.6 / 1.5
        assert result is not None
        assert abs(result - expected) < 1e-6

    def test_empty_returns_none(self):
        from tools._sentiment_finbert import (
            compute_weighted_score,
        )

        result = compute_weighted_score([], [])
        assert result is None

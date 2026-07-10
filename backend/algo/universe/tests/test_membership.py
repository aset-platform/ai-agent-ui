"""Unit tests for get_off_universe_tickers()."""
from __future__ import annotations

from unittest.mock import patch

from backend.algo.universe.membership import get_off_universe_tickers


class TestGetOffUniverseTickers:
    def test_empty_input_returns_empty(self):
        assert get_off_universe_tickers([]) == []

    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_ticker_in_universe_not_flagged(self, mock_query):
        mock_query.return_value = [
            {"ticker": "ITC.NS"}, {"ticker": "TCS.NS"},
        ]
        result = get_off_universe_tickers(["ITC.NS"])
        assert result == []

    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_ticker_absent_from_universe_flagged(self, mock_query):
        mock_query.return_value = [{"ticker": "ITC.NS"}]
        result = get_off_universe_tickers(
            ["ITC.NS", "MOVALUE.NS", "SMALLCAP.NS"],
        )
        assert result == ["MOVALUE.NS", "SMALLCAP.NS"]

    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_query_failure_fails_open(self, mock_query):
        mock_query.side_effect = RuntimeError("duckdb boom")
        result = get_off_universe_tickers(["ANYTHING.NS"])
        assert result == []

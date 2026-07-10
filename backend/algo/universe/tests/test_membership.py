"""Unit tests for get_off_universe_tickers()."""
from __future__ import annotations

import json
from unittest.mock import patch

from backend.algo.universe.membership import get_off_universe_tickers


class _FakeCache:
    """In-memory stand-in for CacheService — real .get()/.set()
    semantics (JSON string in, JSON string out) without touching
    Redis."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ttl: int = 300) -> None:
        self._store[key] = value


class _RaisingCache:
    """Simulates a Redis outage that escapes CacheService's own
    internal RedisError handling (e.g. get_cache() itself blows
    up) — exercises the fail-open fallback to DuckDB."""

    def get(self, key: str):
        raise ConnectionError("redis unreachable")

    def set(self, key: str, value: str, ttl: int = 300) -> None:
        raise ConnectionError("redis unreachable")


class TestGetOffUniverseTickers:
    def test_empty_input_returns_empty(self):
        assert get_off_universe_tickers([]) == []

    @patch("backend.algo.universe.membership.get_cache")
    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_ticker_in_universe_not_flagged(
        self, mock_query, mock_get_cache,
    ):
        mock_get_cache.return_value = _FakeCache()
        mock_query.return_value = [
            {"ticker": "ITC.NS"}, {"ticker": "TCS.NS"},
        ]
        result = get_off_universe_tickers(["ITC.NS"])
        assert result == []

    @patch("backend.algo.universe.membership.get_cache")
    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_ticker_absent_from_universe_flagged(
        self, mock_query, mock_get_cache,
    ):
        mock_get_cache.return_value = _FakeCache()
        mock_query.return_value = [{"ticker": "ITC.NS"}]
        result = get_off_universe_tickers(
            ["ITC.NS", "MOVALUE.NS", "SMALLCAP.NS"],
        )
        assert result == ["MOVALUE.NS", "SMALLCAP.NS"]

    @patch("backend.algo.universe.membership.get_cache")
    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_query_failure_fails_open(self, mock_query, mock_get_cache):
        mock_get_cache.return_value = _FakeCache()
        mock_query.side_effect = RuntimeError("duckdb boom")
        result = get_off_universe_tickers(["ANYTHING.NS"])
        assert result == []

    @patch("backend.algo.universe.membership.get_cache")
    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_cache_unavailable_falls_through_to_duckdb(
        self, mock_query, mock_get_cache,
    ):
        """Redis unreachable (get_cache() itself raises past
        CacheService's internal handling) must not break the
        function — it falls through to the DuckDB query and
        still returns the correct result."""
        mock_get_cache.return_value = _RaisingCache()
        mock_query.return_value = [{"ticker": "ITC.NS"}]
        result = get_off_universe_tickers(
            ["ITC.NS", "MOVALUE.NS"],
        )
        assert result == ["MOVALUE.NS"]
        mock_query.assert_called_once()

    @patch("backend.algo.universe.membership.get_cache")
    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_cache_hit_skips_duckdb_query(
        self, mock_query, mock_get_cache,
    ):
        """Second call with a warm cache must NOT re-run the
        DuckDB query — the in-universe set is scope-level data
        and should be served from cache."""
        cache = _FakeCache()
        cache.set(
            "cache:universe:members",
            json.dumps(["ITC.NS", "TCS.NS"]),
            ttl=300,
        )
        mock_get_cache.return_value = cache
        mock_query.return_value = [{"ticker": "ITC.NS"}]

        result = get_off_universe_tickers(
            ["ITC.NS", "MOVALUE.NS"],
        )

        assert result == ["MOVALUE.NS"]
        mock_query.assert_not_called()

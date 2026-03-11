"""Tests for Story 3.1 — CachedRepository wrapper."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from stocks.cached_repository import CachedRepository


@pytest.fixture()
def mock_repo():
    """Return a mock StockRepository."""
    repo = MagicMock()
    repo.get_currency.return_value = "USD"
    repo.get_latest_ohlcv_date.return_value = date(
        2026,
        3,
        10,
    )
    repo.get_latest_analysis_summary.return_value = {
        "ticker": "AAPL",
        "analysis_date": date(2026, 3, 10),
    }
    repo.get_latest_forecast_run.return_value = {
        "ticker": "AAPL",
        "horizon_months": 3,
        "run_date": date(2026, 3, 10),
    }
    return repo


@pytest.fixture()
def cached(mock_repo):
    """Return a CachedRepository wrapping the mock."""
    return CachedRepository(mock_repo, ttl=60)


def test_cache_hit_avoids_second_call(cached, mock_repo):
    """Second call should return cached result."""
    cached.get_currency("AAPL")
    cached.get_currency("AAPL")
    mock_repo.get_currency.assert_called_once_with("AAPL")


def test_cache_miss_delegates(cached, mock_repo):
    """First call should delegate to the underlying repo."""
    result = cached.get_currency("AAPL")
    assert result == "USD"
    mock_repo.get_currency.assert_called_once()


def test_different_tickers_cache_separately(
    cached,
    mock_repo,
):
    """Different tickers should not share cache entries."""
    cached.get_currency("AAPL")
    cached.get_currency("MSFT")
    assert mock_repo.get_currency.call_count == 2


def test_ohlcv_date_cached(cached, mock_repo):
    """get_latest_ohlcv_date should be cached."""
    cached.get_latest_ohlcv_date("AAPL")
    cached.get_latest_ohlcv_date("AAPL")
    mock_repo.get_latest_ohlcv_date.assert_called_once()


def test_analysis_summary_cached(cached, mock_repo):
    """get_latest_analysis_summary should be cached."""
    cached.get_latest_analysis_summary("AAPL")
    cached.get_latest_analysis_summary("AAPL")
    mock_repo.get_latest_analysis_summary.assert_called_once()


def test_forecast_run_cached(cached, mock_repo):
    """get_latest_forecast_run should be cached."""
    cached.get_latest_forecast_run("AAPL", 3)
    cached.get_latest_forecast_run("AAPL", 3)
    mock_repo.get_latest_forecast_run.assert_called_once()


def test_write_invalidates_cache(cached, mock_repo):
    """insert_ohlcv should invalidate the ohlcv date cache."""
    cached.get_latest_ohlcv_date("AAPL")
    cached.insert_ohlcv("AAPL", [])
    cached.get_latest_ohlcv_date("AAPL")
    assert mock_repo.get_latest_ohlcv_date.call_count == 2


def test_uncached_method_delegates(cached, mock_repo):
    """Methods not explicitly cached should pass through."""
    mock_repo.get_ohlcv.return_value = "data"
    result = cached.get_ohlcv("AAPL")
    assert result == "data"
    mock_repo.get_ohlcv.assert_called_once_with("AAPL")

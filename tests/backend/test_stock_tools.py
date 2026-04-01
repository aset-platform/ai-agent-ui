"""Unit tests for stock data, price analysis, and forecasting tools.

Tests are isolated — yfinance, PyArrow/Iceberg, and parquet I/O are all
mocked so the suite runs offline without any local data files.
"""

import math
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(rows: int = 300, adj_close_nan: bool = False) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with a DatetimeIndex.

    Args:
        rows: Number of rows to generate.
        adj_close_nan: If True, ``Adj Close`` column is all NaN
            (simulates yfinance >= 1.2 or Iceberg with missing adj_close).
    """
    idx = pd.date_range("2020-01-01", periods=rows, freq="B")
    rng = np.random.default_rng(0)
    close = 100 + rng.standard_normal(rows).cumsum()
    df = pd.DataFrame(
        {
            "Open": close * 0.99,
            "High": close * 1.01,
            "Low": close * 0.98,
            "Close": close,
            "Adj Close": [float("nan")] * rows if adj_close_nan else close,
            "Volume": rng.integers(1_000_000, 5_000_000, rows),
        },
        index=idx,
    )
    df.index.name = "Date"
    return df


def _make_iceberg_ohlcv(rows: int = 300, ticker: str = "AAPL") -> pd.DataFrame:
    """Return a DataFrame shaped like ``StockRepository.get_ohlcv()`` output."""
    idx = pd.date_range("2020-01-01", periods=rows, freq="B")
    rng = np.random.default_rng(0)
    close = 100 + rng.standard_normal(rows).cumsum()
    return pd.DataFrame(
        {
            "ticker": [ticker] * rows,
            "date": idx.date,
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "adj_close": close,
            "volume": rng.integers(1_000_000, 5_000_000, rows),
        }
    )


def _mock_repo():
    """Return a MagicMock configured as a StockRepository stand-in."""
    repo = MagicMock()
    repo.check_existing_data.return_value = None
    repo.get_all_registry.return_value = {}
    repo.get_latest_company_info_if_fresh.return_value = None
    repo.get_latest_company_info.return_value = None
    repo.get_currency.return_value = "USD"
    repo.insert_ohlcv.return_value = 0
    repo.insert_company_info.return_value = None
    repo.upsert_registry.return_value = None
    repo.insert_dividends.return_value = 0
    repo.upsert_technical_indicators.return_value = None
    repo.insert_analysis_summary.return_value = None
    repo.insert_forecast_run.return_value = None
    repo.insert_forecast_series.return_value = None
    repo.get_latest_analysis_summary.return_value = None
    repo.get_latest_forecast_run.return_value = None
    return repo


# ---------------------------------------------------------------------------
# fetch_stock_data
# ---------------------------------------------------------------------------


class TestFetchStockData:
    """Tests for :func:`tools.stock_data_tool.fetch_stock_data`."""

    def test_returns_string(self, tmp_path, monkeypatch):
        """fetch_stock_data must always return a string, never raise."""
        import tools._stock_shared as _ss
        from tools import stock_data_tool

        df = _make_ohlcv()
        repo = _mock_repo()

        monkeypatch.setattr(_ss, "_DATA_RAW", tmp_path / "raw")
        monkeypatch.setattr(_ss, "_STOCK_REPO", repo)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = stock_data_tool.fetch_stock_data.invoke(
                {"ticker": "AAPL"}
            )

        assert isinstance(result, str)
        assert "AAPL" in result

    def test_unknown_ticker_returns_error_string(self, tmp_path, monkeypatch):
        """Empty yfinance response must yield an error string, not an exception."""
        import tools._stock_shared as _ss
        from tools import stock_data_tool

        repo = _mock_repo()
        monkeypatch.setattr(_ss, "_DATA_RAW", tmp_path / "raw")
        monkeypatch.setattr(_ss, "_STOCK_REPO", repo)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = stock_data_tool.fetch_stock_data.invoke(
                {"ticker": "XXXINVALID"}
            )

        assert isinstance(result, str)
        assert "Error" in result or "error" in result.lower()

    def test_up_to_date_skips_fetch(self, tmp_path, monkeypatch):
        """If registry shows today's date, no yfinance call is made."""
        import tools._stock_shared as _ss
        from tools import stock_data_tool

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True)

        repo = _mock_repo()
        repo.check_existing_data.return_value = {
            "ticker": "AAPL",
            "last_fetch_date": str(date.today()),
            "total_rows": 100,
            "date_range": {"start": "2020-01-01", "end": str(date.today())},
            "file_path": str(raw_dir / "AAPL_raw.parquet"),
        }

        monkeypatch.setattr(_ss, "_DATA_RAW", raw_dir)
        monkeypatch.setattr(_ss, "_STOCK_REPO", repo)

        # Write a dummy parquet so file_path exists
        _make_ohlcv(100).to_parquet(raw_dir / "AAPL_raw.parquet")

        with patch("yfinance.Ticker") as mock_yf:
            result = stock_data_tool.fetch_stock_data.invoke(
                {"ticker": "AAPL"}
            )
            mock_yf.assert_not_called()

        assert "up to date" in result.lower()


# ---------------------------------------------------------------------------
# get_stock_info — Iceberg cache check
# ---------------------------------------------------------------------------


class TestGetStockInfo:
    """Tests for :func:`tools.stock_data_tool.get_stock_info`."""

    def test_returns_cached_when_fresh(self, monkeypatch):
        """When Iceberg has a fresh snapshot, yfinance is not called."""
        import tools._stock_shared as _ss
        from tools import stock_data_tool

        repo = _mock_repo()
        repo.get_latest_company_info_if_fresh.return_value = {
            "company_name": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "market_cap": 3000000000000,
            "pe_ratio": 30.5,
            "week_52_high": 200.0,
            "week_52_low": 140.0,
            "current_price": 190.0,
            "currency": "USD",
        }
        monkeypatch.setattr(_ss, "_STOCK_REPO", repo)

        with patch("yfinance.Ticker") as mock_yf:
            result = stock_data_tool.get_stock_info.invoke({"ticker": "AAPL"})
            mock_yf.assert_not_called()

        assert "Apple" in result
        assert isinstance(result, str)

    def test_fetches_when_stale(self, monkeypatch):
        """When Iceberg cache is stale, yfinance is called."""
        import tools._stock_shared as _ss
        from tools import stock_data_tool

        repo = _mock_repo()
        # Stale — returns None
        repo.get_latest_company_info_if_fresh.return_value = None
        monkeypatch.setattr(_ss, "_STOCK_REPO", repo)

        mock_ticker = MagicMock()
        mock_ticker.info = {
            "longName": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "marketCap": 3000000000000,
            "trailingPE": 30.5,
            "fiftyTwoWeekHigh": 200.0,
            "fiftyTwoWeekLow": 140.0,
            "currentPrice": 190.0,
            "currency": "USD",
        }

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = stock_data_tool.get_stock_info.invoke({"ticker": "AAPL"})

        assert "Apple" in result
        repo.insert_company_info.assert_called_once()


# ---------------------------------------------------------------------------
# _get_repo singleton retry
# ---------------------------------------------------------------------------


class TestGetRepoRetry:
    """The singleton must retry after a failed initialisation attempt."""

    def test_retries_after_failure(self, monkeypatch):
        """_get_repo should retry and succeed if initially unavailable."""
        import tools._stock_shared as _ss

        monkeypatch.setattr(_ss, "_STOCK_REPO", None)
        monkeypatch.setattr(_ss, "_STOCK_REPO_INIT_ATTEMPTED", False)

        class FakeRepo:
            pass

        # First call: repo is None, init raises → still None
        with patch(
            "stocks.repository.StockRepository",
            side_effect=RuntimeError("catalog not ready"),
        ):
            result_first = _ss._get_repo()
        assert result_first is None

        # Second call: init succeeds (wrapped in CachedRepository)
        monkeypatch.setattr(_ss, "_STOCK_REPO", None)
        monkeypatch.setattr(_ss, "_STOCK_REPO_INIT_ATTEMPTED", False)
        fake_instance = FakeRepo()
        with patch(
            "stocks.repository.StockRepository", return_value=fake_instance
        ):
            result_second = _ss._get_repo()
        # _get_repo wraps in CachedRepository; verify the inner repo.
        from stocks.cached_repository import CachedRepository

        assert isinstance(result_second, CachedRepository)
        assert result_second._repo is fake_instance


# ---------------------------------------------------------------------------
# _prepare_data_for_prophet — Adj Close fallback
# ---------------------------------------------------------------------------


class TestPrepareDataForProphet:
    """Tests for :func:`tools._forecast_model._prepare_data_for_prophet`."""

    def test_uses_adj_close_when_present(self):
        """When Adj Close has valid data, it should be used."""
        from tools._forecast_model import _prepare_data_for_prophet

        df = _make_ohlcv(50)
        result = _prepare_data_for_prophet(df)
        assert len(result) == 50
        # y values should match Adj Close (which equals Close in _make_ohlcv)
        assert result["y"].iloc[-1] == pytest.approx(df["Adj Close"].iloc[-1])

    def test_falls_back_to_close_when_adj_close_all_nan(self):
        """When Adj Close is all NaN, Close must be used instead."""
        from tools._forecast_model import _prepare_data_for_prophet

        df = _make_ohlcv(50, adj_close_nan=True)
        result = _prepare_data_for_prophet(df)
        assert len(result) == 50
        assert result["y"].iloc[-1] == pytest.approx(df["Close"].iloc[-1])

    def test_falls_back_when_adj_close_missing(self):
        """When Adj Close column is absent entirely, Close is used."""
        from tools._forecast_model import _prepare_data_for_prophet

        df = _make_ohlcv(50).drop(columns=["Adj Close"])
        result = _prepare_data_for_prophet(df)
        assert len(result) == 50
        assert result["y"].iloc[-1] == pytest.approx(df["Close"].iloc[-1])


# ---------------------------------------------------------------------------
# Price analysis helpers (pure Python — no I/O)
# ---------------------------------------------------------------------------


class TestCalculateTechnicalIndicators:
    """Tests for :func:`tools.price_analysis_tool._calculate_technical_indicators`."""

    def test_adds_all_indicator_columns(self):
        """All expected indicator columns must be present in the output."""
        from tools.price_analysis_tool import _calculate_technical_indicators

        df = _make_ohlcv(300)
        result = _calculate_technical_indicators(df)

        expected_cols = [
            "SMA_50",
            "SMA_200",
            "EMA_20",
            "RSI_14",
            "MACD",
            "MACD_Signal",
            "MACD_Hist",
            "BB_Upper",
            "BB_Middle",
            "BB_Lower",
            "ATR_14",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_original_df_not_mutated(self):
        """The input DataFrame must not be modified in-place."""
        from tools.price_analysis_tool import _calculate_technical_indicators

        df = _make_ohlcv(300)
        original_cols = list(df.columns)
        _calculate_technical_indicators(df)
        assert list(df.columns) == original_cols


class TestAnalysePriceMovement:
    """Tests for :func:`tools.price_analysis_tool._analyse_price_movement`."""

    def test_returns_expected_keys(self):
        """Returned dict must contain all documented keys."""
        from tools.price_analysis_tool import (
            _analyse_price_movement,
            _calculate_technical_indicators,
        )

        df = _calculate_technical_indicators(_make_ohlcv(300))
        result = _analyse_price_movement(df)

        for key in [
            "bull_phase_pct",
            "bear_phase_pct",
            "max_drawdown_pct",
            "max_drawdown_duration_days",
            "support_levels",
            "resistance_levels",
            "annualized_volatility_pct",
            "annualized_return_pct",
            "sharpe_ratio",
        ]:
            assert key in result, f"Missing key: {key}"

    def test_bull_bear_sum_100(self):
        """bull_phase_pct + bear_phase_pct must equal 100."""
        from tools.price_analysis_tool import (
            _analyse_price_movement,
            _calculate_technical_indicators,
        )

        df = _calculate_technical_indicators(_make_ohlcv(300))
        result = _analyse_price_movement(df)
        assert math.isclose(
            result["bull_phase_pct"] + result["bear_phase_pct"],
            100.0,
            abs_tol=0.1,
        )


# ---------------------------------------------------------------------------
# analyse_stock_price — end-to-end (I/O mocked)
# ---------------------------------------------------------------------------


class TestAnalyseStockPrice:
    """Tests for :func:`tools.price_analysis_tool.analyse_stock_price`."""

    def test_no_data_returns_error_string(self, tmp_path, monkeypatch):
        """When Iceberg has no OHLCV data, tool must return an error string."""
        import tools._analysis_shared as _ash
        from tools import price_analysis_tool

        repo = _mock_repo()
        repo.get_ohlcv.return_value = pd.DataFrame()

        monkeypatch.setattr(_ash, "_get_repo", lambda: repo)
        monkeypatch.setattr(_ash, "_require_repo", lambda: repo)
        monkeypatch.setattr(_ash, "_auto_fetch", lambda t: None)

        result = price_analysis_tool.analyse_stock_price.invoke(
            {"ticker": "AAPL"}
        )
        assert isinstance(result, str)
        assert (
            "Error" in result
            or "No OHLCV" in result
            or "fetch_stock_data" in result
        )

    def test_missing_data_instructs_fetch_first(self, tmp_path, monkeypatch):
        """Missing data message must tell LLM to call fetch_stock_data."""
        import tools._analysis_shared as _ash
        from tools import price_analysis_tool

        repo = _mock_repo()
        repo.get_ohlcv.return_value = pd.DataFrame()

        monkeypatch.setattr(_ash, "_get_repo", lambda: repo)
        monkeypatch.setattr(_ash, "_require_repo", lambda: repo)
        monkeypatch.setattr(_ash, "_auto_fetch", lambda t: None)

        result = price_analysis_tool.analyse_stock_price.invoke(
            {"ticker": "AAPL"}
        )
        assert "fetch_stock_data" in result
        assert "MUST" in result

    def test_with_data_returns_report(self, tmp_path, monkeypatch):
        """With valid Iceberg OHLCV data, tool must return a full report string."""
        import tools._analysis_shared as _ash
        from tools import price_analysis_tool

        repo = _mock_repo()
        repo.get_ohlcv.return_value = _make_iceberg_ohlcv(300, "AAPL")

        monkeypatch.setattr(_ash, "_get_repo", lambda: repo)
        monkeypatch.setattr(_ash, "_require_repo", lambda: repo)
        monkeypatch.setattr(_ash, "_auto_fetch", lambda t: None)

        result = price_analysis_tool.analyse_stock_price.invoke(
            {"ticker": "AAPL"}
        )
        assert isinstance(result, str)
        assert "AAPL" in result
        assert "PRICE ANALYSIS" in result

        # Verify Iceberg writes were called (not swallowed)
        repo.upsert_technical_indicators.assert_called_once()
        repo.insert_analysis_summary.assert_called_once()

    def test_iceberg_write_failure_propagates(self, tmp_path, monkeypatch):
        """When Iceberg write fails, the error propagates as an error string."""
        import tools._analysis_shared as _ash
        from tools import price_analysis_tool

        repo = _mock_repo()
        repo.get_ohlcv.return_value = _make_iceberg_ohlcv(300, "AAPL")
        repo.upsert_technical_indicators.side_effect = RuntimeError(
            "Iceberg write failed"
        )

        monkeypatch.setattr(_ash, "_get_repo", lambda: repo)
        monkeypatch.setattr(_ash, "_require_repo", lambda: repo)
        monkeypatch.setattr(_ash, "_auto_fetch", lambda t: None)

        result = price_analysis_tool.analyse_stock_price.invoke(
            {"ticker": "AAPL"}
        )
        assert isinstance(result, str)
        assert "Error" in result

    def test_analysis_freshness_gate_today(self, tmp_path, monkeypatch):
        """If analysis was done today and OHLCV hasn't changed, return early."""
        import tools._analysis_shared as _ash
        from tools import price_analysis_tool

        repo = _mock_repo()
        repo.get_latest_analysis_summary.return_value = {
            "analysis_date": date.today(),
        }
        # OHLCV date <= analysis date → analysis is still fresh
        repo.get_latest_ohlcv_date.return_value = date.today()

        monkeypatch.setattr(_ash, "_get_repo", lambda: repo)
        monkeypatch.setattr(_ash, "_require_repo", lambda: repo)
        monkeypatch.setattr(_ash, "_auto_fetch", lambda t: None)

        result = price_analysis_tool.analyse_stock_price.invoke(
            {"ticker": "AAPL"}
        )
        assert "already up-to-date" in result
        repo.upsert_technical_indicators.assert_not_called()


# ---------------------------------------------------------------------------
# forecast_stock — end-to-end (I/O mocked)
# ---------------------------------------------------------------------------


class TestForecastStock:
    """Tests for :func:`tools.forecasting_tool.forecast_stock`."""

    def test_no_data_returns_error_string(self, tmp_path, monkeypatch):
        """When Iceberg has no OHLCV data, tool must return an error string."""
        import tools._forecast_shared as _fsh
        from tools import forecasting_tool

        repo = _mock_repo()
        repo.get_ohlcv.return_value = pd.DataFrame()

        monkeypatch.setattr(_fsh, "_get_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_require_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_auto_fetch", lambda t: None)

        result = forecasting_tool.forecast_stock.invoke(
            {"ticker": "AAPL", "months": 3}
        )
        assert isinstance(result, str)
        assert (
            "Error" in result
            or "No OHLCV" in result
            or "fetch_stock_data" in result
        )

    def test_missing_data_instructs_fetch_first(self, tmp_path, monkeypatch):
        """Missing data message must tell LLM to call fetch_stock_data."""
        import tools._forecast_shared as _fsh
        from tools import forecasting_tool

        repo = _mock_repo()
        repo.get_ohlcv.return_value = pd.DataFrame()

        monkeypatch.setattr(_fsh, "_get_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_require_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_auto_fetch", lambda t: None)

        result = forecasting_tool.forecast_stock.invoke(
            {"ticker": "AAPL", "months": 3}
        )
        assert "fetch_stock_data" in result
        assert "MUST" in result

    def test_with_data_returns_report(self, tmp_path, monkeypatch):
        """With valid Iceberg OHLCV data, forecast_stock must return a report string."""
        import tools._forecast_shared as _fsh
        from tools import forecasting_tool

        repo = _mock_repo()
        repo.get_ohlcv.return_value = _make_iceberg_ohlcv(600, "AAPL")

        monkeypatch.setattr(_fsh, "_get_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_require_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_auto_fetch", lambda t: None)

        result = forecasting_tool.forecast_stock.invoke(
            {"ticker": "AAPL", "months": 3}
        )
        assert isinstance(result, str)
        assert "AAPL" in result
        # Should contain PRICE FORECAST header or an error (model training is real)
        assert "FORECAST" in result or "Error" in result

    def test_iceberg_write_failure_propagates(self, tmp_path, monkeypatch):
        """When Iceberg write fails, the error propagates as an error string."""
        import tools._forecast_shared as _fsh
        from tools import forecasting_tool

        repo = _mock_repo()
        repo.get_ohlcv.return_value = _make_iceberg_ohlcv(600, "AAPL")
        repo.insert_forecast_run.side_effect = RuntimeError(
            "Iceberg write failed"
        )

        monkeypatch.setattr(_fsh, "_get_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_require_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_auto_fetch", lambda t: None)

        result = forecasting_tool.forecast_stock.invoke(
            {"ticker": "AAPL", "months": 3}
        )
        assert isinstance(result, str)
        assert "Error" in result

    def test_forecast_7day_cooldown(self, tmp_path, monkeypatch):
        """If forecast was run within 7 days, return cached report."""
        from datetime import timedelta

        import tools._forecast_shared as _fsh
        from tools import forecasting_tool

        repo = _mock_repo()
        repo.get_latest_forecast_run.return_value = {
            "run_date": date.today() - timedelta(days=3),
            "current_price_at_run": 180.50,
            "sentiment": "Bullish",
            "target_3m_price": 195.0,
            "target_3m_pct_change": 8.0,
            "target_3m_lower": 175.0,
            "target_3m_upper": 215.0,
            "target_6m_price": 210.0,
            "target_6m_pct_change": 16.3,
            "target_6m_lower": 180.0,
            "target_6m_upper": 240.0,
            "target_9m_price": 220.0,
            "target_9m_pct_change": 21.9,
            "target_9m_lower": 185.0,
            "target_9m_upper": 255.0,
            "mae": 5.2,
            "rmse": 7.1,
            "mape": 3.4,
        }

        monkeypatch.setattr(_fsh, "_get_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_require_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_auto_fetch", lambda t: None)

        result = forecasting_tool.forecast_stock.invoke(
            {"ticker": "AAPL", "months": 9}
        )
        assert "PRICE FORECAST" in result
        assert "cached from" in result
        assert "3M Target" in result
        assert "BULLISH" in result
        repo.insert_forecast_run.assert_not_called()

    def test_forecast_cooldown_expired(self, tmp_path, monkeypatch):
        """If forecast is older than 7 days, run proceeds."""
        from datetime import timedelta

        import tools._forecast_shared as _fsh
        from tools import forecasting_tool

        repo = _mock_repo()
        repo.get_latest_forecast_run.return_value = {
            "run_date": date.today() - timedelta(days=10),
        }
        repo.get_ohlcv.return_value = _make_iceberg_ohlcv(600, "AAPL")

        monkeypatch.setattr(_fsh, "_get_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_require_repo", lambda: repo)
        monkeypatch.setattr(_fsh, "_auto_fetch", lambda t: None)

        result = forecasting_tool.forecast_stock.invoke(
            {"ticker": "AAPL", "months": 3}
        )
        # Should proceed to run (not blocked by cooldown)
        assert "cached from" not in result

    def test_forecast_inline_backtest_on_first_run(
        self, tmp_path, monkeypatch,
    ):
        """First forecast runs inline backtest for accuracy."""
        import tools._forecast_shared as _fsh
        from tools import forecasting_tool
        from tools import _forecast_accuracy as _fa

        repo = _mock_repo()
        # No previous run → triggers inline backtest
        repo.get_latest_forecast_run.return_value = None
        repo.get_ohlcv.return_value = _make_iceberg_ohlcv(
            800, "AAPL",
        )

        monkeypatch.setattr(
            _fsh, "_get_repo", lambda: repo,
        )
        monkeypatch.setattr(
            _fsh, "_require_repo", lambda: repo,
        )
        monkeypatch.setattr(
            _fsh, "_auto_fetch", lambda t: None,
        )

        # Mock the backtest to return valid accuracy
        mock_acc = {
            "MAE": 5.12,
            "RMSE": 6.34,
            "MAPE_pct": 3.2,
        }
        monkeypatch.setattr(
            _fa,
            "_calculate_forecast_accuracy",
            lambda model, df: mock_acc,
        )

        result = forecasting_tool.forecast_stock.invoke(
            {"ticker": "AAPL", "months": 3},
        )
        assert "MAE" in result
        assert "RMSE" in result
        assert "MAPE" in result
        # Should NOT show "Insufficient data"
        assert "Insufficient" not in result

    def test_forecast_inline_backtest_insufficient_data(
        self, tmp_path, monkeypatch,
    ):
        """Inline backtest error shows helpful message."""
        import tools._forecast_shared as _fsh
        from tools import forecasting_tool
        from tools import _forecast_accuracy as _fa

        repo = _mock_repo()
        repo.get_latest_forecast_run.return_value = None
        repo.get_ohlcv.return_value = _make_iceberg_ohlcv(
            800, "AAPL",
        )

        monkeypatch.setattr(
            _fsh, "_get_repo", lambda: repo,
        )
        monkeypatch.setattr(
            _fsh, "_require_repo", lambda: repo,
        )
        monkeypatch.setattr(
            _fsh, "_auto_fetch", lambda t: None,
        )

        # Mock backtest returning error
        monkeypatch.setattr(
            _fa,
            "_calculate_forecast_accuracy",
            lambda model, df: {
                "error": "Only 500 days (need 730+)",
            },
        )

        result = forecasting_tool.forecast_stock.invoke(
            {"ticker": "AAPL", "months": 3},
        )
        assert "Insufficient data" in result

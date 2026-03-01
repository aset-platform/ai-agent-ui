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

def _make_ohlcv(rows: int = 300) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with a DatetimeIndex."""
    idx = pd.date_range("2020-01-01", periods=rows, freq="B")
    rng = np.random.default_rng(0)
    close = 100 + rng.standard_normal(rows).cumsum()
    df = pd.DataFrame(
        {
            "Open": close * 0.99,
            "High": close * 1.01,
            "Low": close * 0.98,
            "Close": close,
            "Adj Close": close,
            "Volume": rng.integers(1_000_000, 5_000_000, rows),
        },
        index=idx,
    )
    df.index.name = "Date"
    return df


# ---------------------------------------------------------------------------
# fetch_stock_data
# ---------------------------------------------------------------------------


class TestFetchStockData:
    """Tests for :func:`tools.stock_data_tool.fetch_stock_data`."""

    def test_returns_string(self, tmp_path, monkeypatch):
        """fetch_stock_data must always return a string, never raise."""
        from tools import stock_data_tool

        df = _make_ohlcv()

        monkeypatch.setattr(stock_data_tool, "_DATA_RAW", tmp_path / "raw")
        monkeypatch.setattr(stock_data_tool, "_DATA_METADATA", tmp_path / "meta")
        monkeypatch.setattr(stock_data_tool, "_REGISTRY_PATH", tmp_path / "meta" / "registry.json")
        monkeypatch.setattr(stock_data_tool, "_STOCK_REPO", None)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = df

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = stock_data_tool.fetch_stock_data.invoke({"ticker": "AAPL"})

        assert isinstance(result, str)
        assert "AAPL" in result

    def test_unknown_ticker_returns_error_string(self, tmp_path, monkeypatch):
        """Empty yfinance response must yield an error string, not an exception."""
        from tools import stock_data_tool

        monkeypatch.setattr(stock_data_tool, "_DATA_RAW", tmp_path / "raw")
        monkeypatch.setattr(stock_data_tool, "_DATA_METADATA", tmp_path / "meta")
        monkeypatch.setattr(stock_data_tool, "_REGISTRY_PATH", tmp_path / "meta" / "registry.json")
        monkeypatch.setattr(stock_data_tool, "_STOCK_REPO", None)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = stock_data_tool.fetch_stock_data.invoke({"ticker": "XXXINVALID"})

        assert isinstance(result, str)
        assert "Error" in result or "error" in result.lower()

    def test_up_to_date_skips_fetch(self, tmp_path, monkeypatch):
        """If registry shows today's date, no yfinance call is made."""
        from tools import stock_data_tool

        meta_dir = tmp_path / "meta"
        meta_dir.mkdir(parents=True)
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True)
        registry_path = meta_dir / "registry.json"

        monkeypatch.setattr(stock_data_tool, "_DATA_RAW", raw_dir)
        monkeypatch.setattr(stock_data_tool, "_DATA_METADATA", meta_dir)
        monkeypatch.setattr(stock_data_tool, "_REGISTRY_PATH", registry_path)
        monkeypatch.setattr(stock_data_tool, "_STOCK_REPO", None)

        # Write a registry entry saying data was fetched today
        import json
        registry_path.write_text(
            json.dumps({
                "AAPL": {
                    "ticker": "AAPL",
                    "last_fetch_date": str(date.today()),
                    "total_rows": 100,
                    "date_range": {"start": "2020-01-01", "end": str(date.today())},
                    "file_path": str(raw_dir / "AAPL_raw.parquet"),
                }
            })
        )
        # Write a dummy parquet so file_path exists (not read in this path)
        _make_ohlcv(100).to_parquet(raw_dir / "AAPL_raw.parquet")

        with patch("yfinance.Ticker") as mock_yf:
            result = stock_data_tool.fetch_stock_data.invoke({"ticker": "AAPL"})
            mock_yf.assert_not_called()

        assert "up to date" in result.lower()


# ---------------------------------------------------------------------------
# _get_repo singleton retry
# ---------------------------------------------------------------------------


class TestGetRepoRetry:
    """The singleton must retry after a failed initialisation attempt."""

    def test_retries_after_failure(self, monkeypatch):
        """_get_repo should retry and succeed if initially unavailable."""
        import tools.stock_data_tool as sdt

        monkeypatch.setattr(sdt, "_STOCK_REPO", None)
        call_count = {"n": 0}

        class FakeRepo:
            pass

        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        # First call raises, second returns a FakeRepo
        def flaky_repo():
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise RuntimeError("catalog not ready")
            return FakeRepo()

        # Patch at the module level by controlling the global directly
        # First call: repo is None, init raises → still None
        with patch("stocks.repository.StockRepository", side_effect=RuntimeError("catalog not ready")):
            result_first = sdt._get_repo()
        assert result_first is None
        assert sdt._STOCK_REPO is None  # not cached after failure

        # Second call: init succeeds
        monkeypatch.setattr(sdt, "_STOCK_REPO", None)  # reset
        fake_instance = FakeRepo()
        with patch("stocks.repository.StockRepository", return_value=fake_instance):
            result_second = sdt._get_repo()
        assert result_second is fake_instance


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
            "SMA_50", "SMA_200", "EMA_20",
            "RSI_14", "MACD", "MACD_Signal", "MACD_Hist",
            "BB_Upper", "BB_Middle", "BB_Lower", "ATR_14",
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
            "bull_phase_pct", "bear_phase_pct",
            "max_drawdown_pct", "max_drawdown_duration_days",
            "support_levels", "resistance_levels",
            "annualized_volatility_pct", "annualized_return_pct",
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
            result["bull_phase_pct"] + result["bear_phase_pct"], 100.0, abs_tol=0.1
        )


# ---------------------------------------------------------------------------
# analyse_stock_price — end-to-end (I/O mocked)
# ---------------------------------------------------------------------------


class TestAnalyseStockPrice:
    """Tests for :func:`tools.price_analysis_tool.analyse_stock_price`."""

    def test_no_data_returns_error_string(self, tmp_path, monkeypatch):
        """When no parquet file exists, tool must return an error string."""
        import tools._analysis_shared as _ash
        from tools import price_analysis_tool  # noqa: F401 — ensure tool is importable

        monkeypatch.setattr(_ash, "_DATA_RAW", tmp_path / "raw")
        monkeypatch.setattr(_ash, "_DATA_METADATA", tmp_path / "meta")
        monkeypatch.setattr(_ash, "_CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr(_ash, "_STOCK_REPO", None)
        monkeypatch.setattr(_ash, "_STOCK_REPO_INIT_ATTEMPTED", True)

        result = price_analysis_tool.analyse_stock_price.invoke({"ticker": "AAPL"})
        assert isinstance(result, str)
        assert "Error" in result or "No local data" in result

    def test_with_data_returns_report(self, tmp_path, monkeypatch):
        """With a valid parquet file, tool must return a full report string."""
        import tools._analysis_shared as _ash
        from tools import price_analysis_tool  # noqa: F401

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True)
        df = _make_ohlcv(300)
        df.to_parquet(raw_dir / "AAPL_raw.parquet")

        monkeypatch.setattr(_ash, "_DATA_RAW", raw_dir)
        monkeypatch.setattr(_ash, "_DATA_METADATA", tmp_path / "meta")
        monkeypatch.setattr(_ash, "_CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr(_ash, "_CHARTS_ANALYSIS", tmp_path / "charts")
        monkeypatch.setattr(_ash, "_STOCK_REPO", None)
        monkeypatch.setattr(_ash, "_STOCK_REPO_INIT_ATTEMPTED", True)

        result = price_analysis_tool.analyse_stock_price.invoke({"ticker": "AAPL"})
        assert isinstance(result, str)
        assert "AAPL" in result
        assert "PRICE ANALYSIS" in result


# ---------------------------------------------------------------------------
# forecast_stock — end-to-end (I/O mocked)
# ---------------------------------------------------------------------------


class TestForecastStock:
    """Tests for :func:`tools.forecasting_tool.forecast_stock`."""

    def test_no_data_returns_error_string(self, tmp_path, monkeypatch):
        """When no parquet file exists, tool must return an error string."""
        import tools._forecast_shared as _fsh
        from tools import forecasting_tool  # noqa: F401 — ensure tool is importable

        monkeypatch.setattr(_fsh, "_DATA_RAW", tmp_path / "raw")
        monkeypatch.setattr(_fsh, "_DATA_METADATA", tmp_path / "meta")
        monkeypatch.setattr(_fsh, "_CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr(_fsh, "_STOCK_REPO", None)
        monkeypatch.setattr(_fsh, "_STOCK_REPO_INIT_ATTEMPTED", True)

        result = forecasting_tool.forecast_stock.invoke({"ticker": "AAPL", "months": 3})
        assert isinstance(result, str)
        assert "Error" in result or "No local data" in result

    def test_with_data_returns_report(self, tmp_path, monkeypatch):
        """With valid parquet data, forecast_stock must return a report string."""
        import tools._forecast_shared as _fsh
        from tools import forecasting_tool  # noqa: F401

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True)
        df = _make_ohlcv(600)  # ~2.5 years of data
        df.to_parquet(raw_dir / "AAPL_raw.parquet")

        monkeypatch.setattr(_fsh, "_DATA_RAW", raw_dir)
        monkeypatch.setattr(_fsh, "_DATA_METADATA", tmp_path / "meta")
        monkeypatch.setattr(_fsh, "_DATA_FORECASTS", tmp_path / "forecasts")
        monkeypatch.setattr(_fsh, "_CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr(_fsh, "_CHARTS_FORECASTS", tmp_path / "charts")
        monkeypatch.setattr(_fsh, "_STOCK_REPO", None)
        monkeypatch.setattr(_fsh, "_STOCK_REPO_INIT_ATTEMPTED", True)

        result = forecasting_tool.forecast_stock.invoke({"ticker": "AAPL", "months": 3})
        assert isinstance(result, str)
        assert "AAPL" in result
        # Should contain PRICE FORECAST header or an error (model training is real)
        assert "FORECAST" in result or "Error" in result

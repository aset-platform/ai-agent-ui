"""Unit tests for :mod:`dashboard.services.stock_refresh`.

Focuses on the adj_close NaN-fill step added to ``_full_ohlcv_refresh``
and the ``run_full_refresh`` pipeline.  yfinance, Iceberg, and Prophet
are fully mocked -- the suite runs offline.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# Ensure backend/ is on sys.path so ``tools`` package resolves
_BACKEND_DIR = str(Path(__file__).parent.parent.parent / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _make_yf_df(rows: int = 50) -> pd.DataFrame:
    """Return a minimal yfinance-shaped OHLCV DataFrame."""
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


def _make_iceberg_ohlcv(
    rows: int = 50,
    ticker: str = "AAPL",
    adj_close_nan: bool = False,
) -> pd.DataFrame:
    """Return a DataFrame like ``get_ohlcv()`` output."""
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
            "adj_close": ([float("nan")] * rows if adj_close_nan else close),
            "volume": rng.integers(1_000_000, 5_000_000, rows),
        }
    )


def _mock_repo(ice_df: pd.DataFrame | None = None):
    """Return a MagicMock configured as StockRepository."""
    repo = MagicMock()
    repo.insert_ohlcv.return_value = 0
    repo.update_ohlcv_adj_close.return_value = 0
    repo.insert_forecast_run.return_value = None
    repo.insert_forecast_series.return_value = None
    if ice_df is not None:
        repo.get_ohlcv.return_value = ice_df
    else:
        repo.get_ohlcv.return_value = pd.DataFrame()
    return repo


# ===================================================================
# _full_ohlcv_refresh -- adj_close fill step
# ===================================================================


class TestFullOhlcvRefreshAdjCloseFill:
    """Tests for adj_close NaN-fill in _full_ohlcv_refresh."""

    def _run_refresh(self, tmp_path, yf_df, ice_df, repo=None):
        """Run _full_ohlcv_refresh with all I/O mocked."""
        if repo is None:
            repo = _mock_repo(ice_df)

        mock_yf_ticker = MagicMock()
        mock_yf_ticker.history.return_value = yf_df

        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        import tools._stock_registry as _sr
        import tools._stock_shared as _ss

        with (
            patch(
                "yfinance.Ticker",
                return_value=mock_yf_ticker,
            ),
            patch.object(
                _sr,
                "_check_existing_data",
                return_value=None,
            ),
            patch.object(
                _ss,
                "_require_repo",
                return_value=repo,
            ),
            patch.object(
                _ss,
                "_parquet_path",
                return_value=raw_dir / "AAPL_raw.parquet",
            ),
            patch.object(_sr, "_update_registry"),
        ):
            from dashboard.services.stock_refresh import (
                _full_ohlcv_refresh,
            )

            _full_ohlcv_refresh("AAPL")

        return repo

    def test_calls_update_when_nan(self, tmp_path):
        """update_ohlcv_adj_close called when adj_close NaN."""
        yf_df = _make_yf_df(50)
        ice_df = _make_iceberg_ohlcv(50, "AAPL", adj_close_nan=True)

        repo = self._run_refresh(tmp_path, yf_df, ice_df)

        repo.update_ohlcv_adj_close.assert_called_once()
        args = repo.update_ohlcv_adj_close.call_args
        assert args[0][0] == "AAPL"
        assert len(args[0][1]) == 50

    def test_skips_update_when_no_nan(self, tmp_path):
        """No update when adj_close is fully populated."""
        yf_df = _make_yf_df(50)
        ice_df = _make_iceberg_ohlcv(50, "AAPL", adj_close_nan=False)

        repo = self._run_refresh(tmp_path, yf_df, ice_df)

        repo.update_ohlcv_adj_close.assert_not_called()

    def test_fill_values_are_close(self, tmp_path):
        """fill_map values must equal the close prices."""
        yf_df = _make_yf_df(10)
        ice_df = _make_iceberg_ohlcv(10, "AAPL", adj_close_nan=True)
        # Save lookup before refresh mutates ice_df
        date_to_close = dict(zip(ice_df["date"], ice_df["close"]))

        repo = _mock_repo(ice_df)
        self._run_refresh(tmp_path, yf_df, ice_df, repo=repo)

        fill_map = repo.update_ohlcv_adj_close.call_args[0][1]
        for d, val in fill_map.items():
            assert val == pytest.approx(date_to_close[d])

    def test_partial_nan_fills_only_nan(self, tmp_path):
        """Only NaN adj_close rows should be in fill_map."""
        ice_df = _make_iceberg_ohlcv(20, "AAPL", adj_close_nan=False)
        # Set first 5 rows to NaN
        ice_df.loc[:4, "adj_close"] = float("nan")

        yf_df = _make_yf_df(20)
        repo = _mock_repo(ice_df)
        self._run_refresh(tmp_path, yf_df, ice_df, repo=repo)

        fill_map = repo.update_ohlcv_adj_close.call_args[0][1]
        assert len(fill_map) == 5

"""Tests for XGBoost ensemble correction (Phase 3b).

Covers:
- Ensemble corrects Prophet forecast
- Graceful fallback with no tech indicators
- Feature flag gating
- Handles missing feature columns
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd


def _make_train_df(rows: int = 500) -> pd.DataFrame:
    """Simulate train_df from _train_prophet_model."""
    idx = pd.date_range("2022-01-01", periods=rows, freq="B")
    rng = np.random.default_rng(42)
    close = 150 + rng.standard_normal(rows).cumsum()
    return pd.DataFrame(
        {
            "ds": idx,
            "y": close,
            "vix": 20 + rng.standard_normal(rows) * 3,
            "index_return": rng.standard_normal(rows),
            "sentiment": rng.uniform(-0.5, 0.5, rows),
        }
    )


def _make_prophet_df(rows: int = 500) -> pd.DataFrame:
    """Simulate prophet_df (ds + y only)."""
    idx = pd.date_range("2022-01-01", periods=rows, freq="B")
    rng = np.random.default_rng(42)
    close = 150 + rng.standard_normal(rows).cumsum()
    return pd.DataFrame({"ds": idx, "y": close})


def _make_forecast_df(months: int = 9) -> pd.DataFrame:
    """Simulate forecast_df from _generate_forecast."""
    days = months * 30
    idx = pd.date_range("2024-01-01", periods=days, freq="D")
    rng = np.random.default_rng(99)
    yhat = 200 + rng.standard_normal(days).cumsum() * 0.5
    return pd.DataFrame(
        {
            "ds": idx,
            "yhat": yhat,
            "yhat_lower": yhat - 10,
            "yhat_upper": yhat + 10,
        }
    )


def _make_raw_ohlcv() -> pd.DataFrame:
    """Simulate repo.get_ohlcv(ticker) — the real call site behind
    ensemble_forecast's tech-indicator step (via tools._analysis_
    shared.compute_indicators -> _load_ohlcv), not a StockRepository
    get_technical_indicators()/get_ohlcv_batch()-style batch method.

    Spans business days from well before _make_train_df's 2022-01-01
    start (>=200 rows of SMA_200 warmup) through TODAY, so
    _is_ohlcv_stale never triggers _auto_fetch's real yfinance call.
    Lowercase columns matching StockRepository.get_ohlcv's real
    shape (date/open/high/low/close/volume).
    """
    idx = pd.date_range(
        "2021-01-01", pd.Timestamp.now().normalize(), freq="B",
    )
    rng = np.random.default_rng(42)
    close = 150 + rng.standard_normal(len(idx)).cumsum()
    return pd.DataFrame(
        {
            "date": idx.strftime("%Y-%m-%d"),
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": rng.integers(
                1_000_000, 5_000_000, len(idx),
            ),
        }
    )


def _mock_prophet_model():
    """Create a mock Prophet model with predict()."""
    model = MagicMock()

    def _predict(df):
        n = len(df)
        rng = np.random.default_rng(0)
        if "y" in df.columns:
            yhat = df["y"].values + rng.standard_normal(n) * 2
        else:
            yhat = rng.standard_normal(n) * 2 + 200
        return pd.DataFrame({"yhat": yhat})

    model.predict.side_effect = _predict
    return model


@patch("tools._analysis_shared._require_repo")
def test_ensemble_corrects_forecast(mock_require):
    """Ensemble should modify yhat values."""
    repo = MagicMock()
    repo.get_ohlcv.return_value = _make_raw_ohlcv()
    mock_require.return_value = repo

    model = _mock_prophet_model()
    train_df = _make_train_df()
    prophet_df = _make_prophet_df()
    forecast_df = _make_forecast_df()

    original_yhat = forecast_df["yhat"].copy()

    from tools._forecast_ensemble import (
        ensemble_forecast,
    )

    result = ensemble_forecast(
        model,
        train_df,
        prophet_df,
        forecast_df,
        "AAPL",
    )

    assert result is not None
    assert len(result) == len(forecast_df)
    assert "yhat" in result.columns
    # yhat should be different after correction.
    assert not result["yhat"].equals(original_yhat)


@patch("tools._analysis_shared._require_repo")
def test_ensemble_graceful_fallback_no_tech(
    mock_require,
):
    """Returns None when no tech indicators exist."""
    repo = MagicMock()
    repo.get_ohlcv.return_value = pd.DataFrame()
    mock_require.return_value = repo

    model = _mock_prophet_model()
    train_df = _make_train_df()
    prophet_df = _make_prophet_df()
    forecast_df = _make_forecast_df()

    from tools._forecast_ensemble import (
        ensemble_forecast,
    )

    result = ensemble_forecast(
        model,
        train_df,
        prophet_df,
        forecast_df,
        "AAPL",
    )

    assert result is None


@patch("tools._analysis_shared._require_repo")
def test_ensemble_too_few_rows(mock_require):
    """Returns None with insufficient training rows."""
    repo = MagicMock()
    repo.get_ohlcv.return_value = _make_raw_ohlcv()
    mock_require.return_value = repo

    model = _mock_prophet_model()
    train_df = _make_train_df(rows=50)
    prophet_df = _make_prophet_df(rows=50)
    forecast_df = _make_forecast_df()

    from tools._forecast_ensemble import (
        ensemble_forecast,
    )

    result = ensemble_forecast(
        model,
        train_df,
        prophet_df,
        forecast_df,
        "AAPL",
    )

    assert result is None


@patch("tools._analysis_shared._require_repo")
def test_ensemble_preserves_shape(mock_require):
    """Output should have same shape and columns
    as input forecast_df."""
    repo = MagicMock()
    repo.get_ohlcv.return_value = _make_raw_ohlcv()
    mock_require.return_value = repo

    model = _mock_prophet_model()
    train_df = _make_train_df()
    prophet_df = _make_prophet_df()
    forecast_df = _make_forecast_df()

    from tools._forecast_ensemble import (
        ensemble_forecast,
    )

    result = ensemble_forecast(
        model,
        train_df,
        prophet_df,
        forecast_df,
        "AAPL",
    )

    assert result is not None
    assert list(result.columns) == list(forecast_df.columns)
    assert len(result) == len(forecast_df)

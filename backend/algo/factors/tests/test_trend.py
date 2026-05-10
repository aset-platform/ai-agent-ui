"""Trend factor tests."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.algo.factors.trend import compute_trend


def _ohlcv(n: int, drift: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    return pd.DataFrame({
        "bar_date": [
            date(2024, 1, 1) + timedelta(days=i) for i in range(n)
        ],
        "high": high, "low": low, "close": close,
    })


def test_trend_full_window() -> None:
    df = _ohlcv(260)
    out = compute_trend(df)
    last = out[df["bar_date"].iloc[-1]]
    assert 0.0 <= last["adx_14"] <= 100.0
    assert isinstance(last["sma200_slope"], float)
    assert isinstance(last["distance_from_sma200"], float)


def test_trend_short_history_returns_nan() -> None:
    df = _ohlcv(50)
    out = compute_trend(df)
    last = out[df["bar_date"].iloc[-1]]
    assert math.isnan(last["sma200_slope"])
    assert math.isnan(last["distance_from_sma200"])


def test_trend_uptrend_distance_positive() -> None:
    df = _ohlcv(260, drift=0.003)
    out = compute_trend(df)
    last = out[df["bar_date"].iloc[-1]]
    assert last["distance_from_sma200"] > 0

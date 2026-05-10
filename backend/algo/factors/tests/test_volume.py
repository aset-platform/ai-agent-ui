"""Volume factor tests."""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from backend.algo.factors.volume import compute_volume


def _df(close: list[float], volume: list[int]) -> pd.DataFrame:
    n = len(close)
    return pd.DataFrame({
        "bar_date": [
            date(2024, 1, 1) + timedelta(days=i) for i in range(n)
        ],
        "close": close, "volume": volume,
    })


def test_obv_nondecreasing_when_all_green() -> None:
    df = _df(list(range(1, 51)), [1000] * 50)
    out = compute_volume(df)
    last = out[df["bar_date"].iloc[-1]]
    assert last["obv"] == 49000


def test_volume_x_avg_20_at_average_is_one() -> None:
    df = _df([100.0] * 30, [1000] * 30)
    out = compute_volume(df)
    last = out[df["bar_date"].iloc[-1]]
    assert abs(last["volume_x_avg_20"] - 1.0) < 1e-9


def test_short_history_returns_nan() -> None:
    df = _df([100.0] * 5, [1000] * 5)
    out = compute_volume(df)
    last = out[df["bar_date"].iloc[-1]]
    assert math.isnan(last["volume_x_avg_20"])

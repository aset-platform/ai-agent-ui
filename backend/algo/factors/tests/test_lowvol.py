"""Low-vol factor tests."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.algo.factors.lowvol import compute_lowvol


def _series(close: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "bar_date": [
            date(2024, 1, 1) + timedelta(days=i)
            for i in range(len(close))
        ],
        "close": close,
    })


def test_realized_vol_60d_finite_for_long_history() -> None:
    rng = np.random.default_rng(0)
    close = (
        100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
    ).tolist()
    df = _series(close)
    nifty = _series([100 + i * 0.05 for i in range(300)])
    out = compute_lowvol(df, nifty)
    last = out[df["bar_date"].iloc[-1]]
    assert 0.0 < last["realized_vol_60d"] < 1.0


def test_beta_around_one_when_perfectly_correlated() -> None:
    n = 260
    rng = np.random.default_rng(7)
    nifty_close = (
        100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    ).tolist()
    df = _series([c * 1.5 for c in nifty_close])
    nifty = _series(nifty_close)
    out = compute_lowvol(df, nifty)
    last = out[df["bar_date"].iloc[-1]]
    assert 0.8 <= last["beta_to_nifty"] <= 1.2


def test_short_history_returns_nan() -> None:
    df = _series([100.0] * 30)
    nifty = _series([100.0] * 30)
    out = compute_lowvol(df, nifty)
    last = out[df["bar_date"].iloc[-1]]
    assert math.isnan(last["realized_vol_60d"])
    assert math.isnan(last["beta_to_nifty"])

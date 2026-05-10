"""Relative strength factor tests."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.algo.factors.relative_strength import (
    compute_relative_strength,
)


def _df(close: list[float]) -> pd.DataFrame:
    n = len(close)
    return pd.DataFrame({
        "bar_date": [
            date(2024, 1, 1) + timedelta(days=i) for i in range(n)
        ],
        "close": close,
    })


def test_rs_above_one_when_outperforming() -> None:
    n = 200
    nifty = _df(list(np.linspace(100, 110, n)))
    stock = _df(list(np.linspace(100, 130, n)))
    out = compute_relative_strength(
        stock, nifty, sector="IT",
        sector_indices={"IT": _df(list(np.linspace(100, 105, n)))},
    )
    last = out[stock["bar_date"].iloc[-1]]
    assert last["rs_vs_nifty_3m"] > 1.0
    assert last["rs_vs_sector_3m"] > 1.0


def test_rs_unknown_sector_returns_nan() -> None:
    n = 200
    nifty = _df(list(np.linspace(100, 110, n)))
    stock = _df(list(np.linspace(100, 130, n)))
    out = compute_relative_strength(
        stock, nifty, sector="UNKNOWN", sector_indices={},
    )
    last = out[stock["bar_date"].iloc[-1]]
    assert math.isnan(last["rs_vs_sector_3m"])


def test_rs_short_history_nan() -> None:
    df = _df([100.0] * 30)
    out = compute_relative_strength(
        df, df, sector="IT", sector_indices={},
    )
    last = out[df["bar_date"].iloc[-1]]
    assert math.isnan(last["rs_vs_nifty_3m"])
    assert math.isnan(last["rs_vs_nifty_6m"])

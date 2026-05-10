"""Momentum factor tests — table-driven + skip-month gate."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backend.algo.factors.momentum import compute_momentum


def _series(close: list[float]) -> pd.DataFrame:
    n = len(close)
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({"bar_date": dates, "close": close})


def test_mom_12_1_excludes_last_21_days() -> None:
    """If the last 21 days double, mom_12_1 must NOT see them."""
    base = list(np.linspace(100, 200, 232))
    spike = [400.0] * 21
    df = _series(base + spike)
    out = compute_momentum(df)
    last = out[df["bar_date"].iloc[-1]]
    assert 0.5 <= last["mom_12_1"] <= 1.5, (
        f"mom_12_1 leaked the post-skip-month spike: {last['mom_12_1']}"
    )


def test_mom_3_6_12_happy_path() -> None:
    n = 260
    close = list(np.linspace(100, 130, n))
    df = _series(close)
    out = compute_momentum(df)
    last = out[df["bar_date"].iloc[-1]]
    assert not math.isnan(last["mom_12_1"])
    assert not math.isnan(last["mom_6_1"])
    assert not math.isnan(last["mom_3_1"])


def test_prox_52w_at_high_is_one() -> None:
    n = 260
    df = _series(list(np.linspace(100, 200, n)))
    out = compute_momentum(df)
    last = out[df["bar_date"].iloc[-1]]
    assert abs(last["prox_52w"] - 1.0) < 1e-6


def test_prox_52w_below_high() -> None:
    close = list(np.linspace(100, 200, 252)) + [150.0]
    df = _series(close)
    out = compute_momentum(df)
    last = out[df["bar_date"].iloc[-1]]
    assert 0.7 <= last["prox_52w"] <= 0.8


def test_short_history_returns_nan_safely() -> None:
    df = _series(list(np.linspace(100, 110, 30)))
    out = compute_momentum(df)
    last = out[df["bar_date"].iloc[-1]]
    assert math.isnan(last["mom_12_1"])
    assert math.isnan(last["mom_6_1"])
    assert math.isnan(last["mom_3_1"])

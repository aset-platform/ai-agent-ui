"""Momentum factors with mandatory skip-month convention.

Per research §3:
    mom_12_1 = close[t-21] / close[t-252] - 1
    mom_6_1  = close[t-21] / close[t-126] - 1
    mom_3_1  = close[t-21] / close[t-63]  - 1
    prox_52w = close[t]    / max(close[t-252:t])

The skip-month gate (21 trading days) sidesteps the 1-month
mean-reversion effect well-documented in the academic momentum
literature (Jegadeesh & Titman 1993).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

SKIP_DAYS = 21
LOOKBACK_12 = 252
LOOKBACK_6 = 126
LOOKBACK_3 = 63
PROX_WINDOW = 252


def _ratio(num: float, den: float) -> float:
    if den == 0 or np.isnan(num) or np.isnan(den):
        return float("nan")
    return float(num / den - 1.0)


def compute_momentum(
    history: pd.DataFrame,
) -> dict[date, dict[str, float]]:
    if history.empty:
        return {}
    h = history.sort_values("bar_date").reset_index(drop=True)
    closes = h["close"].astype(float).to_numpy()
    dates = h["bar_date"].tolist()
    n = len(closes)
    out: dict[date, dict[str, float]] = {}
    for i in range(n):
        idx_skip = i - SKIP_DAYS
        mom_12 = (
            _ratio(closes[idx_skip], closes[i - LOOKBACK_12])
            if i >= LOOKBACK_12 else float("nan")
        )
        mom_6 = (
            _ratio(closes[idx_skip], closes[i - LOOKBACK_6])
            if i >= LOOKBACK_6 else float("nan")
        )
        mom_3 = (
            _ratio(closes[idx_skip], closes[i - LOOKBACK_3])
            if i >= LOOKBACK_3 + SKIP_DAYS else float("nan")
        )
        prox = (
            float(
                closes[i]
                / closes[i - PROX_WINDOW + 1: i + 1].max()
            )
            if i >= PROX_WINDOW - 1 else float("nan")
        )
        out[dates[i]] = {
            "mom_12_1": mom_12,
            "mom_6_1": mom_6,
            "mom_3_1": mom_3,
            "prox_52w": prox,
        }
    return out

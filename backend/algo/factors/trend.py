"""Trend factors: ADX(14), SMA200 slope, distance from SMA200.

Per research §3:
    adx_14               = ta.trend.ADXIndicator(...).adx()
    sma200_slope         = (sma200[t] - sma200[t-21]) / sma200[t-21]
    distance_from_sma200 = (close[t] - sma200[t])    / sma200[t]
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

ADX_WINDOW = 14
SMA_WINDOW = 200
SLOPE_LOOKBACK = 21


def compute_trend(history: pd.DataFrame) -> dict[date, dict[str, float]]:
    from ta.trend import ADXIndicator

    h = history.sort_values("bar_date").reset_index(drop=True)
    if h.empty:
        return {}
    n = len(h)
    close = h["close"].astype(float)
    high = h["high"].astype(float)
    low = h["low"].astype(float)

    if n >= ADX_WINDOW + 1:
        adx = ADXIndicator(
            high=high, low=low, close=close, window=ADX_WINDOW,
        ).adx().to_numpy()
    else:
        adx = np.full(n, float("nan"))

    sma200 = (
        close.rolling(SMA_WINDOW, min_periods=SMA_WINDOW)
        .mean()
        .to_numpy()
    )
    slope = np.full(n, float("nan"))
    dist = np.full(n, float("nan"))
    for i in range(n):
        if not np.isnan(sma200[i]):
            dist[i] = float((close.iloc[i] - sma200[i]) / sma200[i])
            j = i - SLOPE_LOOKBACK
            if (
                j >= 0
                and not np.isnan(sma200[j])
                and sma200[j] != 0
            ):
                slope[i] = float(
                    (sma200[i] - sma200[j]) / sma200[j]
                )

    dates = h["bar_date"].tolist()
    return {
        dates[i]: {
            "adx_14": (
                float(adx[i])
                if not np.isnan(adx[i]) else float("nan")
            ),
            "sma200_slope": (
                float(slope[i])
                if not np.isnan(slope[i]) else float("nan")
            ),
            "distance_from_sma200": (
                float(dist[i])
                if not np.isnan(dist[i]) else float("nan")
            ),
        }
        for i in range(n)
    }

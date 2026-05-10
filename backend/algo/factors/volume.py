"""Volume factors: OBV, volume_x_avg_20, up_down_vol_ratio_20.

Per research §3:
    obv                  = cumsum(sign(close.diff()) * volume)
    volume_x_avg_20      = volume[t] / mean(volume[-20:])
    up_down_vol_ratio_20 = sum(vol on green days)
                           / sum(vol on red days)   (trailing 20)
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

VOL_AVG_WINDOW = 20
UP_DOWN_WINDOW = 20


def compute_volume(history: pd.DataFrame) -> dict[date, dict[str, float]]:
    h = history.sort_values("bar_date").reset_index(drop=True)
    if h.empty:
        return {}
    close = h["close"].astype(float).to_numpy()
    volume = h["volume"].astype(float).to_numpy()
    n = len(h)

    diff = np.diff(close, prepend=close[0])
    direction = np.sign(diff)
    direction[0] = 0
    obv = np.cumsum(direction * volume)

    avg = (
        pd.Series(volume)
        .rolling(VOL_AVG_WINDOW, min_periods=VOL_AVG_WINDOW)
        .mean()
        .to_numpy()
    )
    vol_x = np.where(avg > 0, volume / avg, np.nan)

    udr = np.full(n, float("nan"))
    for i in range(n):
        if i + 1 < UP_DOWN_WINDOW:
            continue
        sl = slice(i + 1 - UP_DOWN_WINDOW, i + 1)
        d = direction[sl]
        v = volume[sl]
        up = float(v[d > 0].sum())
        dn = float(v[d < 0].sum())
        udr[i] = up / dn if dn > 0 else float("inf")

    dates = h["bar_date"].tolist()
    return {
        dates[i]: {
            "obv": float(obv[i]),
            "volume_x_avg_20": (
                float(vol_x[i])
                if not np.isnan(vol_x[i]) else float("nan")
            ),
            "up_down_vol_ratio_20": (
                float(udr[i])
                if np.isfinite(udr[i]) else float("nan")
            ),
        }
        for i in range(n)
    }

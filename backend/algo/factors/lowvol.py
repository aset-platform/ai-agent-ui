"""Low-vol factors: realized_vol_60d + beta_to_nifty.

Per research §3:
    realized_vol_60d = stdev(log_returns[-60:]) * sqrt(252)
    beta_to_nifty    = cov(stock_log_ret, nifty_log_ret)
                       / var(nifty_log_ret)   over a 252d window
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

VOL_WINDOW = 60
BETA_WINDOW = 252
ANNUALISER = float(np.sqrt(252))


def _log_returns(closes: np.ndarray) -> np.ndarray:
    if closes.size < 2:
        return np.array([])
    return np.diff(np.log(closes))


def compute_lowvol(
    history: pd.DataFrame, nifty: pd.DataFrame,
) -> dict[date, dict[str, float]]:
    h = history.sort_values("bar_date").reset_index(drop=True)
    n = nifty.sort_values("bar_date").reset_index(drop=True)
    merged = pd.merge(
        h[["bar_date", "close"]],
        n[["bar_date", "close"]].rename(
            columns={"close": "nifty_close"},
        ),
        on="bar_date", how="inner",
    )
    if merged.empty:
        return {
            d: {
                "realized_vol_60d": float("nan"),
                "beta_to_nifty": float("nan"),
            }
            for d in h["bar_date"]
        }

    closes = merged["close"].astype(float).to_numpy()
    nclose = merged["nifty_close"].astype(float).to_numpy()
    dates = merged["bar_date"].tolist()
    out: dict[date, dict[str, float]] = {}
    for i in range(len(dates)):
        if i + 1 < VOL_WINDOW:
            rv = float("nan")
        else:
            window = closes[i + 1 - VOL_WINDOW: i + 1]
            r = _log_returns(window)
            rv = (
                float(np.std(r, ddof=0) * ANNUALISER)
                if r.size else float("nan")
            )
        if i + 1 < BETA_WINDOW:
            beta = float("nan")
        else:
            sw = closes[i + 1 - BETA_WINDOW: i + 1]
            nw = nclose[i + 1 - BETA_WINDOW: i + 1]
            sr = _log_returns(sw)
            nr = _log_returns(nw)
            if sr.size and nr.size and np.var(nr, ddof=0) > 1e-12:
                beta = float(
                    np.cov(sr, nr, ddof=0)[0, 1]
                    / np.var(nr, ddof=0)
                )
            else:
                beta = float("nan")
        out[dates[i]] = {
            "realized_vol_60d": rv,
            "beta_to_nifty": beta,
        }
    return out

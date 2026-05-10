"""Relative strength factors vs NIFTY + sector index.

Per research §3:
    rs_vs_nifty_3m  = (stock[t]/stock[t-63])  / (nifty[t]/nifty[t-63])
    rs_vs_nifty_6m  = (stock[t]/stock[t-126]) / (nifty[t]/nifty[t-126])
    rs_vs_sector_3m = same with sector index in place of NIFTY
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

LB_3M = 63
LB_6M = 126


def _rs(
    stock: np.ndarray, ref: np.ndarray, lookback: int, i: int,
) -> float:
    if i < lookback or i >= len(stock) or i >= len(ref):
        return float("nan")
    if (
        np.isnan(stock[i - lookback])
        or np.isnan(ref[i - lookback])
        or stock[i - lookback] == 0
        or ref[i - lookback] == 0
    ):
        return float("nan")
    s = stock[i] / stock[i - lookback]
    r = ref[i] / ref[i - lookback]
    if r == 0 or np.isnan(s) or np.isnan(r):
        return float("nan")
    return float(s / r)


def compute_relative_strength(
    history: pd.DataFrame,
    nifty: pd.DataFrame,
    *,
    sector: str | None,
    sector_indices: dict[str, pd.DataFrame],
) -> dict[date, dict[str, float]]:
    h = history.sort_values("bar_date").reset_index(drop=True)
    n = nifty.sort_values("bar_date").reset_index(drop=True)
    merged = pd.merge(
        h[["bar_date", "close"]],
        n[["bar_date", "close"]].rename(
            columns={"close": "nifty_close"},
        ),
        on="bar_date", how="left",
    )

    sec_df = sector_indices.get(sector) if sector else None
    if sec_df is not None and not sec_df.empty:
        merged = pd.merge(
            merged,
            sec_df.sort_values("bar_date")[["bar_date", "close"]]
            .rename(columns={"close": "sector_close"}),
            on="bar_date", how="left",
        )
    else:
        merged["sector_close"] = float("nan")

    closes = merged["close"].astype(float).to_numpy()
    nclose = merged["nifty_close"].astype(float).to_numpy()
    sclose = merged["sector_close"].astype(float).to_numpy()
    dates = merged["bar_date"].tolist()
    return {
        dates[i]: {
            "rs_vs_nifty_3m": _rs(closes, nclose, LB_3M, i),
            "rs_vs_nifty_6m": _rs(closes, nclose, LB_6M, i),
            "rs_vs_sector_3m": _rs(closes, sclose, LB_3M, i),
        }
        for i in range(len(dates))
    }

"""Vol-normalized 3-class label function for the bake-off.

Pure. No I/O. Spec §4.3.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LABEL_SHORT = 0
LABEL_FLAT = 1
LABEL_LONG = 2


def label_bars(
    bars: pd.DataFrame,
    *,
    threshold: float = 0.5,
) -> pd.DataFrame:
    """Attach an integer ``label`` column to *bars* under the spec rule.

    Per-row label::

      r_fwd  = (close[t+4] - open[t+1]) / open[t+1]
      r_norm = r_fwd / (atr_14[t] / close[t])
      label  = LONG  if r_norm >= +threshold
               SHORT if r_norm <= -threshold
               else FLAT

    Bars where the label window crosses a ``bar_date`` boundary,
    or where ``atr_14[t]`` is NaN / zero, are dropped — they
    cannot be labelled without forward-looking information.

    Args:
        bars: Must contain ``ticker``, ``bar_open_ts_ns``, ``bar_date``,
            ``open``, ``close``, ``atr_14``. Sorted ascending per ticker.
        threshold: σ-multiple threshold for LONG / SHORT cut-off.

    Returns:
        Frame with the same columns as *bars* plus ``label``,
        ``entry_px``, ``exit_px``, ``r_norm``. Unlabellable rows
        are dropped.
    """
    required = {"ticker", "bar_open_ts_ns", "bar_date",
                "open", "close", "atr_14"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    if (bars["close"] <= 0).any() or (bars["open"] <= 0).any():
        raise ValueError("non-positive price encountered")

    out_frames: list[pd.DataFrame] = []
    for ticker, grp in bars.groupby("ticker", sort=False):
        grp = grp.sort_values("bar_open_ts_ns").reset_index(drop=True)
        n = len(grp)
        if n < 5:
            continue

        entry_px = grp["open"].shift(-1)
        exit_px = grp["close"].shift(-4)
        same_day = grp["bar_date"].shift(-4) == grp["bar_date"]
        r_fwd = (exit_px - entry_px) / entry_px
        atr_ret = grp["atr_14"] / grp["close"]
        r_norm = r_fwd / atr_ret

        keep = (
            (grp.index < n - 4)
            & same_day
            & atr_ret.notna()
            & (atr_ret > 0)
            & r_fwd.notna()
        )

        sub = grp[keep].copy()
        sub["entry_px"] = entry_px[keep].values
        sub["exit_px"] = exit_px[keep].values
        sub["r_norm"] = r_norm[keep].values
        sub["label"] = np.where(
            sub["r_norm"] >= threshold,
            LABEL_LONG,
            np.where(sub["r_norm"] <= -threshold, LABEL_SHORT, LABEL_FLAT),
        )
        out_frames.append(sub)

    if not out_frames:
        return bars.iloc[0:0].assign(
            entry_px=pd.Series(dtype=float),
            exit_px=pd.Series(dtype=float),
            r_norm=pd.Series(dtype=float),
            label=pd.Series(dtype=int),
        )
    return pd.concat(out_frames, ignore_index=True)

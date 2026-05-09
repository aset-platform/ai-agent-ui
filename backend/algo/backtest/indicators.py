"""On-the-fly technical-indicator feature computation.

Used by the backtest + paper runtimes to populate the
``EvalContext.features`` map per (ticker, bar) with values
referenced by the strategy AST (``sma_50``, ``sma_200``,
``rsi_14``, ``golden_cross_days_ago``, ...).

Source of truth = ``stocks.ohlcv`` (loaded with warmup history).
We do NOT depend on a separate ``stocks.technical_indicators``
table — that table doesn't exist in the schema, and
recomputing here is fast (O(N) rolling sums).

All features Decimal for consistency with the rest of the
pipeline. ``golden_cross_days_ago`` for bars before the first
SMA50/SMA200 crossover is a sentinel ``999`` so a strategy
condition like ``<= 10`` always fails until the cross fires.
"""
from __future__ import annotations

import logging
from collections import deque
from decimal import Decimal
from typing import Iterable

from backend.algo.backtest.types import BarData

_logger = logging.getLogger(__name__)

# Default warmup so SMA200 is well-formed at period_start.
# 200 trading days ≈ 280 calendar days (5/7 trading-day ratio
# minus IST holidays). 400 calendar days gives a comfortable
# buffer so SMA200 + golden_cross_days_ago are settled by the
# user-requested period_start.
DEFAULT_WARMUP_BARS = 400

# Sentinel for "no crossover seen yet" — large enough that any
# `<= N` comparison in a strategy condition fails.
NO_CROSS_SENTINEL = Decimal("999")


def _rolling_sma(
    closes: list[Decimal], window: int,
) -> list[Decimal | None]:
    """Right-aligned simple moving average. Output[i] = mean of
    closes[i-window+1 : i+1] when ≥ window points exist, else
    None. O(N) via a sliding sum.
    """
    out: list[Decimal | None] = [None] * len(closes)
    if not closes or window <= 0:
        return out
    running = Decimal("0")
    for i, c in enumerate(closes):
        running += c
        if i >= window:
            running -= closes[i - window]
        if i >= window - 1:
            out[i] = running / Decimal(window)
    return out


def compute_indicators(
    bars: list[BarData],
    *,
    sma_windows: Iterable[int] = (20, 50, 200),
) -> dict[object, dict[str, Decimal]]:
    """Per-(date) feature map for a single ticker's bar series.

    Bars MUST be ascending by date and from a single ticker.
    Returns a dict keyed by ``date`` (the bar's ``BarData.date``)
    so callers can look up features per (ticker, bar_date) at
    runtime.
    """
    if not bars:
        return {}

    closes = [b.close for b in bars]
    sma_series: dict[int, list[Decimal | None]] = {
        w: _rolling_sma(closes, w) for w in sma_windows
    }

    # golden_cross_days_ago tracking — distance since the most
    # recent SMA50 crossing ABOVE SMA200. Reset on cross-down
    # (SMA50 dipping back below SMA200) so the strategy doesn't
    # mistake a stale cross for a fresh signal.
    s50 = sma_series.get(50)
    s200 = sma_series.get(200)
    last_cross_up_idx: int | None = None

    out: dict[object, dict[str, Decimal]] = {}
    for i, bar in enumerate(bars):
        feats: dict[str, Decimal] = {
            "today_ltp": bar.close,
            "today_vol": Decimal(bar.volume),
        }
        for w in sma_windows:
            v = sma_series[w][i]
            if v is not None:
                feats[f"sma_{w}"] = v

        if s50 is not None and s200 is not None and i > 0:
            cur_50 = s50[i]
            cur_200 = s200[i]
            prev_50 = s50[i - 1]
            prev_200 = s200[i - 1]
            if (
                cur_50 is not None and cur_200 is not None
                and prev_50 is not None and prev_200 is not None
            ):
                # Crossing UP: prev had 50 ≤ 200, now 50 > 200.
                if prev_50 <= prev_200 and cur_50 > cur_200:
                    last_cross_up_idx = i
                # Crossing DOWN: invalidate the prior up-cross.
                if prev_50 >= prev_200 and cur_50 < cur_200:
                    last_cross_up_idx = None

        if last_cross_up_idx is not None:
            feats["golden_cross_days_ago"] = Decimal(
                i - last_cross_up_idx,
            )
        else:
            feats["golden_cross_days_ago"] = NO_CROSS_SENTINEL

        out[bar.date] = feats
    return out


def compute_indicators_for_universe(
    bars_by_ticker: dict[str, list[BarData]],
    *,
    sma_windows: Iterable[int] = (20, 50, 200),
) -> dict[str, dict[object, dict[str, Decimal]]]:
    """Apply ``compute_indicators`` per-ticker. Output:
    ``{ticker: {bar_date: {feature: Decimal}}}``.
    """
    out: dict[str, dict[object, dict[str, Decimal]]] = {}
    for ticker, blist in bars_by_ticker.items():
        if not blist:
            continue
        # Defensive sort — caller is expected to already deliver
        # ascending bars but a misordered list would silently
        # corrupt every SMA.
        sorted_bars = sorted(blist, key=lambda b: b.date)
        out[ticker] = compute_indicators(
            sorted_bars, sma_windows=sma_windows,
        )
    return out

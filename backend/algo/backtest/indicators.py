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


def _vwap_intraday(
    bars: list[BarData],
) -> list[Decimal | None]:
    """Intraday Volume-Weighted Average Price.

    VWAP[i] = Σ(typical_price × volume) / Σ(volume), accumulated
    from the first bar of the calendar day up to and including
    bars[i]. Resets at each calendar-date boundary so the value
    matches the standard NSE intraday session definition (the
    cumulative reset happens implicitly when bars[i].date changes).

    typical_price = (high + low + close) / 3 — the standard
    Bollinger / VWAP inputs definition. Falls back to None when
    cumulative volume is zero (a fresh-day bar with vol=0, e.g.
    pre-market or a halted ticker).

    Notes per runtime:
    - Live + paper (minute bars): proper intraday running mean.
      Useful as a `today_ltp < vwap` mean-reversion check.
    - Backtest (daily bars): degenerates to typical_price
      (single observation per date). Strategies that gate on
      VWAP behave differently in backtest vs live; document this
      when surfacing the feature in the catalog.
    """
    out: list[Decimal | None] = [None] * len(bars)
    if not bars:
        return out
    cum_pv = Decimal("0")
    cum_v = Decimal("0")
    last_date = None
    three = Decimal("3")
    for i, bar in enumerate(bars):
        if bar.date != last_date:
            cum_pv = Decimal("0")
            cum_v = Decimal("0")
            last_date = bar.date
        typical = (bar.high + bar.low + bar.close) / three
        vol = Decimal(bar.volume)
        cum_pv += typical * vol
        cum_v += vol
        if cum_v > 0:
            out[i] = cum_pv / cum_v
    return out


def _wilder_rsi(
    closes: list[Decimal], window: int = 14,
) -> list[Decimal | None]:
    """Wilder's RSI. Output[i] is None for the first ``window``
    bars; subsequent bars use the smoothed average gain/loss with
    Wilder's exponential smoothing (alpha = 1/window).
    """
    n = len(closes)
    out: list[Decimal | None] = [None] * n
    if n <= window:
        return out
    gains = Decimal("0")
    losses = Decimal("0")
    for i in range(1, window + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains / Decimal(window)
    avg_loss = losses / Decimal(window)
    if avg_loss == 0:
        out[window] = Decimal("100")
    else:
        rs = avg_gain / avg_loss
        out[window] = Decimal("100") - Decimal("100") / (
            Decimal("1") + rs
        )
    w = Decimal(window)
    for i in range(window + 1, n):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else Decimal("0")
        loss = -diff if diff < 0 else Decimal("0")
        avg_gain = (avg_gain * (w - 1) + gain) / w
        avg_loss = (avg_loss * (w - 1) + loss) / w
        if avg_loss == 0:
            out[i] = Decimal("100")
        else:
            rs = avg_gain / avg_loss
            out[i] = Decimal("100") - Decimal("100") / (
                Decimal("1") + rs
            )
    return out


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
    rsi_series = _wilder_rsi(closes, 14)
    vwap_series = _vwap_intraday(bars)

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
        rsi_v = rsi_series[i]
        if rsi_v is not None:
            feats["rsi"] = rsi_v
            feats["rsi_14"] = rsi_v
        vwap_v = vwap_series[i]
        if vwap_v is not None:
            feats["vwap"] = vwap_v

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


# Per-bar percentage return of regime ticker over prior N bars.
# Strategies reference ``{"feature": "nifty_30d_return_pct"}`` to
# add trend-strength gating on top of the binary SMA200 regime
# (e.g. ``> 0`` for "rising last 30d"; ``> 2`` to exclude chop).
def compute_market_trend_strength(
    period_start: object,
    period_end: object,
    regime_ticker: str = "^NSEI",
    lookback_bars: int = 30,
    warmup_days: int = DEFAULT_WARMUP_BARS,
) -> dict[object, Decimal]:
    """Returns ``{bar_date: Decimal(pct_return_over_N_bars)}``.

    Empty for bars without sufficient lookback history. Callers
    fall back to ``Decimal(0)`` for missing dates so a strategy
    gated on ``> 0`` correctly treats unknown-trend bars as not
    qualifying.
    """
    from backend.algo.backtest.data_source import load_ohlcv_window

    bars_by_ticker = load_ohlcv_window(
        tickers=[regime_ticker],
        period_start=period_start,
        period_end=period_end,
        warmup_days=warmup_days,
    )
    blist = bars_by_ticker.get(regime_ticker) or []
    if not blist:
        return {}
    sorted_bars = sorted(blist, key=lambda b: b.date)
    closes = [b.close for b in sorted_bars]
    out: dict[object, Decimal] = {}
    for i, bar in enumerate(sorted_bars):
        if i < lookback_bars:
            continue
        prior = closes[i - lookback_bars]
        if prior == 0:
            continue
        out[bar.date] = (
            (bar.close - prior) / prior * Decimal("100")
        )
    return out


# Per-bar boolean (Decimal 1.0/0.0): regime ticker close > SMA(N).
# Strategies reference ``{"feature": "nifty_above_sma200"}`` to
# gate entries to bull-regime days only. Single index, one
# computation reused across every (ticker, bar) in the universe.
def compute_market_regime(
    period_start: object,
    period_end: object,
    regime_ticker: str = "^NSEI",
    sma_window: int = 200,
    warmup_days: int = DEFAULT_WARMUP_BARS,
) -> dict[object, Decimal]:
    """Returns ``{bar_date: Decimal(1) if close > SMA else Decimal(0)}``.

    Empty dict if the regime ticker has no OHLCV in the window —
    callers MUST treat missing dates as ``Decimal(0)`` so a
    strategy gated on ``nifty_above_sma200 > 0`` falls through
    to ``hold`` rather than firing without regime data.
    """
    # Local import avoids a circular dependency at module load
    # time — data_source pulls from this module's bar shape.
    from backend.algo.backtest.data_source import load_ohlcv_window

    bars_by_ticker = load_ohlcv_window(
        tickers=[regime_ticker],
        period_start=period_start,
        period_end=period_end,
        warmup_days=warmup_days,
    )
    blist = bars_by_ticker.get(regime_ticker) or []
    if not blist:
        return {}
    sorted_bars = sorted(blist, key=lambda b: b.date)
    closes = [b.close for b in sorted_bars]
    sma = _rolling_sma(closes, sma_window)
    out: dict[object, Decimal] = {}
    for i, bar in enumerate(sorted_bars):
        s = sma[i]
        if s is None:
            continue
        out[bar.date] = (
            Decimal("1") if bar.close > s else Decimal("0")
        )
    return out

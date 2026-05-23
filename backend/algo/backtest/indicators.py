"""On-the-fly DAILY technical-indicator feature computation.

Used by the backtest + paper + live runtimes to populate the
``EvalContext.features`` map per (ticker, bar) with values
referenced by the strategy AST (``sma_50``, ``sma_200``,
``rsi_14``, ``golden_cross_days_ago``, ...) on the DAILY
cadence.

Intraday features (15m / 5m / 1m) used to live here too as
slice-4b's ``compute_indicators_intraday`` /
``compute_indicators_for_universe_intraday`` — they were
deleted in ASETPLTFRM-402 / FE-4 when the centralized feature
engine + ``stocks.intraday_features`` table became the sole
source of intraday features. The shared primitives
(``vwap_intraday``, ``wilder_rsi``, ``rolling_sma``,
``DEFAULT_INTRADAY_SMA_WINDOWS``, ``DEFAULT_INTRADAY_WARMUP_DAYS``,
``NO_CROSS_SENTINEL``) now live under ``backend.algo.features``.

Source of truth for the daily path = ``stocks.ohlcv`` (loaded
with warmup history). We do NOT depend on a separate
``stocks.technical_indicators`` table — that table doesn't
exist in the schema, and recomputing here is fast (O(N)
rolling sums).

All features Decimal for consistency with the rest of the
pipeline. ``golden_cross_days_ago`` for bars before the first
SMA50/SMA200 crossover is a sentinel ``999`` so a strategy
condition like ``<= 10`` always fails until the cross fires.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Iterable

from backend.algo.backtest.types import BarData
from backend.algo.features.primitives import rolling_sma as _rolling_sma
from backend.algo.features.primitives import vwap_intraday as _vwap_intraday
from backend.algo.features.primitives import wilder_rsi as _wilder_rsi
from backend.algo.features.version import NO_CROSS_SENTINEL

_logger = logging.getLogger(__name__)

# Default warmup so SMA200 is well-formed at period_start.
# 200 trading days ≈ 280 calendar days (5/7 trading-day ratio
# minus IST holidays). 400 calendar days gives a comfortable
# buffer so SMA200 + golden_cross_days_ago are settled by the
# user-requested period_start.
DEFAULT_WARMUP_BARS = 400


def compute_indicators(
    bars: list[BarData],
    *,
    sma_windows: Iterable[int] = (5, 20, 50, 200),
) -> dict[object, dict[str, Decimal]]:
    """Per-(date) feature map for a single ticker's bar series.

    Bars MUST be ascending by date and from a single ticker.
    Returns a dict keyed by ``date`` (the bar's ``BarData.date``)
    so callers can look up features per (ticker, bar_date) at
    runtime.

    Features emitted (beyond sma_N and golden_cross_days_ago):
        - ``rsi`` / ``rsi_14``  — Wilder RSI(14) (original)
        - ``rsi_5``             — Wilder RSI(5)
        - ``rsi_2``             — Wilder RSI(2); Connors mean-rev
        - ``distance_from_sma5``— (close - sma_5) / sma_5; exit
          signal for Connors strategy
    """
    if not bars:
        return {}

    closes = [b.close for b in bars]
    # Ensure sma_5 is always computed even if caller overrides
    # sma_windows (distance_from_sma5 needs it).
    sma_windows_t = tuple(sma_windows)
    if 5 not in sma_windows_t:
        sma_windows_t = (5,) + sma_windows_t
    sma_series: dict[int, list[Decimal | None]] = {
        w: _rolling_sma(closes, w) for w in sma_windows_t
    }
    rsi_series = _wilder_rsi(closes, 14)
    rsi_5_series = _wilder_rsi(closes, 5)
    rsi_2_series = _wilder_rsi(closes, 2)
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
        for w in sma_windows_t:
            v = sma_series[w][i]
            if v is not None:
                feats[f"sma_{w}"] = v
        rsi_v = rsi_series[i]
        if rsi_v is not None:
            feats["rsi"] = rsi_v
            feats["rsi_14"] = rsi_v
        rsi5_v = rsi_5_series[i]
        if rsi5_v is not None:
            feats["rsi_5"] = rsi5_v
        rsi2_v = rsi_2_series[i]
        if rsi2_v is not None:
            feats["rsi_2"] = rsi2_v
        # distance_from_sma5 = (close - sma_5) / sma_5.
        # Connors exit condition: price crossed back above SMA(5).
        sma5_v = sma_series[5][i]
        if sma5_v is not None and sma5_v != 0:
            feats["distance_from_sma5"] = (
                Decimal(str(bar.close)) - sma5_v
            ) / sma5_v
        vwap_v = vwap_series[i]
        if vwap_v is not None:
            feats["vwap"] = vwap_v

        if s50 is not None and s200 is not None and i > 0:
            cur_50 = s50[i]
            cur_200 = s200[i]
            prev_50 = s50[i - 1]
            prev_200 = s200[i - 1]
            if (
                cur_50 is not None
                and cur_200 is not None
                and prev_50 is not None
                and prev_200 is not None
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
            sorted_bars,
            sma_windows=sma_windows,
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
        out[bar.date] = (bar.close - prior) / prior * Decimal("100")
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
        out[bar.date] = Decimal("1") if bar.close > s else Decimal("0")
    return out

"""Centralized pure-function feature engine — entrypoint.

``compute_intraday_features(bars)`` is the canonical Phase-1
producer. Same shape contract as slice-4b
``compute_indicators_intraday(bars)`` so FE-4 can swap it in
mechanically. Bit-for-bit parity is enforced on the overlap
set (vwap, rsi, rsi_14, sma_20/50/100/200, today_ltp,
today_vol, golden_cross_bars_ago) by ``test_slice4b_parity.py``.

Phase-1 features emitted (26 — the two RS-vs-* features defer
to FE-8 because they need ``stocks.index_intraday_bars`` and
``stocks.sector_intraday_bars`` which only exist after
FE-6 / FE-7):

  trend       vwap, dist_from_vwap_pct,
              sma_20, sma_50, sma_100, sma_200,
              ema_20, ema_50, ema_20_slope_5bar,
              golden_cross_bars_ago
  momentum    rsi (alias rsi_14), rsi_5, roc_5
  volatility  atr_14, range_expansion, bb_width
  volume      relative_volume, volume_spike
  structure   gap_pct, orb_high_15min, orb_low_15min,
              dist_from_prev_day_high_pct,
              dist_from_prev_day_low_pct
  time        minutes_since_open, time_of_day_bucket
  trivial     today_ltp, today_vol

Skip-emission contract per CLAUDE.md feature-key-error: keys
absent (never None / NaN) when a feature isn't computable for
that bar (warmup, missing prior day, etc.). The runner's
``KeyError`` counter is the authoritative "feature wasn't
ready" signal.

NO I/O. Inputs in, outputs out. The Iceberg / Redis loader
layers land in FE-3 / FE-5.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from backend.algo.backtest.types import BarData
from backend.algo.features import primitives as p
from backend.algo.features.types import (
    TickerFeaturePanel,
    UniverseFeaturePanel,
)
from backend.algo.features.version import (
    DEFAULT_INTRADAY_SMA_WINDOWS,
    FEATURE_SET_VERSION,
    NO_CROSS_SENTINEL,
)


def compute_intraday_features(
    bars: list[BarData],
    *,
    sma_windows: Iterable[int] = DEFAULT_INTRADAY_SMA_WINDOWS,
    feature_set_version: str = FEATURE_SET_VERSION,
) -> TickerFeaturePanel:
    """Per-bar feature dict for a single ticker's intraday series.

    Args:
        bars: Ascending-by-``bar_open_ts_ns`` list of bars from
            a single ticker. Bars without ``bar_open_ts_ns``
            (i.e. daily-shaped input) are skipped defensively.
        sma_windows: SMA windows to emit. Defaults to
            ``DEFAULT_INTRADAY_SMA_WINDOWS = (20, 50, 100, 200)``.
        feature_set_version: Reserved for FE-3's persisted-row
            stamping. Not embedded in the returned dict — the
            persister adds the column on write.

    Returns:
        ``{bar_open_ts_ns: {feature_name: Decimal | str}}``.
        ``time_of_day_bucket`` is ``str``; every other feature
        is ``Decimal``. Missing-feature keys are simply absent.
    """
    del feature_set_version  # accepted for forward-compat; reserved.
    if not bars:
        return {}
    series = [b for b in bars if b.bar_open_ts_ns is not None]
    if not series:
        return {}

    closes = [b.close for b in series]
    sma_windows_t = tuple(sma_windows)
    sma_by_w: dict[int, list[Decimal | None]] = {
        w: p.rolling_sma(closes, w) for w in sma_windows_t
    }
    rsi_14 = p.wilder_rsi(closes, 14)
    rsi_5 = p.wilder_rsi(closes, 5)
    vwap = p.vwap_intraday(series)
    ema_20 = p.ema(closes, 20)
    ema_50 = p.ema(closes, 50)
    ema_20_slope = p.series_slope_n_bar(ema_20, 5)
    roc_5 = p.roc_n_bar(closes, 5)
    atr_14 = p.wilder_atr(series, 14)
    bb_w = p.bollinger_band_width(closes, 20)
    rel_vol = p.relative_volume_by_time_of_day(
        series,
        lookback_days=20,
    )
    vol_spike = p.volume_spike_flag(series, window=20)
    prev_day = p.prev_day_close_high_low(series)
    today_open = p.today_open_per_bar(series)
    orb = p.orb_per_bar(series)
    mins_since_open = p.minutes_since_open(series)
    tod_bucket = p.time_of_day_bucket(series)

    s50 = sma_by_w.get(50)
    s200 = sma_by_w.get(200)
    last_cross_up_idx: int | None = None

    out: TickerFeaturePanel = {}
    for i, bar in enumerate(series):
        feats: dict[str, Decimal | str] = {
            "today_ltp": bar.close,
            "today_vol": Decimal(bar.volume),
        }

        # SMA family.
        for w in sma_windows_t:
            v = sma_by_w[w][i]
            if v is not None:
                feats[f"sma_{w}"] = v

        # RSI family.
        rsi_v = rsi_14[i]
        if rsi_v is not None:
            feats["rsi"] = rsi_v
            feats["rsi_14"] = rsi_v
        rsi5_v = rsi_5[i]
        if rsi5_v is not None:
            feats["rsi_5"] = rsi5_v

        # VWAP + distance.
        vwap_v = vwap[i]
        if vwap_v is not None:
            feats["vwap"] = vwap_v
            if vwap_v != 0:
                feats["dist_from_vwap_pct"] = (
                    (bar.close - vwap_v) / vwap_v * Decimal("100")
                )

        # EMA family + slope.
        ema20_v = ema_20[i]
        if ema20_v is not None:
            feats["ema_20"] = ema20_v
        ema50_v = ema_50[i]
        if ema50_v is not None:
            feats["ema_50"] = ema50_v
        ema20_slope_v = ema_20_slope[i]
        if ema20_slope_v is not None:
            feats["ema_20_slope_5bar"] = ema20_slope_v

        # ROC.
        roc_v = roc_5[i]
        if roc_v is not None:
            feats["roc_5"] = roc_v

        # ATR + range expansion.
        atr_v = atr_14[i]
        if atr_v is not None:
            feats["atr_14"] = atr_v
            if atr_v != 0:
                feats["range_expansion"] = (bar.high - bar.low) / atr_v

        # Bollinger band width.
        bb_v = bb_w[i]
        if bb_v is not None:
            feats["bb_width"] = bb_v

        # Volume features.
        rel_v = rel_vol[i]
        if rel_v is not None:
            feats["relative_volume"] = rel_v
        spike_v = vol_spike[i]
        if spike_v is not None:
            feats["volume_spike"] = spike_v

        # Structure: gap + prev-day distances.
        prev = prev_day[i]
        open_today = today_open[i]
        if prev is not None and open_today is not None:
            prev_close, prev_high, prev_low = prev
            if prev_close != 0:
                feats["gap_pct"] = (
                    (open_today - prev_close) / prev_close * Decimal("100")
                )
            if prev_high != 0:
                feats["dist_from_prev_day_high_pct"] = (
                    (bar.close - prev_high) / prev_high * Decimal("100")
                )
            if prev_low != 0:
                feats["dist_from_prev_day_low_pct"] = (
                    (bar.close - prev_low) / prev_low * Decimal("100")
                )

        # Structure: ORB.
        orb_v = orb[i]
        if orb_v is not None:
            feats["orb_high_15min"] = orb_v[0]
            feats["orb_low_15min"] = orb_v[1]

        # Time features.
        mins_v = mins_since_open[i]
        if mins_v is not None:
            feats["minutes_since_open"] = Decimal(mins_v)
        tod_v = tod_bucket[i]
        if tod_v is not None:
            feats["time_of_day_bucket"] = tod_v

        # Golden-cross bars-ago (bit-for-bit parity with slice-4b).
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
                if prev_50 <= prev_200 and cur_50 > cur_200:
                    last_cross_up_idx = i
                if prev_50 >= prev_200 and cur_50 < cur_200:
                    last_cross_up_idx = None
        if last_cross_up_idx is not None:
            feats["golden_cross_bars_ago"] = Decimal(
                i - last_cross_up_idx,
            )
        else:
            feats["golden_cross_bars_ago"] = NO_CROSS_SENTINEL

        out[bar.bar_open_ts_ns] = feats  # type: ignore[index]
    return out


def compute_intraday_features_for_universe(
    bars_by_ticker: dict[str, list[BarData]],
    *,
    sma_windows: Iterable[int] = DEFAULT_INTRADAY_SMA_WINDOWS,
    feature_set_version: str = FEATURE_SET_VERSION,
) -> UniverseFeaturePanel:
    """Apply :func:`compute_intraday_features` per-ticker. Output
    shape matches
    ``compute_indicators_for_universe_intraday`` so FE-4's swap
    is mechanical.
    """
    out: UniverseFeaturePanel = {}
    for ticker, blist in bars_by_ticker.items():
        if not blist:
            continue
        sorted_bars = sorted(
            blist,
            key=lambda b: (b.bar_open_ts_ns or 0),
        )
        out[ticker] = compute_intraday_features(
            sorted_bars,
            sma_windows=sma_windows,
            feature_set_version=feature_set_version,
        )
    return out

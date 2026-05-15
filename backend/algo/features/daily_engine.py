"""Daily-cadence (interval_sec=86400) feature engine — FE-15a.

Computes the 18 daily features defined in the FE-15 spec
(:doc:`docs/superpowers/specs/2026-05-15-fe15-daily-feature-parity.md`
§3) from a per-ticker series of daily OHLCV bars.

This is a deliberate sibling of
:func:`backend.algo.features.engine.compute_intraday_features`,
**not** a wrapper. The intraday engine emits a few features that
are intrinsically intraday (vwap, ORB, time-of-day,
relative-volume) which would either be meaningless or
arithmetically degenerate on daily bars. The daily engine
emits exactly the spec's 18 features and nothing else.

All primitives are reused verbatim from
:mod:`backend.algo.features.primitives` — only the orchestration
differs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from backend.algo.backtest.types import BarData
from backend.algo.features import primitives as p
from backend.algo.features.types import TickerFeaturePanel
from backend.algo.features.version import FEATURE_SET_VERSION

DEFAULT_DAILY_SMA_WINDOWS: tuple[int, ...] = (20, 50, 100, 200)


def compute_daily_features(
    bars: list[BarData],
    *,
    sma_windows: Iterable[int] = DEFAULT_DAILY_SMA_WINDOWS,
    feature_set_version: str = FEATURE_SET_VERSION,
) -> TickerFeaturePanel:
    """Per-bar feature dict for a single ticker's daily series.

    Args:
        bars: Ascending-by-``bar_open_ts_ns`` list of daily bars
            for a single ticker. Bars without ``bar_open_ts_ns``
            are skipped defensively (the caller is responsible
            for synthesising UTC-midnight ns from ``bar.date``).
        sma_windows: SMA windows to emit. Defaults to
            ``(20, 50, 100, 200)``.
        feature_set_version: Accepted for API parity with
            :func:`compute_intraday_features`; not embedded in
            the returned dict — the persister adds it on write.

    Returns:
        ``{bar_open_ts_ns: {feature_name: Decimal | str}}``.
        Every feature value is ``Decimal`` (or ``int`` for the
        binary ``volume_spike``). Missing-feature keys are
        simply absent.

    Emitted features (per FE-15 spec §3):
        - Trend (EMA): ``ema_20``, ``ema_50``, ``ema_20_slope_5bar``
        - Trend (SMA): ``sma_20``, ``sma_50``, ``sma_100``, ``sma_200``
        - Trend (cross): ``golden_cross_bars_ago``
        - Momentum: ``rsi_5``, ``rsi_14``, ``roc_5``
        - Volatility: ``atr_14``, ``range_expansion``, ``bb_width``
        - Price-action: ``gap_pct``,
          ``dist_from_prev_day_high_pct``,
          ``dist_from_prev_day_low_pct``
        - Volume: ``volume_spike`` (binary 0/1)
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
    ema_20 = p.ema(closes, 20)
    ema_50 = p.ema(closes, 50)
    ema_20_slope = p.series_slope_n_bar(ema_20, 5)
    roc_5 = p.roc_n_bar(closes, 5)
    atr_14 = p.wilder_atr(series, 14)
    bb_w = p.bollinger_band_width(closes, 20)
    vol_spike = p.volume_spike_flag(series, window=20)
    prev_day = p.prev_day_close_high_low(series)
    today_open = p.today_open_per_bar(series)

    s50 = sma_by_w.get(50)
    s200 = sma_by_w.get(200)
    last_cross_up_idx: int | None = None

    out: TickerFeaturePanel = {}
    for i, bar in enumerate(series):
        feats: dict[str, Decimal | str] = {}

        # SMA family.
        for w in sma_windows_t:
            v = sma_by_w[w][i]
            if v is not None:
                feats[f"sma_{w}"] = v

        # RSI family.
        rsi_v = rsi_14[i]
        if rsi_v is not None:
            feats["rsi_14"] = rsi_v
        rsi5_v = rsi_5[i]
        if rsi5_v is not None:
            feats["rsi_5"] = rsi5_v

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

        # Volume spike (binary).
        spike_v = vol_spike[i]
        if spike_v is not None:
            feats["volume_spike"] = spike_v

        # Price-action: gap_pct + dist-from-prev-day H/L.
        # On daily bars, prev_day primitives bucket by IST date —
        # each bar is its own bucket, so prev[i] == day (i-1)'s
        # close/high/low. Exactly what the spec asks for.
        prev = prev_day[i]
        open_today = today_open[i]
        if prev is not None and open_today is not None:
            prev_close, prev_high, prev_low = prev
            if prev_close != 0:
                feats["gap_pct"] = (
                    (open_today - prev_close)
                    / prev_close
                    * Decimal("100")
                )
            if prev_high != 0:
                feats["dist_from_prev_day_high_pct"] = (
                    (bar.close - prev_high) / prev_high * Decimal("100")
                )
            if prev_low != 0:
                feats["dist_from_prev_day_low_pct"] = (
                    (bar.close - prev_low) / prev_low * Decimal("100")
                )

        # Golden-cross bars-ago (SMA50 crossing above SMA200).
        # Bit-for-bit parity with the intraday engine's
        # implementation in engine.py:220-239 — same semantics,
        # just over daily bars instead of 15m.
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

        out[bar.bar_open_ts_ns] = feats
    return out


def compute_daily_features_for_universe(
    bars_by_ticker: dict[str, list[BarData]],
    *,
    sma_windows: Iterable[int] = DEFAULT_DAILY_SMA_WINDOWS,
    feature_set_version: str = FEATURE_SET_VERSION,
) -> dict[str, TickerFeaturePanel]:
    """Apply :func:`compute_daily_features` per ticker.

    Mirrors :func:`compute_intraday_features_for_universe` for
    API parity. No cross-sectional pass — daily RS-vs-Nifty
    features already live in ``stocks.daily_factors``.
    """
    return {
        ticker: compute_daily_features(
            bars,
            sma_windows=sma_windows,
            feature_set_version=feature_set_version,
        )
        for ticker, bars in bars_by_ticker.items()
    }

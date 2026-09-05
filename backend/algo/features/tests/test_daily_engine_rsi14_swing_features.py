"""Tests for the two daily-engine features the
rsi14_trend_pullback_swing_v1 template references:
rsi_14_delta_1bar and bars_below_sma50.

See docs/superpowers/specs/2026-09-05-rsi14-trend-pullback-swing-design.md.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.algo.backtest.types import BarData
from backend.algo.features.daily_engine import compute_daily_features


def _bars(
    closes: list[float],
    start: date = date(2024, 1, 1),
) -> list[BarData]:
    """Bars built from an explicit close-price list. No weekend
    skipping — daily_engine treats every bar as its own trading
    day regardless of calendar gaps (mirrors the existing
    test_daily_engine_v3_features.py::_bars helper)."""
    bars = []
    for i, price in enumerate(closes):
        c = Decimal(str(price))
        bars.append(BarData(
            ticker="TEST.NS",
            date=start + timedelta(days=i),
            open=c,
            high=c + Decimal("0.5"),
            low=c - Decimal("0.5"),
            close=c,
            volume=10000,
            bar_open_ts_ns=i * 86400 * 10**9,
        ))
    return bars


def _zigzag_closes(n: int, base: float = 100.0) -> list[float]:
    """Alternating +1.5/-1.0 moves — keeps RSI(14) away from the
    0/100 saturation extremes so both rising and falling deltas
    appear across the series."""
    closes = [base]
    for i in range(1, n):
        closes.append(closes[-1] + (1.5 if i % 2 else -1.0))
    return closes


def test_rsi_14_delta_1bar_matches_consecutive_rsi_difference():
    closes = _zigzag_closes(40)
    panel = compute_daily_features(_bars(closes))
    ts = sorted(panel.keys())
    # rsi_14 first appears at bar index 14 (0-indexed; wilder_rsi
    # is None for the first `window` bars). The delta needs one
    # more prior bar, so it first appears at index 15.
    for i in range(15, len(ts)):
        feats = panel[ts[i]]
        prev_feats = panel[ts[i - 1]]
        assert "rsi_14_delta_1bar" in feats
        expected = feats["rsi_14"] - prev_feats["rsi_14"]
        assert feats["rsi_14_delta_1bar"] == expected


def test_rsi_14_delta_1bar_absent_before_warmup():
    closes = _zigzag_closes(10)  # fewer than 15 bars, rsi_14 never warm
    panel = compute_daily_features(_bars(closes))
    for feats in panel.values():
        assert "rsi_14_delta_1bar" not in feats


def _bars_flat_then_dip(
    n_flat: int = 60,
    dip_days: int = 2,
    tail: int = 5,
    flat_price: float = 100.0,
    dip_price: float = 90.0,
) -> list[BarData]:
    """n_flat bars at flat_price, then dip_days bars at dip_price,
    then tail bars back at flat_price. With n_flat=60 the SMA50
    warms up well before the dip (first non-None sma_50 is at
    0-indexed bar 49, all still flat_price)."""
    closes = (
        [flat_price] * n_flat
        + [dip_price] * dip_days
        + [flat_price] * tail
    )
    return _bars(closes)


def test_bars_below_sma50_zero_while_close_at_or_above_sma():
    panel = compute_daily_features(_bars_flat_then_dip())
    ts = sorted(panel.keys())
    # Bars 49..59 (0-indexed): sma_50 just warmed, close == 100 ==
    # sma_50 (flat series so far) — not below, streak stays 0.
    for i in range(49, 60):
        assert panel[ts[i]]["bars_below_sma50"] == 0


def test_bars_below_sma50_increments_across_the_dip():
    panel = compute_daily_features(_bars_flat_then_dip())
    ts = sorted(panel.keys())
    # Bar 60: close=90, sma_50=(49*100+90)/50=99.8 -> below, streak=1.
    assert panel[ts[60]]["bars_below_sma50"] == 1
    # Bar 61: close=90, sma_50=(48*100+2*90)/50=99.6 -> below, streak=2.
    assert panel[ts[61]]["bars_below_sma50"] == 2


def test_bars_below_sma50_resets_on_recovery_above_sma():
    panel = compute_daily_features(_bars_flat_then_dip())
    ts = sorted(panel.keys())
    # Bar 62: close=100 back >= sma_50 (99.6) -> streak resets to 0.
    assert panel[ts[62]]["bars_below_sma50"] == 0
    assert panel[ts[63]]["bars_below_sma50"] == 0


def test_bars_below_sma50_absent_before_sma50_warmup():
    panel = compute_daily_features(
        _bars_flat_then_dip(n_flat=10, dip_days=0, tail=0)
    )
    for feats in panel.values():
        assert "bars_below_sma50" not in feats

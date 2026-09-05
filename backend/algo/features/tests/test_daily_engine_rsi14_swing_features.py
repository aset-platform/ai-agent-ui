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

"""Indicator engine — SMAs + golden_cross_days_ago.

The DAILY indicator path still lives in
``backend.algo.backtest.indicators``; the primitive helpers
(``rolling_sma`` et al.) moved into
``backend.algo.features.primitives`` in FE-4. This test file
keeps the daily-path coverage; primitive-level coverage lives
in ``backend/algo/features/tests/test_primitives.py``.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.algo.backtest.indicators import (
    compute_indicators,
    compute_indicators_for_universe,
)
from backend.algo.backtest.types import BarData
from backend.algo.features.primitives import rolling_sma
from backend.algo.features.version import NO_CROSS_SENTINEL


def _bars(
    closes: list[float], start: date = date(2026, 1, 1)
) -> list[BarData]:
    out: list[BarData] = []
    for i, c in enumerate(closes):
        d = start + timedelta(days=i)
        out.append(
            BarData(
                ticker="X",
                date=d,
                open=Decimal(str(c)),
                high=Decimal(str(c + 1)),
                low=Decimal(str(c - 1)),
                close=Decimal(str(c)),
                volume=1000,
            )
        )
    return out


def test_rolling_sma_window_3():
    out = rolling_sma(
        [Decimal(x) for x in [10, 20, 30, 40, 50]],
        window=3,
    )
    # i=0,1 → None; i=2 → (10+20+30)/3=20; i=3 → (20+30+40)/3=30; i=4 → 40
    assert out == [None, None, Decimal("20"), Decimal("30"), Decimal("40")]


def test_rolling_sma_empty():
    assert rolling_sma([], window=3) == []


def test_compute_indicators_keys_and_sma_values():
    bars = _bars([10, 20, 30, 40, 50])
    out = compute_indicators(bars, sma_windows=(3,))
    last = out[bars[-1].date]
    assert last["today_ltp"] == Decimal("50")
    assert last["sma_3"] == Decimal("40")


def test_golden_cross_sentinel_when_no_cross():
    """SMAs that never cross stay at the sentinel."""
    bars = _bars([100.0] * 250)  # Flat — no cross.
    out = compute_indicators(bars)
    last = out[bars[-1].date]
    assert last["golden_cross_days_ago"] == NO_CROSS_SENTINEL


def test_golden_cross_days_ago_increments():
    """Construct a series where SMA50 is below SMA200 for the
    warmup, then crosses ABOVE near the end. Verify the
    days_ago counter starts at 0 on the cross bar and grows."""
    # First 200 bars trending mildly down so SMA50 ≈ SMA200 but
    # 50 ≤ 200; then last 30 bars sharply up so SMA50 > SMA200.
    closes = [100.0 - 0.05 * i for i in range(200)]
    closes += [closes[-1] + 5.0 + 0.5 * i for i in range(30)]
    bars = _bars(closes)
    out = compute_indicators(bars)
    # Golden cross should fire somewhere in the last 30 bars.
    found_cross = False
    cross_idx = -1
    for i, b in enumerate(bars):
        feats = out.get(b.date, {})
        v = feats.get("golden_cross_days_ago")
        if v is not None and v != NO_CROSS_SENTINEL and v == 0:
            found_cross = True
            cross_idx = i
            break
    assert found_cross, "Expected golden_cross_days_ago=0 on cross bar"
    # Subsequent bars increment by 1 each.
    last_date = bars[-1].date
    expected_age = (last_date - bars[cross_idx].date).days
    assert out[last_date]["golden_cross_days_ago"] == Decimal(
        expected_age,
    )


def test_compute_indicators_for_universe_sorts_per_ticker():
    """Sanity: even if input bars are out-of-order, output uses
    ascending-by-date semantics."""
    bars_a = _bars([10, 20, 30, 40, 50])
    # Reverse the list to confirm internal sort kicks in.
    out = compute_indicators_for_universe(
        {"A": list(reversed(bars_a))},
        sma_windows=(3,),
    )
    last = out["A"][bars_a[-1].date]
    assert last["sma_3"] == Decimal("40")


def test_empty_bars_returns_empty():
    assert compute_indicators([]) == {}
    assert compute_indicators_for_universe({"A": []}) == {}

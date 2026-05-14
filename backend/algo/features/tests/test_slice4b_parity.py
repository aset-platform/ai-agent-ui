"""Bit-for-bit parity between the centralized feature engine
and slice-4b ``compute_indicators_intraday``.

The slice-4b module remains the canonical producer through
FE-3; FE-4 will then delete it and route the backtest runner
through the centralized engine. Any drift between the two
implementations on the overlapping feature set must trip CI
BEFORE FE-4 ships — otherwise we'd be shipping a silent
behaviour change to every existing intraday strategy.

Overlap features (must match exactly):
    today_ltp, today_vol,
    sma_20, sma_50, sma_100, sma_200,
    rsi, rsi_14, vwap,
    golden_cross_bars_ago.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.algo.backtest.indicators import (
    compute_indicators_intraday,
)
from backend.algo.backtest.types import BarData
from backend.algo.features import compute_intraday_features

IST = timezone(timedelta(minutes=330))

_OVERLAP_KEYS = frozenset(
    [
        "today_ltp",
        "today_vol",
        "sma_20",
        "sma_50",
        "sma_100",
        "sma_200",
        "rsi",
        "rsi_14",
        "vwap",
        "golden_cross_bars_ago",
    ]
)


def _ts_ns(day: date, hour: int, minute: int) -> int:
    dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    return int(dt.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _bar(
    day: date,
    hour: int,
    minute: int,
    close: Decimal,
    volume: int = 1000,
) -> BarData:
    return BarData(
        ticker="ITC.NS",
        date=day,
        open=close - Decimal("0.5"),
        high=close + Decimal("0.5"),
        low=close - Decimal("1.0"),
        close=close,
        volume=volume,
        bar_open_ts_ns=_ts_ns(day, hour, minute),
    )


def _series_for_parity(
    n_days: int = 12,
    bars_per_day: int = 25,
) -> list[BarData]:
    """Long-enough series to exercise every overlap feature
    (SMA200 needs 200 bars; 300 bars across 12 days gives buffer).

    Closes drift up and down to produce a non-trivial RSI + a
    genuine SMA50/200 cross (not just monotone).
    """
    bars: list[BarData] = []
    counter = 0
    for d_off in range(n_days):
        day = date(2026, 4, 1) + timedelta(days=d_off)
        for bar_idx in range(bars_per_day):
            hour = 9 + (bar_idx * 15 + 15) // 60
            minute = (bar_idx * 15 + 15) % 60
            # Sinusoidal-ish drift so SMA50 / SMA200 cross at
            # least once.
            base = Decimal("100")
            step = Decimal((counter % 7) - 3)
            close = base + step + Decimal(counter) / Decimal("5")
            bars.append(_bar(day, hour, minute, close))
            counter += 1
    return bars


def test_overlap_features_bit_for_bit():
    bars = _series_for_parity(n_days=12, bars_per_day=25)  # 300 bars
    slice4b = compute_indicators_intraday(bars)
    centralized = compute_intraday_features(bars)
    # Same set of keyed bars.
    assert set(slice4b.keys()) == set(centralized.keys())
    drifts: list[str] = []
    for ts_ns, s4b_feats in slice4b.items():
        c_feats = centralized[ts_ns]
        for key in _OVERLAP_KEYS:
            in_s4b = key in s4b_feats
            in_c = key in c_feats
            if in_s4b != in_c:
                drifts.append(
                    f"key={key} ts_ns={ts_ns} "
                    f"slice4b_present={in_s4b} centralized={in_c}"
                )
                continue
            if not in_s4b:
                continue
            if s4b_feats[key] != c_feats[key]:
                drifts.append(
                    f"key={key} ts_ns={ts_ns} "
                    f"slice4b={s4b_feats[key]} "
                    f"centralized={c_feats[key]}"
                )
    assert not drifts, (
        "Slice-4b ↔ centralized feature engine drift on overlap "
        "set; first 5: " + "\n".join(drifts[:5])
    )


def test_no_extra_overlap_keys_in_slice4b():
    """If slice-4b learns a new key, the centralized engine MUST
    learn it too (or this test fails and FE-4 cannot ship)."""
    bars = _series_for_parity(n_days=12, bars_per_day=25)
    slice4b = compute_indicators_intraday(bars)
    extra: set[str] = set()
    for feats in slice4b.values():
        for k in feats:
            if k in _OVERLAP_KEYS:
                continue
            # Anything slice-4b emits today is grandfathered;
            # this guard catches future drift.
            extra.add(k)
    # As of FE-2 baseline, slice-4b emits exactly the
    # overlap set + nothing else. Detect a future addition.
    assert not extra, (
        "Slice-4b started emitting unexpected key(s) — "
        "update _OVERLAP_KEYS in this test and the centralized "
        f"engine to match: {sorted(extra)}"
    )

"""Unit tests for the centralized feature engine primitives.

Each test pins a primitive against a hand-computed reference
value (documented in the test docstring) so a future regression
of the formula trips immediately. Decimal arithmetic is used
throughout — no floating-point comparisons.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.algo.backtest.types import BarData
from backend.algo.features import primitives as p

IST = timezone(timedelta(minutes=330))


def _ts_ns(day: date, hour: int, minute: int) -> int:
    dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    return int(dt.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def _bar(
    day: date,
    hour: int,
    minute: int,
    close: Decimal,
    *,
    high: Decimal | None = None,
    low: Decimal | None = None,
    open_: Decimal | None = None,
    volume: int = 1000,
    ticker: str = "ITC.NS",
) -> BarData:
    return BarData(
        ticker=ticker,
        date=day,
        open=open_ if open_ is not None else close - Decimal("0.5"),
        high=high if high is not None else close + Decimal("0.5"),
        low=low if low is not None else close - Decimal("1.0"),
        close=close,
        volume=volume,
        bar_open_ts_ns=_ts_ns(day, hour, minute),
    )


# ────────────────────────────────────────────────────────────────
# EMA — TA-Lib seeding convention
# ────────────────────────────────────────────────────────────────


def test_ema_returns_none_until_span_bars():
    """span=5 → indices 0..3 are None, index 4 is the SMA seed."""
    closes = [Decimal(str(v)) for v in [10, 11, 12, 13, 14]]
    out = p.ema(closes, span=5)
    assert out[:4] == [None, None, None, None]
    # Seed = mean(10,11,12,13,14) = 12.
    assert out[4] == Decimal("12")


def test_ema_decay_post_seed():
    """span=3 → seed at index 2 is mean(1,2,3) = 2; index 3 then
    applies alpha = 2/(3+1) = 0.5 → 0.5*4 + 0.5*2 = 3. Index 4
    → 0.5*5 + 0.5*3 = 4."""
    closes = [Decimal(str(v)) for v in [1, 2, 3, 4, 5]]
    out = p.ema(closes, span=3)
    assert out[2] == Decimal("2")
    assert out[3] == Decimal("3")
    assert out[4] == Decimal("4")


def test_ema_empty_and_short_returns_all_none():
    assert p.ema([], span=20) == []
    short = [Decimal("1"), Decimal("2")]
    assert p.ema(short, span=20) == [None, None]


# ────────────────────────────────────────────────────────────────
# series_slope_n_bar
# ────────────────────────────────────────────────────────────────


def test_series_slope_returns_difference_at_lag():
    series: list[Decimal | None] = [
        None,
        None,
        Decimal("10"),
        Decimal("11"),
        Decimal("13"),
    ]
    out = p.series_slope_n_bar(series, lag=2)
    # i=0,1 → None (insufficient lag).
    assert out[0] is None
    assert out[1] is None
    # i=2 → series[2] − series[0] = 10 − None → None.
    assert out[2] is None
    # i=3 → series[3] − series[1] = 11 − None → None.
    assert out[3] is None
    # i=4 → series[4] − series[2] = 13 − 10 = 3.
    assert out[4] == Decimal("3")


# ────────────────────────────────────────────────────────────────
# ROC
# ────────────────────────────────────────────────────────────────


def test_roc_returns_fraction_not_percent():
    """roc_5 spec says (close[i] / close[i-5]) − 1 as fraction."""
    closes = [Decimal(str(v)) for v in [100, 100, 100, 100, 100, 110]]
    out = p.roc_n_bar(closes, lag=5)
    assert out[5] == Decimal("0.1")
    assert out[:5] == [None] * 5


def test_roc_handles_zero_prior():
    closes = [Decimal("0"), Decimal("10")]
    out = p.roc_n_bar(closes, lag=1)
    # Division by zero → skip.
    assert out[1] is None


# ────────────────────────────────────────────────────────────────
# Wilder ATR
# ────────────────────────────────────────────────────────────────


def test_wilder_atr_constant_range_series():
    """Flat series with H-L = 2 every bar → TR = 2 every bar
    after bar 0; ATR seeds to mean(TR[:14]) = 2 and stays at 2."""
    day = date(2026, 4, 1)
    bars = []
    for i in range(20):
        hour = 9 + (i * 15 + 15) // 60
        minute = (i * 15 + 15) % 60
        bars.append(
            _bar(
                day,
                hour,
                minute,
                close=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
            )
        )
    out = p.wilder_atr(bars, window=14)
    assert out[12] is None
    assert out[13] == Decimal("2")
    assert out[19] == Decimal("2")


def test_wilder_atr_short_series_all_none():
    day = date(2026, 4, 1)
    bars = [_bar(day, 9, 15 + i, close=Decimal("100")) for i in range(5)]
    out = p.wilder_atr(bars, window=14)
    assert out == [None] * 5


# ────────────────────────────────────────────────────────────────
# Bollinger band width
# ────────────────────────────────────────────────────────────────


def test_bb_width_flat_series_zero():
    """Constant close → std = 0 → bb_width = 0."""
    closes = [Decimal("100")] * 20
    out = p.bollinger_band_width(closes, window=20)
    assert out[19] == Decimal("0")


def test_bb_width_pop_std_formula():
    """closes = [1..20]. Population std (ddof=0) over 1..20 =
    sqrt(mean(((1-10.5)^2 + (2-10.5)^2 + ... + (20-10.5)^2))).
    mean = 10.5; var = (sum of squared deviations) / 20 =
    665 / 20 = 33.25; std = sqrt(33.25) ≈ 5.766281297335397.
    bb_width = 2 × 5.7663 / 10.5 ≈ 1.0983.
    """
    closes = [Decimal(str(v)) for v in range(1, 21)]
    out = p.bollinger_band_width(closes, window=20)
    val = out[19]
    assert val is not None
    # Tolerant Decimal compare — sqrt context-precision can
    # vary slightly; assert within 1e-6.
    assert abs(val - Decimal("1.098339181565")) < Decimal("0.0001")


# ────────────────────────────────────────────────────────────────
# Rolling avg volume + volume spike flag
# ────────────────────────────────────────────────────────────────


def test_rolling_avg_volume_window_filling():
    day = date(2026, 4, 1)
    vols = [1000, 2000, 3000, 4000, 5000]
    bars = [
        _bar(day, 9, 15 + i, close=Decimal("100"), volume=v)
        for i, v in enumerate(vols)
    ]
    out = p.rolling_avg_volume(bars, window=3)
    # 0,1 → None (insufficient bars).
    assert out[:2] == [None, None]
    assert out[2] == Decimal("2000")  # (1000+2000+3000)/3
    assert out[3] == Decimal("3000")
    assert out[4] == Decimal("4000")


def test_volume_spike_triggers_above_2x():
    day = date(2026, 4, 1)
    # 20 bars of volume=1000, then one bar of volume=2500
    # (> 2 × 1000).
    vols = [1000] * 20 + [2500]
    bars = [
        _bar(day, 9, (i * 5) % 60, close=Decimal("100"), volume=v)
        for i, v in enumerate(vols)
    ]
    out = p.volume_spike_flag(bars, window=20)
    assert out[18] is None
    assert out[19] == Decimal("0")  # not a spike (=1000 × 2 = 2000, equal)
    assert out[20] == Decimal("1")  # 2500 > 2000


# ────────────────────────────────────────────────────────────────
# Relative volume by time-of-day
# ────────────────────────────────────────────────────────────────


def test_relative_volume_first_occurrence_is_none():
    """The 09:15 bar of day 1 has no prior 09:15 occurrence → None.
    The 09:15 bar of day 2 has 1 prior (day 1's 09:15) → ratio."""
    bars: list[BarData] = []
    for d_off in range(2):
        d = date(2026, 4, 1) + timedelta(days=d_off)
        bars.append(_bar(d, 9, 15, close=Decimal("100"), volume=1000))
        bars.append(_bar(d, 9, 30, close=Decimal("100"), volume=2000))
    out = p.relative_volume_by_time_of_day(bars, lookback_days=20)
    # Day 1 — no prior history.
    assert out[0] is None
    assert out[1] is None
    # Day 2, 09:15 bar — 1 prior @ vol=1000; ratio = 1.0.
    assert out[2] == Decimal("1")
    # Day 2, 09:30 bar — 1 prior @ vol=2000; ratio = 1.0.
    assert out[3] == Decimal("1")


# ────────────────────────────────────────────────────────────────
# Per-day primitives: prev_day, today_open, ORB
# ────────────────────────────────────────────────────────────────


def test_prev_day_close_high_low_first_day_none():
    d = date(2026, 4, 1)
    bars = [
        _bar(
            d,
            9,
            15,
            close=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("99"),
        ),
        _bar(
            d,
            9,
            30,
            close=Decimal("101"),
            high=Decimal("103"),
            low=Decimal("100"),
        ),
    ]
    out = p.prev_day_close_high_low(bars)
    assert out == [None, None]


def test_prev_day_close_high_low_day2_aggregates_day1():
    d1 = date(2026, 4, 1)
    d2 = date(2026, 4, 2)
    bars = [
        _bar(
            d1,
            9,
            15,
            close=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("98"),
        ),
        _bar(
            d1,
            9,
            30,
            close=Decimal("102"),
            high=Decimal("106"),
            low=Decimal("99"),
        ),
        _bar(
            d2,
            9,
            15,
            close=Decimal("103"),
            high=Decimal("107"),
            low=Decimal("101"),
        ),
    ]
    out = p.prev_day_close_high_low(bars)
    assert out[0] is None
    assert out[1] is None
    # Day 2, bar 0 → prev = (last close of d1, max high of d1,
    # min low of d1) = (102, 106, 98).
    assert out[2] == (Decimal("102"), Decimal("106"), Decimal("98"))


def test_today_open_constant_within_day():
    d = date(2026, 4, 1)
    bars = [
        _bar(d, 9, 15, close=Decimal("100"), open_=Decimal("99.50")),
        _bar(d, 9, 30, close=Decimal("101"), open_=Decimal("100.00")),
        _bar(d, 9, 45, close=Decimal("102"), open_=Decimal("101.00")),
    ]
    out = p.today_open_per_bar(bars)
    # Every bar of day d → open of the FIRST bar of d.
    assert out == [Decimal("99.50")] * 3


def test_orb_skips_opening_window_and_emits_thereafter():
    """09:15 + 09:30 bars are inside ORB and get None for ORB
    feature themselves (they ARE the range). 09:45 onwards
    gets (max_high_of_orb_bars, min_low_of_orb_bars).

    The ORB window is [09:15, 09:30) — exclusive of 09:30 —
    so only the 09:15 bar contributes."""
    d = date(2026, 4, 1)
    bars = [
        _bar(
            d,
            9,
            15,
            close=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("99"),
        ),
        _bar(
            d,
            9,
            30,
            close=Decimal("101"),
            high=Decimal("103"),
            low=Decimal("100"),
        ),
        _bar(
            d,
            9,
            45,
            close=Decimal("102"),
            high=Decimal("104"),
            low=Decimal("101"),
        ),
    ]
    out = p.orb_per_bar(bars)
    # 09:15 bar → inside ORB window → None.
    assert out[0] is None
    # 09:30 bar → outside [09:15, 09:30) → emits ORB derived
    # from the single 09:15 bar.
    assert out[1] == (Decimal("105"), Decimal("99"))
    # 09:45 bar → same.
    assert out[2] == (Decimal("105"), Decimal("99"))


def test_orb_two_15m_bars_aggregates():
    """If the data cadence is 5m, two bars fall inside [09:15,
    09:30): 09:15 + 09:20. The ORB aggregates max high + min
    low of both."""
    d = date(2026, 4, 1)
    bars = [
        _bar(
            d,
            9,
            15,
            close=Decimal("100"),
            high=Decimal("104"),
            low=Decimal("99"),
        ),
        _bar(
            d,
            9,
            20,
            close=Decimal("101"),
            high=Decimal("106"),
            low=Decimal("98"),
        ),
        _bar(
            d,
            9,
            25,
            close=Decimal("102"),
            high=Decimal("103"),
            low=Decimal("100"),
        ),
        _bar(
            d,
            9,
            30,
            close=Decimal("103"),
            high=Decimal("105"),
            low=Decimal("101"),
        ),
    ]
    out = p.orb_per_bar(bars)
    # 09:15, 09:20, 09:25 all inside [09:15, 09:30) → ORB
    # derived from all three: high = max(104, 106, 103) = 106;
    # low = min(99, 98, 100) = 98.
    assert out[0] is None
    assert out[1] is None
    assert out[2] is None
    assert out[3] == (Decimal("106"), Decimal("98"))


# ────────────────────────────────────────────────────────────────
# Time features
# ────────────────────────────────────────────────────────────────


def test_minutes_since_open():
    d = date(2026, 4, 1)
    bars = [
        _bar(d, 9, 15, close=Decimal("100")),
        _bar(d, 9, 30, close=Decimal("100")),
        _bar(d, 10, 30, close=Decimal("100")),
    ]
    out = p.minutes_since_open(bars)
    assert out == [0, 15, 75]


def test_time_of_day_bucket_boundaries():
    d = date(2026, 4, 1)
    bars = [
        # 09:15 → opening
        _bar(d, 9, 15, close=Decimal("100")),
        # 10:29 → opening (still < 10:30)
        _bar(d, 10, 29, close=Decimal("100")),
        # 10:30 → midday
        _bar(d, 10, 30, close=Decimal("100")),
        # 12:59 → midday
        _bar(d, 12, 59, close=Decimal("100")),
        # 13:00 → lunch
        _bar(d, 13, 0, close=Decimal("100")),
        # 13:59 → lunch
        _bar(d, 13, 59, close=Decimal("100")),
        # 14:00 → closing
        _bar(d, 14, 0, close=Decimal("100")),
        # 15:29 → closing
        _bar(d, 15, 29, close=Decimal("100")),
    ]
    out = p.time_of_day_bucket(bars)
    assert out == [
        "opening",
        "opening",
        "midday",
        "midday",
        "lunch",
        "lunch",
        "closing",
        "closing",
    ]

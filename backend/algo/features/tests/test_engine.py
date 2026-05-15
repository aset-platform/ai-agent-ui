"""Integration tests for the centralized feature engine.

Verifies the per-bar feature dict shape — keys emitted on the
expected bars, absent keys (NOT None) for warmup-truncated
features, and the universe wrapper preserving per-ticker
isolation.

Bit-for-bit slice-4b parity is covered by
``test_slice4b_parity.py`` — this file focuses on the new
Phase-1 features added by FE-2.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.algo.backtest.types import BarData
from backend.algo.features import (
    compute_intraday_features,
    compute_intraday_features_for_universe,
)

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


def _series(n_days: int = 10, bars_per_day: int = 25) -> list[BarData]:
    bars: list[BarData] = []
    counter = 0
    for d_off in range(n_days):
        day = date(2026, 4, 1) + timedelta(days=d_off)
        for bar_idx in range(bars_per_day):
            hour = 9 + (bar_idx * 15 + 15) // 60
            minute = (bar_idx * 15 + 15) % 60
            close = Decimal("100") + Decimal(counter)
            bars.append(_bar(day, hour, minute, close))
            counter += 1
    return bars


# ────────────────────────────────────────────────────────────────
# Shape contract
# ────────────────────────────────────────────────────────────────


def test_empty_input_returns_empty_dict():
    assert compute_intraday_features([]) == {}


def test_daily_shaped_bars_skipped():
    daily = [
        BarData(
            ticker="X",
            date=date(2026, 4, 1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=1000,
        )
    ]
    assert compute_intraday_features(daily) == {}


def test_single_bar_emits_only_trivial_and_time_features():
    """A single bar with no history can produce: today_ltp,
    today_vol, golden_cross_bars_ago (always sentinel for the
    first bar with i=0 — slice-4b behaviour), minutes_since_open,
    time_of_day_bucket. No SMA, no RSI, no VWAP-distance, etc.
    """
    day = date(2026, 4, 1)
    bars = [_bar(day, 9, 15, close=Decimal("100"))]
    out = compute_intraday_features(bars)
    feats = out[bars[0].bar_open_ts_ns]
    # Always emitted.
    assert feats["today_ltp"] == Decimal("100")
    assert feats["today_vol"] == Decimal("1000")
    assert feats["minutes_since_open"] == Decimal("0")
    assert feats["time_of_day_bucket"] == "opening"
    # VWAP IS computable even on a single bar — it just equals
    # the typical price.
    assert "vwap" in feats
    # dist_from_vwap_pct: close == vwap_typical → 0 if close
    # equals (h+l+c)/3 — for our synthetic bar with high=close+
    # 0.5 and low=close-1, typical ≠ close so it's emitted.
    assert "dist_from_vwap_pct" in feats
    # Warmup-truncated features must be ABSENT (not None).
    for absent in (
        "sma_20",
        "sma_50",
        "sma_100",
        "sma_200",
        "ema_20",
        "ema_50",
        "rsi",
        "rsi_14",
        "rsi_5",
        "roc_5",
        "atr_14",
        "bb_width",
        "relative_volume",
        "volume_spike",
        "gap_pct",
        "orb_high_15min",
        "ema_20_slope_5bar",
    ):
        assert (
            absent not in feats
        ), f"{absent} should be absent on bar 0, got {feats.get(absent)}"


def test_warmup_truncation_uses_absent_keys_not_none():
    """Engine MUST skip emitting None — strategies use KeyError
    counting per CLAUDE.md feature-key-error contract."""
    bars = _series(n_days=2, bars_per_day=5)  # 10 bars total.
    out = compute_intraday_features(bars)
    # sma_200 isn't ready until bar 200 → absent on bar 5.
    feats = out[bars[5].bar_open_ts_ns]
    assert "sma_200" not in feats
    # And NEVER emitted as None.
    for v in feats.values():
        assert v is not None


# ────────────────────────────────────────────────────────────────
# Day-2: gap, prev-day distances, VWAP reset
# ────────────────────────────────────────────────────────────────


def test_gap_pct_and_prev_day_distances_emit_on_day2():
    """Day 1 has 3 bars; day 2 has 1 bar. The day-2 bar gets
    gap_pct, dist_from_prev_day_high_pct, dist_from_prev_day_low_pct
    derived from day-1 aggregates."""
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
            open_=Decimal("99"),
        ),
        _bar(
            d1,
            9,
            30,
            close=Decimal("101"),
            high=Decimal("106"),
            low=Decimal("100"),
            open_=Decimal("100"),
        ),
        _bar(
            d1,
            9,
            45,
            close=Decimal("102"),
            high=Decimal("104"),
            low=Decimal("100"),
            open_=Decimal("101"),
        ),
        _bar(
            d2,
            9,
            15,
            close=Decimal("110"),
            high=Decimal("111"),
            low=Decimal("108"),
            open_=Decimal("109"),
        ),
    ]
    out = compute_intraday_features(bars)
    feats_d2 = out[bars[3].bar_open_ts_ns]
    # prev_close (last of d1) = 102; today_open = 109 → gap = +6.86%
    # (109 − 102) / 102 × 100 = 6.862745...%.
    gap = feats_d2["gap_pct"]
    assert isinstance(gap, Decimal)
    assert abs(gap - Decimal("6.862745098")) < Decimal("0.001")
    # prev_day_high = 106 → dist = (110 − 106) / 106 × 100 ≈ 3.77%.
    high_dist = feats_d2["dist_from_prev_day_high_pct"]
    assert isinstance(high_dist, Decimal)
    assert abs(high_dist - Decimal("3.7735849")) < Decimal("0.001")
    # prev_day_low = 98 → dist = (110 − 98) / 98 × 100 ≈ 12.24%.
    low_dist = feats_d2["dist_from_prev_day_low_pct"]
    assert isinstance(low_dist, Decimal)
    assert abs(low_dist - Decimal("12.2448979")) < Decimal("0.001")
    # Day-1 bars must NOT have any of these.
    feats_d1 = out[bars[0].bar_open_ts_ns]
    assert "gap_pct" not in feats_d1
    assert "dist_from_prev_day_high_pct" not in feats_d1


def test_vwap_resets_at_calendar_day_boundary():
    bars = _series(n_days=2, bars_per_day=25)
    out = compute_intraday_features(bars)
    # First bar of day 2 = index 25.
    feats_first_d2 = out[bars[25].bar_open_ts_ns]
    typical = (bars[25].high + bars[25].low + bars[25].close) / Decimal("3")
    assert feats_first_d2["vwap"] == typical


# ────────────────────────────────────────────────────────────────
# Universe wrapper
# ────────────────────────────────────────────────────────────────


def test_universe_wrapper_keys_by_ticker():
    out = compute_intraday_features_for_universe(
        {
            "ITC.NS": _series(n_days=1, bars_per_day=3),
            "RELIANCE.NS": _series(n_days=1, bars_per_day=3),
            "EMPTY.NS": [],
        }
    )
    assert set(out.keys()) == {"ITC.NS", "RELIANCE.NS"}
    for tkr in ("ITC.NS", "RELIANCE.NS"):
        for ts_ns, feats in out[tkr].items():
            assert isinstance(ts_ns, int)
            assert "today_ltp" in feats


# ────────────────────────────────────────────────────────────────
# String feature: time_of_day_bucket
# ────────────────────────────────────────────────────────────────


def test_time_of_day_bucket_is_string_type():
    d = date(2026, 4, 1)
    bars = [_bar(d, 13, 30, close=Decimal("100"))]
    out = compute_intraday_features(bars)
    feats = out[bars[0].bar_open_ts_ns]
    val = feats["time_of_day_bucket"]
    assert isinstance(val, str)
    assert val == "lunch"


# ────────────────────────────────────────────────────────────────
# All-features readiness on a long series
# ────────────────────────────────────────────────────────────────


def test_long_series_emits_all_phase1_features():
    """≥ 250 bars across multiple days → every Phase-1 feature
    (except RS-vs-* which defer to FE-8) is present on the last
    bar."""
    bars = _series(n_days=10, bars_per_day=25)  # 250 bars
    out = compute_intraday_features(bars)
    last = out[bars[-1].bar_open_ts_ns]
    expected = {
        "today_ltp",
        "today_vol",
        "vwap",
        "dist_from_vwap_pct",
        "sma_20",
        "sma_50",
        "sma_100",
        "sma_200",
        "ema_20",
        "ema_50",
        "ema_20_slope_5bar",
        "rsi",
        "rsi_14",
        "rsi_5",
        "roc_5",
        "atr_14",
        "range_expansion",
        "bb_width",
        "relative_volume",
        "volume_spike",
        "gap_pct",
        "dist_from_prev_day_high_pct",
        "dist_from_prev_day_low_pct",
        "minutes_since_open",
        "time_of_day_bucket",
        "golden_cross_bars_ago",
    }
    missing = expected - set(last.keys())
    assert not missing, f"Missing on last bar: {sorted(missing)}"
    # ORB is constant within a trading day — assert orb_high /
    # orb_low present on last bar (last bar is at end of day 10,
    # well past 09:30).
    assert "orb_high_15min" in last
    assert "orb_low_15min" in last

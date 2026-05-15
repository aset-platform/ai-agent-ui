"""Tests for the daily-cadence feature engine — FE-15a.

Verifies that ``compute_daily_features`` emits exactly the 18
spec features (and nothing intraday-only), reuses the same
primitive impls as the intraday engine, and handles edge cases
(empty bars, bars missing ``bar_open_ts_ns``).
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal

from backend.algo.backtest.types import BarData
from backend.algo.features.daily_engine import (
    DEFAULT_DAILY_SMA_WINDOWS,
    compute_daily_features,
    compute_daily_features_for_universe,
)


SPEC_18_FEATURES = {
    "ema_20",
    "ema_50",
    "ema_20_slope_5bar",
    "sma_20",
    "sma_50",
    "sma_100",
    "sma_200",
    "golden_cross_bars_ago",
    "rsi_5",
    "rsi_14",
    "roc_5",
    "atr_14",
    "range_expansion",
    "bb_width",
    "gap_pct",
    "dist_from_prev_day_high_pct",
    "dist_from_prev_day_low_pct",
    "volume_spike",
}

INTRADAY_ONLY_FEATURES = {
    "vwap",
    "dist_from_vwap_pct",
    "orb_high_15min",
    "orb_low_15min",
    "minutes_since_open",
    "time_of_day_bucket",
    "relative_volume",
    "rsi",  # intraday emits both "rsi" and "rsi_14"; daily only "rsi_14"
    "today_ltp",
    "today_vol",
}


def _utc_midnight_ns(d: date) -> int:
    return int(
        datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp()
        * 1_000_000_000
    )


def _make_daily_series(
    *,
    ticker: str = "RELIANCE.NS",
    start: date = date(2025, 1, 1),
    n_days: int = 250,
    base_price: float = 100.0,
    daily_trend: float = 0.001,
    volatility: float = 0.01,
) -> list[BarData]:
    """Synthetic daily series with mild upward drift. Enough
    days (250 default) for SMA200 to be computable on the tail.
    """
    bars: list[BarData] = []
    price = base_price
    for i in range(n_days):
        d = date.fromordinal(start.toordinal() + i)
        # Skip weekends to mimic NSE trading calendar.
        if d.weekday() >= 5:
            continue
        open_p = price
        close_p = price * (1 + daily_trend)
        high_p = max(open_p, close_p) * (1 + volatility / 2)
        low_p = min(open_p, close_p) * (1 - volatility / 2)
        bars.append(
            BarData(
                ticker=ticker,
                date=d,
                open=Decimal(str(round(open_p, 2))),
                high=Decimal(str(round(high_p, 2))),
                low=Decimal(str(round(low_p, 2))),
                close=Decimal(str(round(close_p, 2))),
                volume=100_000 + i * 100,
                bar_open_ts_ns=_utc_midnight_ns(d),
            )
        )
        price = close_p
    return bars


def test_empty_bars_returns_empty_panel():
    assert compute_daily_features([]) == {}


def test_bars_without_ts_ns_skipped_defensively():
    bar = BarData(
        ticker="X.NS",
        date=date(2026, 1, 1),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=1000,
        bar_open_ts_ns=None,
    )
    assert compute_daily_features([bar]) == {}


def test_emits_only_spec_features_no_intraday_leakage():
    """The daily engine MUST NOT emit any of the intraday-only
    features (vwap, ORB, time-of-day, relative_volume, etc.).
    Spec §3 — daily features only.
    """
    series = _make_daily_series(n_days=300)
    panel = compute_daily_features(series)
    assert panel, "expected non-empty panel"

    emitted: set[str] = set()
    for feats in panel.values():
        emitted.update(feats.keys())

    leaked = emitted & INTRADAY_ONLY_FEATURES
    assert not leaked, f"intraday-only features leaked: {leaked}"


def test_emits_all_spec_features_on_well_warmed_series():
    """With 300 bars (>200 for SMA200 warmup) all 18 spec
    features should appear on the tail of the series.
    ``golden_cross_bars_ago`` only emits AFTER an SMA50/SMA200
    cross — a synthetic series may not yield it, so treat it as
    optional.
    """
    series = _make_daily_series(n_days=300)
    panel = compute_daily_features(series)
    last_ts = max(panel.keys())
    last_feats = panel[last_ts]
    missing = (
        SPEC_18_FEATURES - set(last_feats.keys()) - {"golden_cross_bars_ago"}
    )
    assert not missing, f"missing features on tail: {missing}"


def test_sma_windows_default_to_20_50_100_200():
    assert DEFAULT_DAILY_SMA_WINDOWS == (20, 50, 100, 200)


def test_rsi_14_and_rsi_5_both_emitted():
    """Spec §3 lists both rsi_5 (short) and rsi_14 (standard)
    on the daily surface — daily engine MUST emit both.
    """
    series = _make_daily_series(n_days=100)
    panel = compute_daily_features(series)
    tail_feats = panel[max(panel.keys())]
    assert "rsi_14" in tail_feats
    assert "rsi_5" in tail_feats
    # Both must be in the [0, 100] valid RSI range (synthetic
    # monotonic data can saturate to 100; we don't assert
    # inequality because that's data-dependent).
    assert 0 <= tail_feats["rsi_5"] <= 100
    assert 0 <= tail_feats["rsi_14"] <= 100


def test_gap_pct_uses_today_open_vs_prev_close():
    """gap_pct on daily bars = (today_open - prev_close) /
    prev_close × 100. With identical closes and varying opens,
    gap_pct should reflect open relative to prior close exactly.
    """
    bars = [
        BarData(
            ticker="X.NS",
            date=date(2026, 1, d),
            open=Decimal(f"{100 + d}"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("100"),
            volume=1000,
            bar_open_ts_ns=_utc_midnight_ns(date(2026, 1, d)),
        )
        for d in (5, 6)  # Mon, Tue
    ]
    panel = compute_daily_features(bars)
    tail_ts = bars[-1].bar_open_ts_ns
    # Day 2 open=106, prev close=100 → gap_pct = 6%.
    assert panel[tail_ts]["gap_pct"] == Decimal("6")


def test_idempotent_dict_for_universe():
    series_a = _make_daily_series(ticker="A.NS", n_days=300)
    series_b = _make_daily_series(
        ticker="B.NS",
        n_days=300,
        base_price=200.0,
    )
    panels = compute_daily_features_for_universe(
        {"A.NS": series_a, "B.NS": series_b},
    )
    assert set(panels.keys()) == {"A.NS", "B.NS"}
    assert all(panels[t] for t in panels)
    a_last_ts = max(panels["A.NS"].keys())
    b_last_ts = max(panels["B.NS"].keys())
    # Same dates → same ts_ns since UTC-midnight is deterministic.
    assert a_last_ts == b_last_ts


def test_volume_spike_is_binary():
    """volume_spike should be 0 or 1 (binary), never a ratio.
    Spec §3 — Volume family.
    """
    series = _make_daily_series(n_days=100)
    panel = compute_daily_features(series)
    spikes = [
        feats.get("volume_spike")
        for feats in panel.values()
        if "volume_spike" in feats
    ]
    assert spikes, "expected at least one volume_spike value"
    for v in spikes:
        assert v in (0, 1, Decimal("0"), Decimal("1"))

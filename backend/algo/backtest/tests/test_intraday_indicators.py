"""Indicators on intraday cadence (ASETPLTFRM-400 slice 4b).

The slice-3 runner shipped intraday strategies with bar-level
features ONLY (``today_ltp`` / ``today_vol``). Operator's ask:
RSI, SMA20/50/100/200, VWAP available at the bar level so
typical intraday strategies (mean-reversion vs VWAP, SMA
cross-over on 15m, RSI(14) oversold bounce) can be backtested
end-to-end.

Covered here:
- ``compute_indicators_intraday`` emits the spec'd feature
  set keyed by ``bar_open_ts_ns`` (not ``bar.date`` — the
  daily engine's collision).
- VWAP resets at each calendar-day boundary inside the
  intraday series (the standard NSE-session definition).
- SMA200 emits only once ≥ 200 bars of history are present;
  earlier bars get no ``sma_200`` key (strategies gated on
  ``sma_200`` silently no-op during warmup, which is the
  correct behaviour).
- RSI(14) settles after 14 bars; before that, no ``rsi`` key.
- ``DEFAULT_INTRADAY_SMA_WINDOWS`` pinned to (20, 50, 100, 200)
  so the operator-requested set is the default.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.algo.backtest.indicators import (
    DEFAULT_INTRADAY_SMA_WINDOWS,
    DEFAULT_INTRADAY_WARMUP_DAYS,
    compute_indicators_for_universe_intraday,
    compute_indicators_intraday,
)
from backend.algo.backtest.types import BarData

IST = timezone(timedelta(minutes=330))


def _bar(
    ticker: str,
    day: date,
    hour: int,
    minute: int,
    close: Decimal,
    vol: int = 1000,
) -> BarData:
    open_dt = datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=IST,
    )
    ts_ns = int(
        open_dt.astimezone(timezone.utc).timestamp() * 1_000_000_000,
    )
    return BarData(
        ticker=ticker,
        date=day,
        open=close - Decimal("0.5"),
        high=close + Decimal("0.5"),
        low=close - Decimal("1.0"),
        close=close,
        volume=vol,
        bar_open_ts_ns=ts_ns,
    )


def _intraday_series(
    n_days: int = 10,
    bars_per_day: int = 25,
    *,
    monotonic_up: bool = True,
) -> list[BarData]:
    """Build a synthetic intraday bar series. Each day has
    ``bars_per_day`` × 15m bars opening 09:15 IST. Closes can
    trend up monotonically (``monotonic_up=True``) for a clean
    RSI calculation, or oscillate for VWAP / SMA tests."""
    bars: list[BarData] = []
    counter = 0
    for d_off in range(n_days):
        day = date(2026, 4, 1) + timedelta(days=d_off)
        for bar_idx in range(bars_per_day):
            hour = 9 + (bar_idx * 15) // 60
            minute = (15 + (bar_idx * 15)) % 60
            close = (
                Decimal("100") + Decimal(counter)
                if monotonic_up
                else Decimal("100") + Decimal((counter % 5) - 2)
            )
            bars.append(
                _bar(
                    "ITC.NS",
                    day,
                    hour,
                    minute,
                    close,
                )
            )
            counter += 1
    return bars


# ────────────────────────────────────────────────────────────────
# Module-level constants
# ────────────────────────────────────────────────────────────────


def test_default_intraday_sma_windows_match_operator_spec():
    """Pinned so a future "reduce defaults to (20, 50, 200)"
    regression fails CI — operator explicitly requested 100."""
    assert DEFAULT_INTRADAY_SMA_WINDOWS == (20, 50, 100, 200)


def test_default_intraday_warmup_is_20_calendar_days():
    """SMA(200) at 15m needs ~8 NSE trading days of history;
    20 calendar days covers weekends + a few NSE holidays."""
    assert DEFAULT_INTRADAY_WARMUP_DAYS == 20


# ────────────────────────────────────────────────────────────────
# Per-bar emission shape
# ────────────────────────────────────────────────────────────────


def test_empty_bars_returns_empty_dict():
    assert compute_indicators_intraday([]) == {}


def test_keys_are_bar_open_ts_ns_not_bar_date():
    """The whole reason for the slice — keying by ts_ns lets
    every bar in the same day have its own feature dict."""
    bars = _intraday_series(n_days=1, bars_per_day=5)
    out = compute_indicators_intraday(bars)
    assert len(out) == 5
    expected_keys = {b.bar_open_ts_ns for b in bars}
    assert set(out.keys()) == expected_keys


def test_bars_without_bar_open_ts_ns_are_skipped():
    """Defensive — a caller passing daily-shaped bars
    (``bar_open_ts_ns=None``) silently gets {} instead of a
    corrupted output keyed on ``None``."""
    daily_bars = [
        BarData(
            ticker="ITC.NS",
            date=date(2026, 4, 1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=1000,
        ),
    ]
    assert compute_indicators_intraday(daily_bars) == {}


def test_today_ltp_and_today_vol_always_emitted():
    """Even on warmup bars before SMA/RSI settle, ``today_ltp``
    and ``today_vol`` must be present — strategies fall back to
    them when indicators aren't available."""
    bars = _intraday_series(n_days=1, bars_per_day=3)
    out = compute_indicators_intraday(bars)
    for bar in bars:
        feats = out[bar.bar_open_ts_ns]
        assert feats["today_ltp"] == bar.close
        assert feats["today_vol"] == Decimal(bar.volume)


# ────────────────────────────────────────────────────────────────
# Individual indicators
# ────────────────────────────────────────────────────────────────


def test_sma_emits_for_each_default_window():
    """≥ 200 bars → all four SMAs present on the last bar."""
    bars = _intraday_series(n_days=10, bars_per_day=25)  # 250
    out = compute_indicators_intraday(bars)
    last_ts = bars[-1].bar_open_ts_ns
    feats = out[last_ts]
    for w in (20, 50, 100, 200):
        assert (
            f"sma_{w}" in feats
        ), f"sma_{w} missing at bar 250 — warmup miscalculated?"


def test_sma200_absent_before_200_bars_present():
    """First 199 bars have no ``sma_200`` key (rolling window
    isn't full). Strategy that gates on sma_200 must silently
    no-op during warmup — the absent key triggers a feature-
    key-error which the runner counts but doesn't crash on."""
    bars = _intraday_series(n_days=8, bars_per_day=25)  # 200
    out = compute_indicators_intraday(bars)
    # Bar 199 (0-indexed) is exactly the 200th bar → first
    # sma_200 emission. Bar 198 should be the last without it.
    assert "sma_200" not in out[bars[198].bar_open_ts_ns]
    assert "sma_200" in out[bars[199].bar_open_ts_ns]


def test_rsi_14_emits_after_warmup():
    """RSI(14) needs 14 bars of history; output[14] is the first
    bar with an RSI value. The trending-up series should land
    RSI ≥ 70 (overbought) within a few bars after settle."""
    bars = _intraday_series(n_days=1, bars_per_day=25)
    out = compute_indicators_intraday(bars)
    # Bar 14 (0-indexed) is the 15th bar = first RSI emission.
    assert "rsi" not in out[bars[13].bar_open_ts_ns]
    rsi_at_14 = out[bars[14].bar_open_ts_ns]["rsi"]
    assert rsi_at_14 == Decimal(
        "100"
    ), "monotonic-up series with no losses → RSI = 100"
    # ``rsi_14`` is an alias for ``rsi`` (operator may reference
    # either key in a strategy).
    assert out[bars[14].bar_open_ts_ns]["rsi_14"] == rsi_at_14


def test_vwap_resets_at_calendar_day_boundary():
    """The standard NSE-session VWAP definition: cumulative
    within day, reset at midnight. First bar of day 2 should
    have VWAP ≈ its own typical_price (single observation),
    NOT a continuation of day 1's running mean."""
    bars = _intraday_series(n_days=2, bars_per_day=25)
    out = compute_indicators_intraday(bars)
    # First bar of day 2 = index 25.
    first_bar_day2 = bars[25]
    assert first_bar_day2.date == date(2026, 4, 2)
    feats = out[first_bar_day2.bar_open_ts_ns]
    typical = (
        first_bar_day2.high + first_bar_day2.low + first_bar_day2.close
    ) / Decimal("3")
    # Single-observation VWAP = typical price exactly.
    assert feats["vwap"] == typical


def test_vwap_accumulates_within_day():
    """Within a single day, VWAP[i] ≠ VWAP[i-1] (unless prices
    are flat). The running mean drifts with the price path."""
    bars = _intraday_series(n_days=1, bars_per_day=10)
    out = compute_indicators_intraday(bars)
    vwap_first = out[bars[0].bar_open_ts_ns]["vwap"]
    vwap_last = out[bars[-1].bar_open_ts_ns]["vwap"]
    assert (
        vwap_first != vwap_last
    ), "rising series should make VWAP drift across the day"


# ────────────────────────────────────────────────────────────────
# Universe wrapper
# ────────────────────────────────────────────────────────────────


def test_universe_wrapper_keys_by_ticker_then_ts_ns():
    bars_by_ticker = {
        "ITC.NS": _intraday_series(n_days=1, bars_per_day=3),
        "RELIANCE.NS": _intraday_series(n_days=1, bars_per_day=3),
    }
    out = compute_indicators_for_universe_intraday(bars_by_ticker)
    assert set(out.keys()) == {"ITC.NS", "RELIANCE.NS"}
    for ticker, feats_by_ts in out.items():
        for ts_ns in feats_by_ts:
            assert isinstance(ts_ns, int)
            assert "today_ltp" in feats_by_ts[ts_ns]


def test_universe_wrapper_skips_empty_ticker():
    out = compute_indicators_for_universe_intraday(
        {
            "EMPTY.NS": [],
            "ITC.NS": _intraday_series(
                n_days=1,
                bars_per_day=2,
            ),
        },
    )
    assert "EMPTY.NS" not in out
    assert "ITC.NS" in out

"""Tests for the 5 ``stocks.intraday_bars`` quality assertions
(ASETPLTFRM-400 slice 1e).

Each assertion is exercised against happy + failure inputs. The
orchestrator helper ``run_post_ingest_assertions`` is tested end-
to-end with an in-memory ``events_sink`` capturing the emitted
``data_quality_violation`` payloads.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from backend.algo.backtest.intraday_quality import (
    _BAR_COUNT_FLOORS,
    bar_count_floor,
    bar_open_ts_monotonic,
    build_assertions,
    cross_source_close_match,
    ohlc_not_nan,
    ohlc_self_consistent,
    run_post_ingest_assertions,
)
from backend.algo.backtest.types import BarData


def _bar(
    ticker,
    day,
    hour,
    minute,
    *,
    o=99.5,
    h=100.5,
    lo=99.0,
    c=100.0,
    vol=10,
):
    from datetime import timedelta as _td

    ist = timezone(_td(minutes=330))
    open_dt = datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=ist,
    )
    ns = int(
        open_dt.astimezone(timezone.utc).timestamp() * 1_000_000_000,
    )
    return BarData(
        ticker=ticker,
        date=day,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=vol,
        bar_open_ts_ns=ns,
    )


# ────────────────────────────────────────────────────────────────
# bar_count_floor
# ────────────────────────────────────────────────────────────────


def test_bar_count_floor_passes_when_full_day():
    """25 × 15m bars on one day → above 23-bar floor."""
    bars = [_bar("ITC.NS", date(2026, 4, 1), 9, 15 + i) for i in range(25)]
    a = bar_count_floor(900)
    r = a.evaluate({"bars": bars})
    assert r.passed, r.message


def test_bar_count_floor_fails_when_short_session():
    """A half-day with only 10 × 15m bars → 10 < 23 → warn."""
    bars = [_bar("ITC.NS", date(2026, 4, 1), 9, 15 + i) for i in range(10)]
    a = bar_count_floor(900)
    r = a.evaluate({"bars": bars})
    assert not r.passed
    assert r.severity == "warn"
    assert r.detail["below_floor_count"] == 1
    assert r.detail["floor"] == 23


def test_bar_count_floor_empty_bars_passes():
    """No bars → vacuously passes (empty batch isn't a violation;
    the universe-empty status from slice 1d handles that)."""
    a = bar_count_floor(900)
    r = a.evaluate({"bars": []})
    assert r.passed


@pytest.mark.parametrize(
    "iv,expected",
    [
        (60, 350),
        (300, 70),
        (900, 23),
    ],
)
def test_bar_count_floors_match_spec(iv, expected):
    assert _BAR_COUNT_FLOORS[iv] == expected


# ────────────────────────────────────────────────────────────────
# ohlc_not_nan
# ────────────────────────────────────────────────────────────────


def test_ohlc_not_nan_passes_on_clean_bars():
    bars = [
        _bar("ITC.NS", date(2026, 4, 1), 9, 15),
        _bar("ITC.NS", date(2026, 4, 1), 9, 30),
    ]
    r = ohlc_not_nan().evaluate({"bars": bars})
    assert r.passed


def test_ohlc_not_nan_fails_on_nan_close():
    """Bypass the Decimal type checker — float NaN reaching the
    assertion is the regression we're guarding."""
    bars = [_bar("ITC.NS", date(2026, 4, 1), 9, 15)]
    # Mutate the bar to inject a NaN. BarData uses Decimal so we
    # patch the attribute directly.
    object.__setattr__(bars[0], "close", math.nan)
    r = ohlc_not_nan().evaluate({"bars": bars})
    assert not r.passed
    assert r.severity == "error"
    assert "NaN" in str(r.detail.get("sample", ""))


# ────────────────────────────────────────────────────────────────
# bar_open_ts_monotonic
# ────────────────────────────────────────────────────────────────


def test_bar_open_ts_monotonic_passes_when_ordered():
    bars = [
        _bar("ITC.NS", date(2026, 4, 1), 9, 15),
        _bar("ITC.NS", date(2026, 4, 1), 9, 30),
        _bar("ITC.NS", date(2026, 4, 1), 9, 45),
    ]
    r = bar_open_ts_monotonic().evaluate(
        {"bars": bars, "interval_sec": 900},
    )
    assert r.passed


def test_bar_open_ts_monotonic_fails_on_duplicate():
    same = _bar("ITC.NS", date(2026, 4, 1), 9, 15)
    bars = [same, same]  # identical bar_open_ts_ns → not strict asc
    r = bar_open_ts_monotonic().evaluate(
        {"bars": bars, "interval_sec": 900},
    )
    assert not r.passed
    assert r.severity == "error"


def test_bar_open_ts_monotonic_fails_on_out_of_order():
    bars = [
        _bar("ITC.NS", date(2026, 4, 1), 9, 30),
        _bar("ITC.NS", date(2026, 4, 1), 9, 15),  # earlier
    ]
    r = bar_open_ts_monotonic().evaluate(
        {"bars": bars, "interval_sec": 900},
    )
    assert not r.passed


def test_bar_open_ts_monotonic_isolates_tickers():
    """Per-ticker ordering — interleaved-ticker bars are fine."""
    bars = [
        _bar("A.NS", date(2026, 4, 1), 9, 15),
        _bar("B.NS", date(2026, 4, 1), 9, 15),
        _bar("A.NS", date(2026, 4, 1), 9, 30),
        _bar("B.NS", date(2026, 4, 1), 9, 30),
    ]
    r = bar_open_ts_monotonic().evaluate(
        {"bars": bars, "interval_sec": 900},
    )
    assert r.passed


def test_bar_open_ts_monotonic_missing_interval_in_ctx():
    """Defensive: a context that forgot ``interval_sec`` should
    fail loud, not silent-pass."""
    bars = [_bar("ITC.NS", date(2026, 4, 1), 9, 15)]
    r = bar_open_ts_monotonic().evaluate({"bars": bars})
    assert not r.passed


# ────────────────────────────────────────────────────────────────
# ohlc_self_consistent
# ────────────────────────────────────────────────────────────────


def test_ohlc_self_consistent_passes_clean():
    bars = [_bar("ITC.NS", date(2026, 4, 1), 9, 15)]
    r = ohlc_self_consistent().evaluate({"bars": bars})
    assert r.passed


def test_ohlc_self_consistent_fails_when_high_below_low():
    bars = [_bar("ITC.NS", date(2026, 4, 1), 9, 15, o=100, h=90, lo=99, c=100)]
    r = ohlc_self_consistent().evaluate({"bars": bars})
    assert not r.passed
    assert r.severity == "error"


def test_ohlc_self_consistent_fails_when_close_above_high():
    bars = [_bar("ITC.NS", date(2026, 4, 1), 9, 15, o=99, h=100, lo=98, c=101)]
    r = ohlc_self_consistent().evaluate({"bars": bars})
    assert not r.passed


def test_ohlc_self_consistent_fails_when_open_below_low():
    bars = [_bar("ITC.NS", date(2026, 4, 1), 9, 15, o=95, h=100, lo=98, c=99)]
    r = ohlc_self_consistent().evaluate({"bars": bars})
    assert not r.passed


# ────────────────────────────────────────────────────────────────
# cross_source_close_match
# ────────────────────────────────────────────────────────────────


def test_cross_source_close_match_skips_when_no_ltp_map():
    """No LTP context → assertion passes silently (ad-hoc CLI
    backfills don't carry LTPs)."""
    bars = [_bar("ITC.NS", date(2026, 4, 1), 15, 15, c=300)]
    r = cross_source_close_match().evaluate({"bars": bars})
    assert r.passed


def test_cross_source_close_match_passes_within_tolerance():
    bars = [_bar("ITC.NS", date(2026, 4, 1), 15, 15, c=300.0)]
    r = cross_source_close_match().evaluate(
        {"bars": bars, "kite_ltp": {"ITC.NS": 300.5}},
    )
    # 0.5 / 300 = 0.167% < default 0.5%
    assert r.passed


def test_cross_source_close_match_fails_beyond_tolerance():
    bars = [_bar("ITC.NS", date(2026, 4, 1), 15, 15, c=300.0)]
    r = cross_source_close_match().evaluate(
        {"bars": bars, "kite_ltp": {"ITC.NS": 305.0}},
    )
    # 5 / 305 = 1.64% > 0.5%
    assert not r.passed
    assert r.severity == "warn"
    assert r.detail["breach_count"] == 1


def test_cross_source_close_match_picks_latest_bar():
    """The assertion compares the LTP against the LATEST bar
    close per ticker, not the first."""
    bars = [
        _bar("ITC.NS", date(2026, 4, 1), 9, 15, c=290.0),  # early
        _bar("ITC.NS", date(2026, 4, 1), 15, 15, c=300.0),  # latest
    ]
    # If the assertion accidentally compared LTP=300.5 against
    # the early bar (close=290) it would breach at ~3.6%; against
    # the latest bar (close=300) it's within 0.17%.
    r = cross_source_close_match().evaluate(
        {"bars": bars, "kite_ltp": {"ITC.NS": 300.5}},
    )
    assert r.passed


# ────────────────────────────────────────────────────────────────
# build_assertions + run_post_ingest_assertions
# ────────────────────────────────────────────────────────────────


def test_build_assertions_returns_all_five():
    a = build_assertions(900)
    names = [x.name for x in a]
    assert any("bar-count-floor" in n for n in names)
    assert any("ohlc-not-nan" in n for n in names)
    assert any("bar-open-ts-monotonic" in n for n in names)
    assert any("ohlc-self-consistent" in n for n in names)
    assert any("cross-source-close-match" in n for n in names)
    assert len(a) == 5


def test_run_post_ingest_emits_violation_events():
    """Inject a self-inconsistency to trip ohlc_self_consistent;
    the events_sink should receive exactly one
    ``data_quality_violation`` event."""
    bars = [
        _bar(
            "ITC.NS", date(2026, 4, 1), 9, 15, o=100, h=90, lo=99, c=100
        ),  # high < low
    ]
    # Pad with valid bars to clear the bar_count_floor floor.
    bars.extend(_bar("ITC.NS", date(2026, 4, 1), 9, 30 + i) for i in range(24))
    sink: list[dict] = []
    report = run_post_ingest_assertions(
        bars=bars,
        interval_sec=900,
        events_sink=sink.append,
    )
    assert report.status == "error"
    violations = [e for e in sink if e["type"] == "data_quality_violation"]
    failed_names = {r.name for r in report.failed}
    assert "intraday-ohlc-self-consistent" in failed_names
    assert len(violations) == len(report.failed)


def test_run_post_ingest_clean_batch_emits_no_events():
    bars = [
        _bar("ITC.NS", date(2026, 4, 1), 9, 15 + i)
        for i in range(25)  # clears 15m floor
    ]
    sink: list[dict] = []
    report = run_post_ingest_assertions(
        bars=bars,
        interval_sec=900,
        events_sink=sink.append,
    )
    assert report.status == "ok"
    assert sink == []

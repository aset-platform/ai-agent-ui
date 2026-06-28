"""Two-clock backtest engine integration test.

A *daily*-signal strategy with trailing enabled and 15m coverage
must evaluate exits on 15m execution bars while signals still fire
once per trading day. The execution clock collapses to the signal
clock when there's no intraday coverage (byte-identical to the
pure-daily path).

Patch targets are module-level references in ``runner``:
  - ``runner.load_ohlcv_window``        — daily signal bars
  - ``runner.intraday_coverage``        — finest grain probe (Task 1)
  - ``runner.load_intraday_bars_window``— 15m execution bars (Task 1/3)
  - ``runner.flush_events``             — no Iceberg writes in test
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from backend.algo.backtest.coverage import TickerCoverage
from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.strategy.ast import parse_strategy

_BASE = date(2026, 1, 1)


def _daily_bars(closes):
    out = []
    for i, c in enumerate(closes):
        c = Decimal(str(c))
        out.append(BarData(
            ticker="FAKE.NS", date=_BASE + timedelta(days=i),
            open=Decimal("100"), high=max(Decimal("100"), c) + 1,
            low=min(Decimal("100"), c) - 1, close=c, volume=10_000,
        ))
    return {"FAKE.NS": out}


def _ns(d, hh, mm):
    # IST clock time → UTC instant (IST = UTC+5:30).
    ist = datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)
    dt = ist - timedelta(hours=5, minutes=30)
    return int(dt.timestamp() * 1_000_000_000)


def _intraday_15m_for_day(d, lows):
    # one bar per 15m slot; inject a deep LOW mid-day to trip the
    # trailing stop intraday even though the daily close never does.
    bars = []
    for k, lo in enumerate(lows):
        hh, mm = 9 + (k * 15) // 60, (15 + k * 15) % 60
        ts = _ns(d, hh, mm)
        bars.append(BarData(
            ticker="FAKE.NS", date=d,
            open=Decimal("100"), high=Decimal("101"),
            low=Decimal(str(lo)), close=Decimal("100"),
            volume=500, bar_open_ts_ns=ts,
        ))
    return bars


def _v5_daily_strategy():
    # reuse the trailing-enabled daily strategy shape.
    from backend.algo.backtest.tests.test_trailing_stop_integration import (
        _v5_strategy,
    )
    return _v5_strategy()


def test_daily_strategy_exits_intraday_on_15m():
    # On a later day price never *closes* below the stop, but a single
    # 15m bar dips below it -> the two-clock engine must catch the
    # intraday stop-hit and book an exit with an intraday timestamp.
    daily = _daily_bars([100] * 25)
    cov = {"FAKE.NS": TickerCoverage(
        "FAKE.NS", 900, _BASE, _BASE + timedelta(days=24), 25,
    )}
    exec_bars = {"FAKE.NS": []}
    for i in range(25):
        d = _BASE + timedelta(days=i)
        lows = [99] * 25
        if i == 22:
            lows[10] = 90  # deep dip mid-day
        exec_bars["FAKE.NS"].extend(_intraday_15m_for_day(d, lows))

    strategy = parse_strategy(_v5_daily_strategy())
    req = BacktestRequest(
        strategy_id=strategy.id, period_start=_BASE + timedelta(days=20),
        period_end=_BASE + timedelta(days=24),
    )
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=daily,
    ), patch(
        "backend.algo.backtest.runner.intraday_coverage", return_value=cov,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_bars_window",
        return_value=exec_bars,
    ), patch("backend.algo.backtest.runner.flush_events"):
        summary = run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=["FAKE.NS"],
        )
    reasons = {t.exit_reason for t in summary.trade_list}
    assert reasons & {"phase1_stop", "phase1_ratchet", "trail_stop"}, (
        f"expected an intraday trailing exit, got {reasons}"
    )
    # exit fill carries an intraday timestamp (not a pure daily exit).
    assert any(
        t.closed_at_ts_ns is not None for t in summary.trade_list
    )


def _v5_daily_strategy_with_time_stop(max_holding_days: int):
    s = _v5_daily_strategy()
    s["risk"]["per_trade"]["max_holding_days"] = max_holding_days
    return s


def test_time_stop_fills_in_two_clock_mode():
    # Regression for Fix 1: in two-clock mode the time-stop exit was
    # built with an exec-bar ``ts_ns`` but executed via the DAILY
    # ``sim`` (whose ts_ns index is empty) -> ``execute()`` returned
    # None and the exit silently never filled. After the fix it must
    # route through ``exec_sim_broker`` and FILL.
    #
    # Prices are flat at 100 (intraday lows 99) so the trailing stop
    # never arms; the ONLY exit available is the time-stop.
    daily = _daily_bars([100] * 25)
    cov = {"FAKE.NS": TickerCoverage(
        "FAKE.NS", 900, _BASE, _BASE + timedelta(days=24), 25,
    )}
    exec_bars = {"FAKE.NS": []}
    for i in range(25):
        d = _BASE + timedelta(days=i)
        exec_bars["FAKE.NS"].extend(
            _intraday_15m_for_day(d, [99] * 25)
        )

    strategy = parse_strategy(
        _v5_daily_strategy_with_time_stop(max_holding_days=2)
    )
    req = BacktestRequest(
        strategy_id=strategy.id,
        period_start=_BASE + timedelta(days=20),
        period_end=_BASE + timedelta(days=24),
    )
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=daily,
    ), patch(
        "backend.algo.backtest.runner.intraday_coverage",
        return_value=cov,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_bars_window",
        return_value=exec_bars,
    ), patch("backend.algo.backtest.runner.flush_events"):
        summary = run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=["FAKE.NS"],
        )
    reasons = [t.exit_reason for t in summary.trade_list]
    assert "time_stop" in reasons, (
        f"time-stop must FILL in two-clock mode, got {reasons}"
    )
    # The time-stop exit filled on an execution bar -> intraday ts.
    assert any(
        t.exit_reason == "time_stop" and t.closed_at_ts_ns is not None
        for t in summary.trade_list
    )


def _daily_bars_for(ticker, closes):
    out = []
    for i, c in enumerate(closes):
        c = Decimal(str(c))
        out.append(BarData(
            ticker=ticker, date=_BASE + timedelta(days=i),
            open=Decimal("100"), high=max(Decimal("100"), c) + 1,
            low=min(Decimal("100"), c) - 1, close=c, volume=10_000,
        ))
    return out


def _intraday_15m_for(ticker, d, lows):
    bars = []
    for k, lo in enumerate(lows):
        hh, mm = 9 + (k * 15) // 60, (15 + k * 15) % 60
        ts = _ns(d, hh, mm)
        bars.append(BarData(
            ticker=ticker, date=d,
            open=Decimal("100"), high=Decimal("101"),
            low=Decimal(str(lo)), close=Decimal("100"),
            volume=500, bar_open_ts_ns=ts,
        ))
    return bars


def test_mixed_universe_covered_and_uncovered_both_exit_on_stop():
    # Regression for Fix 2: a universe with one exec-covered ticker
    # (15m bars) and one UNCOVERED ticker (no 15m bars) under
    # two-clock. Both must have their trailing stops evaluated and
    # BOTH must exit on a trailing stop — the uncovered ticker via
    # the legacy daily ``_trailing_managers`` path (evaluated on
    # signal bars against the daily bars), NOT held to period end.
    #
    # COVERED.NS: flat daily 100; one mid-day 15m low of 90 trips the
    #   stop intraday.
    # UNCOVERED.NS: a daily bar whose LOW gaps to 90 trips the
    #   phase1 stop on the daily signal clock.
    covered = "COVERED.NS"
    uncov = "UNCOVERED.NS"
    daily = {
        covered: _daily_bars_for(covered, [100] * 25),
        uncov: _daily_bars_for(uncov, [100] * 25),
    }
    # Force the uncovered ticker's daily LOW below the stop on day 23
    # (period). _daily_bars_for sets low = min(100, close) - 1 = 99;
    # override day 23 to a deep low so the daily trailing stop fires.
    daily[uncov][23] = BarData(
        ticker=uncov, date=_BASE + timedelta(days=23),
        open=Decimal("100"), high=Decimal("101"),
        low=Decimal("90"), close=Decimal("100"), volume=10_000,
    )

    cov = {covered: TickerCoverage(
        covered, 900, _BASE, _BASE + timedelta(days=24), 25,
    )}
    # exec_bars only contains the covered ticker -> uncovered is in
    # daily_fallback_tickers.
    exec_bars = {covered: []}
    for i in range(25):
        d = _BASE + timedelta(days=i)
        lows = [99] * 25
        if i == 22:
            lows[10] = 90  # intraday dip trips covered ticker's stop
        exec_bars[covered].extend(_intraday_15m_for(covered, d, lows))

    strategy = parse_strategy(_v5_daily_strategy())
    req = BacktestRequest(
        strategy_id=strategy.id,
        period_start=_BASE + timedelta(days=20),
        period_end=_BASE + timedelta(days=24),
    )
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=daily,
    ), patch(
        "backend.algo.backtest.runner.intraday_coverage",
        return_value=cov,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_bars_window",
        return_value=exec_bars,
    ), patch("backend.algo.backtest.runner.flush_events"):
        summary = run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(),
            universe=[covered, uncov],
        )
    trail_reasons = {"phase1_stop", "phase1_ratchet", "trail_stop"}
    per_ticker = {}
    for t in summary.trade_list:
        per_ticker.setdefault(t.ticker, set()).add(t.exit_reason)
    # Covered ticker exits on a trailing stop (intraday).
    assert per_ticker.get(covered, set()) & trail_reasons, (
        f"covered ticker must exit on a stop, got {per_ticker}"
    )
    # Uncovered ticker also exits on a trailing stop (daily fallback)
    # — NOT only a synthetic period_end_mtm.
    assert per_ticker.get(uncov, set()) & trail_reasons, (
        f"uncovered ticker must exit on a daily stop (not held to "
        f"period end), got {per_ticker}"
    )

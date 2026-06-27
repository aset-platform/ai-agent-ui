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

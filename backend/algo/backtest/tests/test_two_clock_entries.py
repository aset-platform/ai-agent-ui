"""PRE-3 Task 1 — intraday FEATURE load at the exec grain.

A *daily*-signal strategy running in two-clock mode must load
per-exec-bar features (``load_intraday_features_window``) for
the exec-covered tickers so a later task (Task 2) can evaluate
entries against them. Plain-daily (non-two-clock) runs must NOT
call this loader at all — the two-clock setup block is the only
new call site.

Patch targets are module-level references in ``runner`` (mirrors
``test_two_clock_runner.py``):
  - ``runner.load_ohlcv_window``            — daily signal bars
  - ``runner.intraday_coverage``            — finest grain probe
  - ``runner.load_intraday_bars_window``    — 15m execution bars
  - ``runner.load_intraday_features_window``— 15m exec features
  - ``runner.flush_events``                 — no Iceberg writes
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


def _ns(d, hh, mm):
    # IST clock time → UTC instant (IST = UTC+5:30).
    ist = datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)
    dt = ist - timedelta(hours=5, minutes=30)
    return int(dt.timestamp() * 1_000_000_000)


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


def _v5_daily_strategy():
    # reuse the trailing-enabled daily strategy shape.
    from backend.algo.backtest.tests.test_trailing_stop_integration import (
        _v5_strategy,
    )
    return _v5_strategy()


def _two_clock_fixture(ticker="FAKE.NS", num_days=25):
    daily = {ticker: _daily_bars_for(ticker, [100] * num_days)}
    cov = {ticker: TickerCoverage(
        ticker, 900, _BASE, _BASE + timedelta(days=num_days - 1),
        num_days,
    )}
    exec_bars = {ticker: []}
    for i in range(num_days):
        d = _BASE + timedelta(days=i)
        exec_bars[ticker].extend(
            _intraday_15m_for(ticker, d, [99] * num_days)
        )
    return daily, cov, exec_bars


def test_two_clock_run_loads_intraday_features_for_exec_covered():
    ticker = "FAKE.NS"
    daily, cov, exec_bars = _two_clock_fixture(ticker)
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
    ), patch(
        "backend.algo.backtest.runner.load_intraday_features_window",
        return_value={},
    ) as mock_features, patch(
        "backend.algo.backtest.runner.flush_events",
    ):
        run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=[ticker],
        )
    assert mock_features.called, (
        "two-clock daily-signal run must load intraday features "
        "at the exec grain"
    )
    _, kwargs = mock_features.call_args
    assert kwargs["interval_sec"] == 900
    assert set(kwargs["tickers"]) == {ticker}
    assert kwargs["period_start"] == req.period_start
    assert kwargs["period_end"] == req.period_end


def test_two_clock_features_skip_daily_fallback_ticker():
    # A ticker with NO 15m coverage is not in ``exec_covered`` and
    # must not be passed to the features loader.
    covered = "COVERED.NS"
    uncov = "UNCOVERED.NS"
    daily_c, cov_c, exec_bars_c = _two_clock_fixture(covered)
    daily = {
        **daily_c,
        uncov: _daily_bars_for(uncov, [100] * 25),
    }
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
        return_value=cov_c,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_bars_window",
        return_value=exec_bars_c,
    ), patch(
        "backend.algo.backtest.runner.load_intraday_features_window",
        return_value={},
    ) as mock_features, patch(
        "backend.algo.backtest.runner.flush_events",
    ):
        run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=[covered, uncov],
        )
    assert mock_features.called
    _, kwargs = mock_features.call_args
    assert set(kwargs["tickers"]) == {covered}


def test_plain_daily_run_never_loads_intraday_features():
    # No two-clock trigger (trailing disabled / no coverage probed
    # here since ``intraday_coverage`` isn't even reached) — a
    # plain daily run must not touch the features loader at all.
    ticker = "FAKE.NS"
    daily = {ticker: _daily_bars_for(ticker, [100] * 25)}
    strategy_dict = _v5_daily_strategy()
    # Disable trailing so the two-clock probe never fires — this
    # collapses to the pure-daily path exactly as today.
    strategy_dict["risk"]["per_trade"]["trailing_trigger_pct"] = None
    strategy_dict["risk"]["per_trade"]["trailing_atr_multiplier"] = None
    strategy = parse_strategy(strategy_dict)
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
    ) as mock_cov, patch(
        "backend.algo.backtest.runner.load_intraday_bars_window",
    ) as mock_bars, patch(
        "backend.algo.backtest.runner.load_intraday_features_window",
    ) as mock_features, patch(
        "backend.algo.backtest.runner.flush_events",
    ):
        run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=[ticker],
        )
    assert not mock_cov.called, "plain-daily must skip the probe"
    assert not mock_bars.called
    assert not mock_features.called, (
        "plain-daily (non-two-clock) run must not load intraday "
        "features"
    )

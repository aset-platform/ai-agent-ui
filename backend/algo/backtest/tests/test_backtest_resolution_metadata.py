"""Task 6 — BacktestSummary resolution metadata.

Tests that a completed run is tagged with the actual execution
grain and the tickers that fell back to daily resolution.
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.strategy.ast import parse_strategy
from backend.algo.backtest.tests.test_trailing_stop_integration import (
    _v5_strategy,
)

_BASE = date(2026, 1, 1)


def _daily(n):
    return {"FAKE.NS": [BarData(
        ticker="FAKE.NS", date=_BASE + timedelta(days=i),
        open=Decimal("100"), high=Decimal("102"), low=Decimal("99"),
        close=Decimal("100"), volume=10_000) for i in range(n)]}


def test_daily_only_run_tags_86400():
    strategy = parse_strategy(_v5_strategy())
    req = BacktestRequest(
        strategy_id=strategy.id, period_start=_BASE + timedelta(days=20),
        period_end=_BASE + timedelta(days=24))
    # no intraday coverage -> stays daily
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=_daily(25),
    ), patch(
        "backend.algo.backtest.runner.intraday_coverage", return_value={},
    ), patch("backend.algo.backtest.runner.flush_events"):
        summary = run_backtest(
            strategy=strategy, request=req,
            user_id=uuid4(), universe=["FAKE.NS"])
    assert summary.execution_interval_sec == 86400
    assert summary.daily_fallback_tickers == ["FAKE.NS"]

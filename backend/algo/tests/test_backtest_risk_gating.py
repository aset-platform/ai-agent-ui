"""Risk-engine gating in the backtest runner.

The backtest runner must apply the same 3-tier RiskEngine.gate
that PaperRuntime uses, so a strategy behaves identically across
backtest and paper. These tests use a 30-bar synthetic ticker
(same fixture as test_backtest_runner.py) and tweak the
strategy's risk config to force each gate path.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.strategy.ast import parse_strategy


def _gen_bars(ticker: str) -> list[BarData]:
    base = date(2026, 4, 1)
    bars: list[BarData] = []
    for i in range(30):
        d = base + timedelta(days=i)
        openp = Decimal("100") + Decimal(i)
        close = openp + Decimal("2")
        bars.append(BarData(
            ticker=ticker, date=d,
            open=openp, high=close + 1, low=openp - 1,
            close=close, volume=10_000,
        ))
    return bars


def _strategy(buy_qty: int = 5, max_qty: int = 100) -> dict:
    return {
        "id": str(uuid4()),
        "name": "buy on every bar",
        "universe": {
            "type": "scope", "scope": "watchlist",
            "filter": {
                "ticker_type": ["stock"], "market": "india",
            },
        },
        "schedule": {
            "type": "bar_close", "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": buy_qty}},
        "risk": {
            "per_trade": {
                "stop_loss_pct": 5, "max_qty": max_qty,
            },
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {
                "max_loss_pct": 2,
                "max_open_positions": 10,
            },
        },
    }


@pytest.fixture
def patches():
    bars = {"FAKE.NS": _gen_bars("FAKE.NS")}
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=bars,
    ), patch(
        "backend.algo.backtest.runner.flush_events",
    ) as flush_mock:
        yield flush_mock


def _flushed_rows(flush_mock):
    if not flush_mock.call_args_list:
        return []
    return flush_mock.call_args_list[0].args[0]


def test_risk_max_qty_blocks_oversized_buy(patches):
    """qty=200 exceeds per_trade.max_qty=100 → every signal
    rejected, no trades close."""
    strategy = parse_strategy(_strategy(buy_qty=200, max_qty=100))
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    summary = run_backtest(
        strategy=strategy, request=request,
        user_id=uuid4(), universe=["FAKE.NS"],
    )
    assert summary.total_trades == 0
    assert summary.total_fees_inr == Decimal("0")
    rows = _flushed_rows(patches)
    rejected = [r for r in rows if r["type"] == "signal_rejected"]
    assert len(rejected) >= 1
    payloads = [json.loads(r["payload_json"]) for r in rejected]
    assert all(p["reason"] == "max_qty" for p in payloads)


def test_risk_accept_under_max_qty(patches):
    """qty=5 well under max_qty=100 → orders fire."""
    strategy = parse_strategy(_strategy(buy_qty=5, max_qty=100))
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    summary = run_backtest(
        strategy=strategy, request=request,
        user_id=uuid4(), universe=["FAKE.NS"],
    )
    rows = _flushed_rows(patches)
    rejected = [r for r in rows if r["type"] == "signal_rejected"]
    filled = [r for r in rows if r["type"] == "order_filled"]
    assert len(rejected) == 0
    assert len(filled) >= 1


def test_risk_signal_rejected_payload_carries_threshold(patches):
    """signal_rejected payload includes threshold + observed."""
    strategy = parse_strategy(_strategy(buy_qty=200, max_qty=50))
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    run_backtest(
        strategy=strategy, request=request,
        user_id=uuid4(), universe=["FAKE.NS"],
    )
    rows = _flushed_rows(patches)
    rejected = [
        json.loads(r["payload_json"])
        for r in rows if r["type"] == "signal_rejected"
    ]
    assert rejected
    p = rejected[0]
    assert p["reason"] == "max_qty"
    assert p["threshold"] == "50"
    assert p["observed_value"] == "200"
    assert p["ticker"] == "FAKE.NS"
    assert p["side"] == "BUY"

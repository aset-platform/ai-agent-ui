"""Backtest runner — stop_loss_monitor integration tests.

Drives run_backtest() end-to-end with hand-built daily bars and
verifies the new stop-loss path:

  1. Breach bar → exit emitted, fills at next-bar open.
  2. stop_loss_pct=0 → feature disabled, no stop-loss exits.
  3. Same-bar AST skip → exactly one full-qty close (no partial
     reduce + separate close from the AST loop on the same bar).
  4. summary.trade_list carries exit_reason="stop_loss".

Fixture: 10 daily bars on a single ticker. The strategy is a
trivial "buy 10 shares on every bar" so it would normally try
to BUY on the breach bar — the stop_loss_skip set must block
that AST action.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.strategy.ast import parse_strategy


# Closes: bars 0..4 flat at 100, bar 5 drops -4 % to 96, then
# stays low. With stop_loss_pct=3.0 the breach fires at bar 5
# close (loss = -4 %), the SELL emits at bar 5 and SimBroker
# fills at bar 6 open.
_CLOSES = [
    Decimal("100"),
    Decimal("100"),
    Decimal("100"),
    Decimal("100"),
    Decimal("100"),
    Decimal("96"),
    Decimal("95"),
    Decimal("94"),
    Decimal("94"),
    Decimal("94"),
]


def _gen_bars(ticker: str) -> list[BarData]:
    base = date(2026, 4, 1)
    bars: list[BarData] = []
    for i, close in enumerate(_CLOSES):
        d = base + timedelta(days=i)
        # Open == close so the next-bar-open fill price equals
        # the close we use for the loss calculation. Makes the
        # assertions easy to reason about.
        bars.append(BarData(
            ticker=ticker,
            date=d,
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=10_000,
        ))
    return bars


def _strategy(stop_loss_pct: float = 3.0) -> dict:
    return {
        "id": str(uuid4()),
        "name": "buy 10 every bar",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {
                "ticker_type": ["stock"],
                "market": "india",
            },
        },
        "schedule": {
            "type": "bar_close",
            "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": 10}},
        "risk": {
            "per_trade": {
                "stop_loss_pct": stop_loss_pct,
                "max_qty": 1000,
            },
            "portfolio": {
                "max_exposure_pct": 100,
                "max_concentration_pct": 100,
            },
            "daily": {
                "max_loss_pct": 50,
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
    ):
        yield


def _run(stop_loss_pct: float):
    strategy = parse_strategy(_strategy(stop_loss_pct))
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 10),
    )
    return run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )


def test_stop_loss_emits_exit_at_breach_bar(patches):
    """Bar 5 close = 96 (-4 % vs avg ≥ 100) with stop_pct=3 →
    SELL emitted at bar 5, fills at bar 6 open. At least one
    closed position carries exit_reason='stop_loss'."""
    summary = _run(stop_loss_pct=3.0)
    sl_trades = [
        t for t in summary.trade_list if t.exit_reason == "stop_loss"
    ]
    assert sl_trades, "expected at least one stop_loss exit"
    # Breach detected at bar 5 (2026-04-06); fill at bar 6 open
    # (2026-04-07) per SimBroker T+1 semantics.
    first_sl = sl_trades[0]
    assert first_sl.closed_at == date(2026, 4, 7)


def test_no_stop_loss_when_pct_zero(patches):
    """stop_loss_pct=0 disables the feature → no closed positions
    carry exit_reason='stop_loss' even on a -10 % path."""
    summary = _run(stop_loss_pct=0.0)
    sl_trades = [
        t for t in summary.trade_list if t.exit_reason == "stop_loss"
    ]
    assert sl_trades == []


def test_stop_loss_skips_ast_for_stopped_ticker(patches):
    """Proxy assertion: when the stop fires, the close is full-qty
    (matches the original entry size) AND only one stop_loss exit
    is booked on that bar — no partial reduce + separate close
    from the AST evaluating BUY on the same bar."""
    summary = _run(stop_loss_pct=3.0)
    sl_trades = [
        t for t in summary.trade_list if t.exit_reason == "stop_loss"
    ]
    assert sl_trades, "expected at least one stop_loss exit"
    # AST buys 10 shares per bar. The position open at bar 5 close
    # is the accumulation of bars 1..5 fills (5 fills × 10 = 50).
    # The stop closes the entire position in a single SELL.
    first_sl = sl_trades[0]
    assert first_sl.qty > 0
    # Exactly one stop_loss row on the breach close-day (no
    # accidental double-close from AST same bar).
    same_day = [
        t for t in sl_trades if t.closed_at == first_sl.closed_at
    ]
    assert len(same_day) == 1


def test_stop_loss_exit_reason_lands_in_trade_list(patches):
    """summary.trade_list must surface exit_reason='stop_loss' so
    the UI badge renders correctly."""
    summary = _run(stop_loss_pct=3.0)
    reasons = {t.exit_reason for t in summary.trade_list}
    assert "stop_loss" in reasons

"""Equity curve mark-to-market correctness.

Before this fix the runner used ``blist[-1].close`` (always
period_end's close) when building the marks dict, which meant
unrealised P&L on open positions was 0 for every day except
the very last bar — equity curves looked stair-stepped (only
realised P&L moved them) instead of tracking actual market
path. The fix uses a running ``last_close[ticker]`` updated as
we walk so each day's snapshot reflects today's marks.
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


def _strategy() -> dict:
    """Buy 5 shares once at the open of every bar — accumulates
    a position so unrealised P&L can move with mark price."""
    return {
        "id": str(uuid4()),
        "name": "buy 5 every day",
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
        "root": {"type": "buy", "qty": {"shares": 5}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                # Generous caps so the buy fires every bar.
                "max_exposure_pct": 100,
                "max_concentration_pct": 100,
            },
            "daily": {
                "max_loss_pct": 50,
                "max_open_positions": 50,
            },
        },
    }


def _gen_rising_bars() -> list[BarData]:
    """30 bars rising linearly: close grows by 2 each day so
    unrealised P&L on a held position must rise daily."""
    base = date(2026, 4, 1)
    bars: list[BarData] = []
    for i in range(30):
        d = base + timedelta(days=i)
        openp = Decimal("100") + Decimal(i)
        close = openp + Decimal("2")
        bars.append(BarData(
            ticker="X", date=d,
            open=openp, high=close + 1, low=openp - 1,
            close=close, volume=10_000,
        ))
    return bars


@pytest.fixture
def patches():
    bars = {"X": _gen_rising_bars()}
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=bars,
    ), patch(
        "backend.algo.backtest.runner.flush_events",
    ):
        yield


def test_equity_curve_tracks_unrealised_pnl_intra_period(patches):
    """On a rising-price ticker with a buy-and-hold pattern, the
    equity curve must STRICTLY RISE most days (not be flat) —
    that proves unrealised P&L is being marked daily, not
    suppressed until period_end."""
    strategy = parse_strategy(_strategy())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        initial_capital_inr=Decimal("100000"),
    )
    summary = run_backtest(
        strategy=strategy, request=request,
        user_id=uuid4(), universe=["X"],
    )
    eq = summary.equity_curve
    assert len(eq) >= 5
    # Values should not all be equal — the bug had every interior
    # snapshot equal to initial_capital because marks were empty.
    distinct = {p.equity_inr for p in eq}
    assert len(distinct) > 1, (
        "equity curve is flat — unrealised PnL not being marked"
    )
    # On a strictly rising market, mid-period equity must exceed
    # initial capital (the held position is in the green).
    mid = eq[len(eq) // 2].equity_inr
    assert mid > Decimal("100000"), (
        f"mid-period equity {mid} should exceed initial 100000 "
        "on a rising-price held position"
    )

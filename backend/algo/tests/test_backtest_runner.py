"""End-to-end backtest on a 30-bar synthetic ticker.

Covers the full pipeline: data_source ↔ evaluator ↔ sim_broker
↔ position_tracker ↔ event_writer. Mocks the OHLCV fetch and
the Iceberg flush so the test runs in-process without DuckDB
or PyIceberg roundtrips.
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


def _gen_bars(ticker: str) -> list[BarData]:
    base = date(2026, 4, 1)
    bars: list[BarData] = []
    for i in range(30):
        d = base + timedelta(days=i)
        # Trending up: open == prev close + 1.
        openp = Decimal("100") + Decimal(i)
        close = openp + Decimal("2")
        bars.append(BarData(
            ticker=ticker, date=d,
            open=openp, high=close + 1, low=openp - 1,
            close=close, volume=10_000,
        ))
    return bars


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "Buy on day 1, hold, sell on day 25",
        "universe": {
            "type": "scope", "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d", "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        # Always buy 5 shares — runner has no entry guards in v1
        # so the position keeps stacking on each bar; that's fine
        # for this end-to-end smoke (we only assert "ran without
        # errors and produced a summary").
        "root": {"type": "buy", "qty": {"shares": 5}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
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


def test_runner_produces_summary(patches):
    strategy = parse_strategy(_strategy_payload())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        initial_capital_inr=Decimal("100000.00"),
    )
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    assert summary.run_id is not None
    assert summary.total_trades >= 0
    assert summary.fee_rates_version  # stamped at least once on a fill
    # Every bar buys 5 shares; with 30 bars and BUY-only strategy,
    # accumulated qty > 0.
    assert summary.total_fees_inr > Decimal("0")


def test_runner_flushes_events(patches):
    strategy = parse_strategy(_strategy_payload())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    # flush_events called exactly once at end
    patches.assert_called_once()
    rows = patches.call_args.args[0]
    assert any(r["type"] == "backtest_run_started" for r in rows)
    assert any(r["type"] == "backtest_run_completed" for r in rows)


def test_runner_handles_empty_universe(patches):
    # Override the patch to return empty bars for all tickers.
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value={},
    ), patch(
        "backend.algo.backtest.runner.flush_events",
    ):
        strategy = parse_strategy(_strategy_payload())
        request = BacktestRequest(
            strategy_id=strategy.id,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
        )
        summary = run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=[],
        )
        assert summary.total_trades == 0
        assert summary.total_pnl_inr == Decimal("0")


def test_runner_strategy_with_hold_root_zero_trades(patches):
    payload = _strategy_payload()
    payload["root"] = {"type": "hold"}
    strategy = parse_strategy(payload)
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    assert summary.total_trades == 0
    assert summary.total_fees_inr == Decimal("0")

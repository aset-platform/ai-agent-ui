"""FE-5 hook test: backtest runner writes one
``stocks.trade_feature_snapshots`` row per executed fill.

We patch the snapshot writer at the import path the runner
sees (lazy ``from backend.algo.features.snapshots import …``
inside the hook). Asserts the call shape: ``mode='backtest'``,
fill metadata + features dict at decision time.
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
    for i in range(10):
        d = base + timedelta(days=i)
        openp = Decimal("100") + Decimal(i)
        close = openp + Decimal("2")
        bars.append(
            BarData(
                ticker=ticker,
                date=d,
                open=openp,
                high=close + 1,
                low=openp - 1,
                close=close,
                volume=10_000,
            )
        )
    return bars


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "Buy 1",
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
        "rebalance": {
            "type": "daily",
            "max_positions": 1,
        },
        "root": {"type": "buy", "qty": {"shares": 1}},
        "risk": {
            "per_trade": {
                "stop_loss_pct": 5,
                "max_qty": 100,
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
def patched_world():
    bars = {"FAKE.NS": _gen_bars("FAKE.NS")}
    with (
        patch(
            "backend.algo.backtest.runner.load_ohlcv_window",
            return_value=bars,
        ),
        patch(
            "backend.algo.backtest.runner.flush_events",
        ),
        patch(
            "backend.algo.features.snapshots." "write_trade_feature_snapshot",
        ) as snap_mock,
    ):
        yield snap_mock


def test_backtest_runner_calls_snapshot_per_fill(
    patched_world,
) -> None:
    """One fill per bar → one snapshot per fill, all with
    ``mode='backtest'`` + the ticker we configured."""
    strategy = parse_strategy(_strategy_payload())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 10),
        initial_capital_inr=Decimal("100000.00"),
    )
    run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )

    assert patched_world.call_count >= 1
    # All snapshots carry mode='backtest' + the right ticker.
    for call in patched_world.call_args_list:
        kwargs = call.kwargs
        assert kwargs["mode"] == "backtest"
        assert kwargs["ticker"] == "FAKE.NS"
        assert kwargs["side"] == "BUY"
        assert kwargs["qty"] == 1
        # features dict was passed (non-None) — decision-time
        # context that downstream alpha research needs.
        assert kwargs["features"] is not None
        # Required identity stamps for the alpha-research join.
        assert kwargs["run_id"]
        assert kwargs["strategy_id"]
        assert kwargs["fill_id"]


def test_snapshot_failure_does_not_break_backtest(
    patched_world,
) -> None:
    """The hook's try/except contract: a writer raise MUST
    NOT propagate out of the runner."""
    patched_world.side_effect = RuntimeError("simulated iceberg outage")
    strategy = parse_strategy(_strategy_payload())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 10),
        initial_capital_inr=Decimal("100000.00"),
    )
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    assert summary.run_id is not None

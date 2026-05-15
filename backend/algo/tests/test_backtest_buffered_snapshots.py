"""FE-5.1 — Backtest runtime integration test for the
in-process snapshot buffer (ASETPLTFRM-417).

Validates the end-to-end contract:

* Backtest with N fills produces exactly ONE Iceberg commit at
  ``run_backtest()``'s ``finally`` block (NOT N commits).
* Walk-forward folds each flush their own batch via the same
  ``finally`` path — no fold-level wiring needed.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.features.snapshots_buffer import reset_buffer
from backend.algo.strategy.ast import parse_strategy


def _gen_bars(ticker: str, n: int = 10) -> list[BarData]:
    base = date(2026, 4, 1)
    bars: list[BarData] = []
    for i in range(n):
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
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close",
            "interval": "1d",
            "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "buy", "qty": {"shares": 1}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
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


@pytest.fixture(autouse=True)
def _reset():
    reset_buffer()
    yield
    reset_buffer()


def test_backtest_with_many_fills_writes_1_iceberg_commit():
    """End-to-end: a multi-fill backtest produces exactly ONE
    Iceberg commit (the FE-5.1 design goal — replaces FE-5's
    per-fill commits)."""
    bars = {"FAKE.NS": _gen_bars("FAKE.NS", n=20)}
    strategy = parse_strategy(_strategy_payload())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 20),
        initial_capital_inr=Decimal("100000.00"),
    )
    with (
        patch(
            "backend.algo.backtest.runner.load_ohlcv_window",
            return_value=bars,
        ),
        patch(
            "backend.algo.backtest.runner.flush_events",
        ),
        patch(
            "backend.algo.features.snapshots."
            "write_trade_feature_snapshots_batch",
            side_effect=lambda rows: len(rows),
        ) as batch_writer,
    ):
        run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=["FAKE.NS"],
        )

    # Exactly ONE batch commit (regardless of how many fills).
    assert batch_writer.call_count == 1
    rows_written = batch_writer.call_args.args[0]
    # At least one fill happened on this universe / strategy.
    assert len(rows_written) >= 1
    # Every row in the batch is mode='backtest'.
    for r in rows_written:
        assert r.mode == "backtest"
        assert r.ticker == "FAKE.NS"


def test_walk_forward_each_fold_flushes_own_batch():
    """Each fold's child ``run_backtest()`` runs its own
    ``finally`` flush — 4 folds -> 4 batch writes (NOT 4 *
    rows-per-fold per-row writes)."""
    bars = {"FAKE.NS": _gen_bars("FAKE.NS", n=10)}
    strategy = parse_strategy(_strategy_payload())

    with (
        patch(
            "backend.algo.backtest.runner.load_ohlcv_window",
            return_value=bars,
        ),
        patch(
            "backend.algo.backtest.runner.flush_events",
        ),
        patch(
            "backend.algo.features.snapshots."
            "write_trade_feature_snapshots_batch",
            side_effect=lambda rows: len(rows),
        ) as batch_writer,
    ):
        # Simulate 4 walk-forward folds — each is a separate
        # run_backtest() call with a distinct (strategy.id,
        # session_id) buffer key.
        for _ in range(4):
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

    # 4 folds -> 4 distinct batch writes.
    assert batch_writer.call_count == 4


def test_backtest_buffer_flush_failure_does_not_break_run():
    """If the buffer flush throws, run_backtest still returns a
    valid summary (the FE-5 contract: snapshot loss never
    blocks the run)."""
    bars = {"FAKE.NS": _gen_bars("FAKE.NS")}
    strategy = parse_strategy(_strategy_payload())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 10),
        initial_capital_inr=Decimal("100000.00"),
    )
    with (
        patch(
            "backend.algo.backtest.runner.load_ohlcv_window",
            return_value=bars,
        ),
        patch(
            "backend.algo.backtest.runner.flush_events",
        ),
        patch(
            "backend.algo.features.snapshots."
            "write_trade_feature_snapshots_batch",
            side_effect=RuntimeError("simulated iceberg outage"),
        ),
    ):
        summary = run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=["FAKE.NS"],
        )
    # Run completes successfully.
    assert summary.run_id is not None
    assert summary.status == "completed"

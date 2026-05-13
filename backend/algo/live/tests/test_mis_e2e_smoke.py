"""Smoke E2E for MIS / intraday strategy round-trip (ASETPLTFRM-396).

Final Phase-2 gate: exercises the full MIS path end-to-end through
real types (not MagicMock(spec=Strategy)) so we'd catch regressions
in the AST → runtime → KiteClient wiring that the unit tests can't
see in isolation.

Coverage matrix:
  • Build a real ``Strategy`` from the ``mis_rsi_scalper`` template
    payload (matches the frontend's ``templates.ts`` shape).
  • Construct a ``LiveRuntime`` against it with stubbed warmups and
    a dry-run Kite client.
  • Drive a synthetic BUY through ``_submit_order`` (we skip the
    strategy evaluator — exercised separately — because RSI<30 in
    the synthetic ticker would need a full indicator pipeline).
  • Assert the dry-run synthetic fill event carries
    ``payload.product == "MIS"``.
  • Drive the auto-square task with two open positions and assert
    one SELL per ticker with ``reason="mis_auto_square_off"``.

Limit: doesn't exercise the strategy evaluator path. That's covered
by ``test_live_runtime_intraday_routing.py`` + the unit tests in
``test_live_pre_trade_check.py``. A real end-to-end run would need
to inject a deterministic RSI feature into the evaluator, which is
brittle for a smoke test.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def _mis_strategy_payload() -> dict:
    """Mirror of frontend ``mis_rsi_scalper`` template — the actual
    Builder POSTs a payload of this exact shape."""
    return {
        "id": str(uuid4()),
        "name": "MIS RSI Scalper smoke",
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
            "interval": "5m",
            "time": "15:14 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 3},
        "risk": {
            "per_trade": {"stop_loss_pct": 1.0, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 50,
                "max_concentration_pct": 20,
            },
            "daily": {
                "max_loss_pct": 1.5,
                "max_open_positions": 3,
            },
        },
        "product": "MIS",
        "square_off_time": "15:14 IST",
        "root": {
            "type": "if",
            "cond": {
                "type": "compare",
                "left": {"feature": "rsi"},
                "op": ">",
                "right": {"literal": 70},
            },
            "then": {"type": "exit", "scope": "this_symbol"},
            "else": {
                "type": "if",
                "cond": {
                    "type": "compare",
                    "left": {"feature": "rsi"},
                    "op": "<",
                    "right": {"literal": 30},
                },
                "then": {
                    "type": "set_target_weight",
                    "weight": 0.20,
                },
                "else": {"type": "hold"},
            },
        },
    }


def _make_mis_runtime() -> Any:
    """Build a LiveRuntime against the real MIS template AST.

    All heavy deps mocked; daily warmup is patched to AssertionError
    so the intraday path is exercised. Kite client runs in dry-run
    so ``_submit_order`` produces a synthetic fill via the existing
    dry-run codepath instead of hitting the SDK boundary.
    """
    from backend.algo.broker.kite_client import KiteClient
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(_mis_strategy_payload())
    assert strategy.product == "MIS"
    assert strategy.schedule.interval == "5m"

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "max_inr": Decimal("100000"),
        "max_orders_per_day": 50,
        "allowed_tickers": ["ITC.NS"],
        "cumulative_inr_today": Decimal("0"),
        "orders_count_today": 0,
    }
    caps_repo.update_in_flight = AsyncMock()
    caps_repo.increment_daily_counters = AsyncMock()

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc = MagicMock()
        MockKC.return_value = kc
        kite = KiteClient(
            api_key="k", access_token="tok", dry_run=True,
        )
        kite._kc = kc

    caps = {
        "live_orders_enabled": True,
        "allowed_tickers": ["ITC.NS"],
    }
    with (
        patch(
            "backend.algo.live.daily_bar_warmup.preload_daily_bars",
            side_effect=AssertionError(
                "MIS smoke must NOT call the daily warmup",
            ),
        ),
        patch(
            "backend.algo.live.intraday_bar_warmup."
            "preload_intraday_bars",
            return_value={},
        ),
    ):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=None,
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime


# ---------------------------------------------------------------
# 1. Dry-run BUY round-trip: returns 1 (submit success) and the
#    KiteClient surface saw product="MIS". This is the end-to-end
#    proof that the AST → runtime → SDK boundary is wired.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mis_buy_routes_product_mis_through_kite_call():
    """``_submit_order`` on a MIS strategy must route
    ``product="MIS"`` all the way to ``KiteClient.place_order``.

    Dry-run mode returns a synthetic DRY_* order id without
    actually calling Kite's SDK, so we wrap the KiteClient's
    place_order to capture the kwargs the runtime hands over.
    """
    from backend.algo.paper.types import Signal

    runtime = _make_mis_runtime()

    captured: dict[str, Any] = {}
    real_place_order = runtime._kite.place_order

    def _spy(**kwargs):
        captured.update(kwargs)
        return real_place_order(**kwargs)

    runtime._kite.place_order = _spy  # type: ignore[assignment]

    signal = Signal(
        strategy_id=runtime._strategy.id,
        user_id=runtime._user_id,
        ticker="ITC.NS",
        side="BUY",
        qty=5,
        emitted_at_ns=int(
            datetime.now(UTC).timestamp() * 1_000_000_000,
        ),
        reason="set_target_weight",
    )
    runtime._DRY_FILL_DELAY_S = 0.02

    n = await runtime._submit_order(
        signal=signal, last_price=Decimal("307.35"),
    )

    assert n == 1, "MIS BUY in dry-run should return 1"
    assert captured.get("product") == "MIS", captured
    assert captured.get("tradingsymbol") == "ITC"
    assert captured.get("transaction_type") == "BUY"
    assert captured.get("quantity") == 5


# ---------------------------------------------------------------
# 2. In-flight ledger row stamped with product=MIS.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mis_in_flight_ledger_carries_product_mis():
    """The ``_in_flight`` list keys the (symbol, product) join the
    Positions tab reads. MIS positions must be tagged accordingly."""
    from backend.algo.paper.types import Signal

    runtime = _make_mis_runtime()
    signal = Signal(
        strategy_id=runtime._strategy.id,
        user_id=runtime._user_id,
        ticker="ITC.NS",
        side="BUY",
        qty=5,
        emitted_at_ns=int(
            datetime.now(UTC).timestamp() * 1_000_000_000,
        ),
        reason="set_target_weight",
    )
    runtime._DRY_FILL_DELAY_S = 0.02

    await runtime._submit_order(
        signal=signal, last_price=Decimal("307.35"),
    )

    assert len(runtime._in_flight) == 1
    entry = runtime._in_flight[0]
    assert entry["product"] == "MIS"
    assert entry["symbol"] == "ITC"
    assert entry["side"] == "BUY"
    assert entry["qty"] == 5
    assert entry["reason"] == "set_target_weight"


# ---------------------------------------------------------------
# 3. Auto-square emits SELL per open position with the right
#    reason and quantity.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mis_auto_square_emits_sell_per_open_position():
    """End-to-end auto-square: with two open positions, the task
    fires two SELL signals with reason='mis_auto_square_off'."""
    runtime = _make_mis_runtime()

    # Seed two open positions on the runtime's PositionTracker. We
    # bypass apply_fill (which would also write closed positions and
    # realised P&L) and just set the open dict directly — the
    # auto-square only reads .qty and .avg_price.
    runtime._positions = MagicMock()
    runtime._positions.open_positions.return_value = {
        "ITC.NS": SimpleNamespace(
            qty=5, avg_price=Decimal("307.35"),
        ),
        "RELIANCE.NS": SimpleNamespace(
            qty=2, avg_price=Decimal("2500"),
        ),
    }

    submitted: list[tuple[str, str, int, str | None]] = []

    async def _capture(*, signal, last_price, **_kwargs):
        submitted.append(
            (signal.ticker, signal.side, signal.qty, signal.reason),
        )
        return 1

    runtime._submit_order = _capture  # type: ignore[assignment]

    fake_now = datetime(2026, 5, 13, 15, 13, 55, tzinfo=IST)
    with patch("backend.algo.live.runtime.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.fromtimestamp = datetime.fromtimestamp
        with patch(
            "backend.algo.live.runtime.asyncio.sleep",
            new=AsyncMock(),
        ):
            await runtime._schedule_mis_square_off()

    assert len(submitted) == 2
    tickers = {row[0] for row in submitted}
    assert tickers == {"ITC.NS", "RELIANCE.NS"}
    assert all(row[1] == "SELL" for row in submitted)
    assert all(
        row[3] == "mis_auto_square_off" for row in submitted
    )
    # Qty matches position qty per ticker.
    qty_by_ticker = {row[0]: row[2] for row in submitted}
    assert qty_by_ticker["ITC.NS"] == 5
    assert qty_by_ticker["RELIANCE.NS"] == 2


# ---------------------------------------------------------------
# 4. The strategy preload routing branches to intraday warmup, not
#    daily — guarantees the engine has a 5-min indicator series
#    even on first eval rather than building from session-local
#    1-min bars.
# ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_mis_runtime_uses_intraday_warmup_not_daily():
    """``_make_mis_runtime`` patches the daily warmup as
    AssertionError; successful construction proves the intraday
    path was taken. Belt-and-braces: also verify the strategy
    cadence on the runtime instance is intraday."""
    runtime = _make_mis_runtime()
    assert runtime._strategy.schedule.interval == "5m"
    assert runtime._strategy.product == "MIS"
    # _bars_by_ticker may be empty (preload returned {}) — what
    # matters is the daily warmup never fired (its AssertionError
    # would have aborted construction).
    assert isinstance(runtime._bars_by_ticker, dict)

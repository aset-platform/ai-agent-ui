"""Live runtime stop-loss integration.

Drives ``LiveRuntime._on_bar_close`` directly with a synthetic
:class:`Bar` so the monitor-→-Kite-place_order path can be asserted
in isolation. Unlike backtest / paper, live must submit the SELL
IMMEDIATELY at trigger time via ``KiteClient.place_order`` (no
next-bar-open delay) and tag the in-flight entry with
``reason="stop_loss"`` so the eventual Kite postback writes the
exit_reason onto the ``order_filled_live`` event payload.

The Kite client is mocked end-to-end; no SDK calls leak out.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# Heavy backend deps (pyarrow + PEP 604 union syntax) only resolve
# inside the Docker backend container — gate the suite identically
# to ``test_live_dry_run.py``.
_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason=(
        "Requires pyarrow + Python ≥3.10 "
        "(run inside Docker backend container)"
    ),
)


def _strategy_payload(
    *,
    stop_loss_pct: int = 5,
    buy_qty: int = 5,
) -> dict:
    return {
        "id": str(uuid4()),
        "name": "live stop-loss test strategy",
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
        # Unconditional BUY so AST eval, if it ran, would emit a
        # competing BUY on the trigger bar — that lets us assert
        # the stop-loss path short-circuits AST eval.
        "root": {"type": "buy", "qty": {"shares": buy_qty}},
        "risk": {
            "per_trade": {
                "stop_loss_pct": stop_loss_pct,
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


def _make_bar(
    *,
    ticker: str = "FAKE.NS",
    close: float,
    ts_ns: int = 1_000_000_000,
):
    from backend.algo.stream.types import Bar

    return Bar(
        ticker=ticker,
        interval_sec=60,
        bar_open_ts_ns=ts_ns,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1,
        written_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
    )


def _make_runtime(
    *,
    stop_loss_pct: int = 5,
    buy_qty: int = 5,
    allowed_ticker: str = "FAKE.NS",
):
    """Construct a LiveRuntime with all I/O deps mocked."""
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(
        _strategy_payload(
            stop_loss_pct=stop_loss_pct,
            buy_qty=buy_qty,
        ),
    )

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "max_inr": Decimal("10000000"),
        "max_orders_per_day": 100,
        "allowed_tickers": [allowed_ticker],
        "cumulative_inr_today": Decimal("0"),
        "orders_count_today": 0,
    }
    caps_repo.update_in_flight = AsyncMock()
    caps_repo.increment_daily_counters = AsyncMock()

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    # Mock Kite: place_order returns a real-shaped kite_order_id.
    # dry_run=False so we exercise the production submission path
    # (KiteClient.place_order short-circuits to DRY_ when truthy).
    kite = MagicMock()
    kite.dry_run = False
    kite.place_order = MagicMock(return_value="KITE_ORDER_42")

    caps = {
        "live_orders_enabled": True,
        "allowed_tickers": [],
    }

    # Patch position_hydration so the constructor's Kite probe is a
    # no-op (we seed positions manually in each test).
    with patch(
        "backend.algo.live.position_hydration.hydrate",
        return_value=[],
    ):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=date(2026, 4, 1),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime, kite


def _seed_open_position(
    runtime,
    *,
    ticker: str,
    qty: int,
    avg_price: Decimal,
) -> None:
    """Drop a synthetic BUY Fill straight into the runtime's
    PositionTracker so subsequent bars see an existing open position
    without having to replay a full tick stream."""
    from backend.algo.backtest.types import Fill

    fill = Fill(
        intent_id=uuid4(),
        ticker=ticker,
        side="BUY",
        qty=qty,
        fill_price=avg_price,
        fill_date=date(2026, 5, 1),
        fees_inr=Decimal("0"),
        fee_rates_version="test",
    )
    runtime._positions.apply_fill(fill)


def _seed_bar_history(runtime, *, ticker: str, close: float) -> None:
    """Pre-seed _bars_by_ticker so _on_bar_close skips the warmup."""
    from backend.algo.backtest.types import BarData as _BackBar

    runtime._bars_by_ticker[ticker] = [
        _BackBar(
            ticker=ticker,
            date=date(2026, 5, 1),
            open=Decimal(str(close)),
            high=Decimal(str(close)),
            low=Decimal(str(close)),
            close=Decimal(str(close)),
            volume=1,
            bar_open_ts_ns=0,
        ),
    ]


@pytest.fixture(autouse=True)
def _force_eval_gate_open(monkeypatch):
    """Bypass the daily-cadence eval-time gate (default 14:30 IST).
    Tests must trigger the stop-loss path regardless of wall-clock
    time; patch the module-level cutoff to ``00:00``."""
    import datetime as _dt

    from backend.algo.live import runtime as _runtime_mod

    monkeypatch.setattr(
        _runtime_mod,
        "_MIN_EVAL_TIME_IST",
        _dt.time(0, 0),
    )


@pytest.mark.asyncio
async def test_live_stop_loss_calls_kite_place_order():
    """Open position breaches stop_loss_pct=5 → _on_bar_close
    submits a SELL via KiteClient.place_order on the same bar."""
    runtime, kite = _make_runtime(stop_loss_pct=5, buy_qty=5)
    _seed_open_position(
        runtime,
        ticker="FAKE.NS",
        qty=10,
        avg_price=Decimal("100"),
    )
    _seed_bar_history(runtime, ticker="FAKE.NS", close=94.0)

    bar = _make_bar(close=94.0)  # -6% — trips the 5% stop.
    result = await runtime._on_bar_close(
        bar=bar,
        last_price=Decimal("94"),
    )

    # Kite must have received exactly one SELL order — for the full
    # open qty, tagged with our strategy tag prefix.
    assert kite.place_order.call_count == 1
    kwargs = kite.place_order.call_args.kwargs
    assert kwargs["transaction_type"] == "SELL"
    assert kwargs["quantity"] == 10
    assert kwargs["tradingsymbol"] == "FAKE"
    # LIMIT (not MARKET) — matches the existing live SELL path
    # since last_price=94 was supplied. Kite v2 rejects naked
    # MARKET orders without market_protection (see comment in
    # _submit_order); the stop-loss path MUST mirror that.
    assert kwargs["order_type"] == "LIMIT"
    # Same-bar AST skip: result is 1 (one fill submitted) without
    # the AST-loop's signal_generated row having been emitted.
    assert result == 1
    assert not any(
        r["type"] == "signal_generated" for r in runtime._events
    )


@pytest.mark.asyncio
async def test_live_stop_loss_propagates_exit_reason():
    """exit_reason='stop_loss' must flow to the in-flight entry so
    the Kite postback handler writes it onto the order_filled_live
    event payload (spec §4.5)."""
    runtime, kite = _make_runtime(stop_loss_pct=5, buy_qty=5)
    _seed_open_position(
        runtime,
        ticker="FAKE.NS",
        qty=10,
        avg_price=Decimal("100"),
    )
    _seed_bar_history(runtime, ticker="FAKE.NS", close=92.0)

    bar = _make_bar(close=92.0)  # -8% — trips the 5% stop.
    await runtime._on_bar_close(
        bar=bar,
        last_price=Decimal("92"),
    )

    # KiteClient.place_order itself doesn't accept an exit_reason
    # kwarg (v2 doesn't surface it). The attribution flow stamps it
    # onto in_flight_entry["reason"] which the postback handler
    # later forwards to the order_filled_live event payload.
    assert len(runtime._in_flight) == 1
    assert runtime._in_flight[0]["reason"] == "stop_loss"
    assert runtime._in_flight[0]["side"] == "SELL"
    assert runtime._in_flight[0]["qty"] == 10


@pytest.mark.asyncio
async def test_live_stop_loss_skips_ast_for_stopped_ticker():
    """AST eval MUST NOT run on the trigger bar for a ticker that
    just stopped out — the strategy root is unconditional BUY, so
    no BUY order may reach Kite alongside the stop-loss SELL."""
    runtime, kite = _make_runtime(stop_loss_pct=5, buy_qty=5)
    _seed_open_position(
        runtime,
        ticker="FAKE.NS",
        qty=10,
        avg_price=Decimal("100"),
    )
    _seed_bar_history(runtime, ticker="FAKE.NS", close=90.0)

    bar = _make_bar(close=90.0)  # -10% — trips the 5% stop.
    await runtime._on_bar_close(
        bar=bar,
        last_price=Decimal("90"),
    )

    # Exactly one Kite order — the stop-loss SELL. No competing
    # BUY from the AST root.
    assert kite.place_order.call_count == 1
    assert (
        kite.place_order.call_args.kwargs["transaction_type"]
        == "SELL"
    )
    # No signal_generated event row either — that would mean the
    # AST loop executed for this bar.
    signal_rows = [
        r for r in runtime._events if r["type"] == "signal_generated"
    ]
    assert signal_rows == []


@pytest.mark.asyncio
async def test_live_stop_loss_no_trigger_when_loss_below_threshold():
    """Sanity check: -3% loss with threshold=5 must NOT enter the
    stop-loss path — no SELL Kite call, no stop_loss entry in the
    in-flight ledger. Whatever the AST chooses to do on this bar
    is out of scope; we only assert the stop-loss branch did NOT
    fire."""
    runtime, kite = _make_runtime(stop_loss_pct=5, buy_qty=5)
    _seed_open_position(
        runtime,
        ticker="FAKE.NS",
        qty=10,
        avg_price=Decimal("100"),
    )
    _seed_bar_history(runtime, ticker="FAKE.NS", close=97.0)

    bar = _make_bar(close=97.0)  # -3% — below the 5% stop.
    await runtime._on_bar_close(
        bar=bar,
        last_price=Decimal("97"),
    )

    sell_calls = [
        c for c in kite.place_order.call_args_list
        if c.kwargs.get("transaction_type") == "SELL"
    ]
    assert sell_calls == []
    in_flight_reasons = [e["reason"] for e in runtime._in_flight]
    assert "stop_loss" not in in_flight_reasons


@pytest.mark.asyncio
async def test_live_stop_loss_propagates_submit_order_return_value():
    """When _submit_order returns 0 (e.g. LTP staleness or place_order
    failure), _on_bar_close also returns 0 — preserves the per-bar
    fill counter semantics. Without this, an SL submission failure
    would be silently counted as 1 successful fill."""
    runtime, _kite = _make_runtime(stop_loss_pct=5, buy_qty=5)
    _seed_open_position(
        runtime,
        ticker="FAKE.NS",
        qty=10,
        avg_price=Decimal("100"),
    )
    _seed_bar_history(runtime, ticker="FAKE.NS", close=94.0)

    # Patch _submit_order to simulate a failure (returns 0).
    runtime._submit_order = AsyncMock(return_value=0)

    bar = _make_bar(close=94.0)  # -6% — trips the 5% stop.
    result = await runtime._on_bar_close(
        bar=bar,
        last_price=Decimal("94"),
    )

    # SL path was entered (one _submit_order call) but it returned 0,
    # so _on_bar_close must propagate 0 — not the previous hard-coded 1.
    assert runtime._submit_order.await_count == 1
    assert result == 0

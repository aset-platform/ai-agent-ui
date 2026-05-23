"""Paper runtime stop-loss integration.

Drives ``PaperRuntime._on_bar_close`` directly with synthetic Bar
objects so the monitor-→-SELL emission path can be asserted in
isolation. Full end-to-end coverage (replay tick fixture → fills)
lives in ``test_paper_runtime.py``; these tests target the
stop-loss branch added to ``_on_bar_close`` per Task 4 of the
stop-loss enforcement plan.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from backend.algo.backtest.types import Fill
from backend.algo.paper.broker import PaperBroker
from backend.algo.paper.runtime import PaperRuntime
from backend.algo.paper.types import Signal
from backend.algo.stream.types import Bar
from backend.algo.strategy.ast import parse_strategy


def _strategy_payload(
    *,
    stop_loss_pct: int = 5,
    buy_qty: int = 5,
) -> dict:
    return {
        "id": str(uuid4()),
        "name": "stop-loss test strategy",
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
        # Plain buy so the strategy would normally re-enter on every
        # bar — that lets us assert the stop-loss branch short-circuits
        # AST evaluation on the trigger bar.
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
    ts_ns: int = 0,
) -> Bar:
    """Synthetic 1m bar. OHLC collapsed to the close for simplicity
    — the runtime only reads ``close`` for the stop-loss check."""
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


def _seed_open_position(
    runtime: PaperRuntime,
    *,
    ticker: str,
    qty: int,
    avg_price: Decimal,
) -> None:
    """Drop a synthetic BUY Fill straight into the runtime's
    PositionTracker so subsequent bars see an existing open
    position without having to replay a full tick stream."""
    fill = Fill(
        intent_id=uuid4(),
        ticker=ticker,
        side="BUY",
        qty=qty,
        fill_price=avg_price,
        fill_date=datetime(2026, 5, 1).date(),
        fees_inr=Decimal("0"),
        fee_rates_version="test",
    )
    runtime._positions.apply_fill(fill)


def test_paper_stop_loss_emits_sell_at_threshold_breach():
    """Open position breaches stop_loss_pct=5 → _on_bar_close emits
    a SELL fill tagged exit_reason='stop_loss' and the AST's
    standing BUY action is SKIPPED for the same bar."""
    strategy = parse_strategy(
        _strategy_payload(stop_loss_pct=5, buy_qty=5),
    )
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=datetime(2026, 4, 1).date(),
    )
    _seed_open_position(
        runtime,
        ticker="FAKE.NS",
        qty=10,
        avg_price=Decimal("100"),
    )
    # Close at 94 → loss = -6% which trips stop_loss_pct=5.
    bar = _make_bar(close=94.0)
    fills = runtime._on_bar_close(
        bar=bar, last_price=Decimal("94"),
    )
    assert fills == 1, "stop-loss SELL should count as one fill"
    # Position must be closed via stop_loss exit_reason.
    closed = runtime._positions.closed_positions()
    assert len(closed) == 1
    assert closed[0].ticker == "FAKE.NS"
    assert closed[0].exit_reason == "stop_loss"
    # The order_filled event payload must carry the exit_reason tag
    # so the Trade-list UI badge + outcome filter both see it.
    filled_events = [
        json.loads(r["payload_json"])
        for r in runtime._events
        if r["type"] == "order_filled"
    ]
    assert len(filled_events) == 1
    assert filled_events[0]["side"] == "SELL"
    assert filled_events[0]["exit_reason"] == "stop_loss"
    # AST short-circuit: no signal_generated row should have been
    # emitted on this bar (the strategy root is unconditional BUY).
    assert not any(
        r["type"] == "signal_generated" for r in runtime._events
    )


def test_paper_stop_loss_disabled_when_pct_zero():
    """stop_loss_pct=0 disables the monitor — even a -50% bar must
    NOT produce a stop-loss SELL; existing AST flow proceeds."""
    strategy = parse_strategy(
        _strategy_payload(stop_loss_pct=0, buy_qty=5),
    )
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=datetime(2026, 4, 1).date(),
    )
    _seed_open_position(
        runtime,
        ticker="FAKE.NS",
        qty=10,
        avg_price=Decimal("100"),
    )
    # Catastrophic 50% drop — would trip any nonzero threshold.
    bar = _make_bar(close=50.0)
    runtime._on_bar_close(bar=bar, last_price=Decimal("50"))
    # No stop-loss path taken → no Position should be closed with
    # exit_reason='stop_loss'.
    closed = runtime._positions.closed_positions()
    sl_closes = [c for c in closed if c.exit_reason == "stop_loss"]
    assert sl_closes == []
    # Likewise, no order_filled payload should carry the tag.
    sl_filled = [
        json.loads(r["payload_json"])
        for r in runtime._events
        if r["type"] == "order_filled"
        and json.loads(r["payload_json"]).get("exit_reason")
        == "stop_loss"
    ]
    assert sl_filled == []


def test_paper_stop_loss_no_trigger_when_above_threshold():
    """Loss of -3% with threshold=5 must NOT trigger; AST eval
    proceeds normally and emits its standing BUY signal."""
    strategy = parse_strategy(
        _strategy_payload(stop_loss_pct=5, buy_qty=5),
    )
    runtime = PaperRuntime(
        strategy=strategy,
        user_id=uuid4(),
        initial_capital_inr=Decimal("100000"),
        fee_as_of=datetime(2026, 4, 1).date(),
    )
    _seed_open_position(
        runtime,
        ticker="FAKE.NS",
        qty=10,
        avg_price=Decimal("100"),
    )
    bar = _make_bar(close=97.0)  # -3% — below the 5% stop.
    runtime._on_bar_close(bar=bar, last_price=Decimal("97"))
    closed = runtime._positions.closed_positions()
    sl_closes = [c for c in closed if c.exit_reason == "stop_loss"]
    assert sl_closes == []
    # Strategy root is unconditional BUY → AST eval must have run
    # and produced at least one signal_generated row.
    signals = [
        r for r in runtime._events if r["type"] == "signal_generated"
    ]
    assert len(signals) >= 1


def test_paper_broker_forwards_signal_reason_to_fill():
    """PaperBroker.execute() carries Signal.reason into the returned
    Fill.exit_reason — closes the workaround gap so the runtime no
    longer needs to model_copy the stamp."""
    broker = PaperBroker(fee_as_of=datetime(2026, 4, 1).date())
    signal = Signal(
        strategy_id=uuid4(),
        user_id=uuid4(),
        ticker="FAKE.NS",
        side="SELL",
        qty=10,
        emitted_at_ns=0,
        reason="stop_loss",
    )
    fill = broker.execute(
        signal=signal,
        last_price=Decimal("100"),
        fill_date=datetime(2026, 1, 2).date(),
    )
    assert fill.exit_reason == "stop_loss"


def test_paper_broker_defaults_to_signal_when_reason_is_none():
    """Signal.reason=None → Fill.exit_reason='signal' (default)."""
    broker = PaperBroker(fee_as_of=datetime(2026, 4, 1).date())
    signal = Signal(
        strategy_id=uuid4(),
        user_id=uuid4(),
        ticker="FAKE.NS",
        side="BUY",
        qty=5,
        emitted_at_ns=0,
    )
    fill = broker.execute(
        signal=signal,
        last_price=Decimal("100"),
        fill_date=datetime(2026, 1, 2).date(),
    )
    assert fill.exit_reason == "signal"

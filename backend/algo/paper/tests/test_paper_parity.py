"""Task 6.1 + 6.2 + 6.3: paper-runtime parity tests.

Task 6.1 — mark-to-market equity + unrealised P&L parity:
  Paper runtime must produce the same equity/sizing behaviour as the
  live runtime:

  1. ``_account_snapshot`` includes open-position market value in
     ``current_equity_inr`` and populates ``daily_unrealised_pnl_inr``
     using ``_last_marks``.

  2. A ticker with no mark in ``_last_marks`` contributes 0 to
     unrealised P&L (safe skip, no crash).

  3. ``_size_via_composer`` passes ``cash = nav - deployed_cost`` into
     ``SizingContext``, not bare ``nav``.

Task 6.2 — directional slippage in PaperBroker.execute():
  ALGO_PAPER_SLIPPAGE_BPS env var controls fill-price adjustment:
  - BUY fills above last_price (buyer pays more).
  - SELL fills below last_price (seller receives less).
  - bps=0 is a no-op (regression guard).
  - Fee base remains last_price regardless of slippage.

Task 6.3 — emit signal_rejected instead of silent qty=0 drop:
  Paper runtime must surface insufficient-capital drops as
  ``signal_rejected`` events (reason=insufficient_capital_qty_zero),
  mirroring what the live runtime emits via
  ``_maybe_emit_qty_zero_rejection``.

We bypass PaperRuntime.__init__ (which has DB/cache calls) via
``object.__new__`` and then seed only the attributes touched by the
methods under test.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest  # noqa: F401 (imported for future parametrize use)

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill
from backend.algo.paper.broker import PaperBroker
from backend.algo.paper.runtime import PaperRuntime
from backend.algo.paper.types import Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime(
    initial: Decimal = Decimal("100000"),
) -> PaperRuntime:
    """Build a PaperRuntime without calling __init__.

    Seeds only the attributes referenced by ``_account_snapshot``,
    ``_size_via_composer``, and ``_action_to_signal``.
    """
    rt = object.__new__(PaperRuntime)
    rt._user_id = uuid4()
    rt._initial = initial
    rt._positions = PositionTracker()
    rt._last_marks: dict[str, Decimal] = {}
    rt._kill_switch_active = False
    rt._factor_cache: dict[Any, Any] = {}
    # Task 6.3: required by _action_to_signal / _maybe_emit_qty_zero_rejection
    rt._session_id: UUID = uuid4()
    rt._strategy = SimpleNamespace(id=uuid4())
    rt._events: list[dict[str, Any]] = []
    return rt


def _buy_fill(ticker: str, qty: int, price: Decimal) -> Fill:
    return Fill(
        intent_id=uuid4(),
        ticker=ticker,
        side="BUY",
        qty=qty,
        fill_price=price,
        fill_date=date(2026, 6, 24),
        fees_inr=Decimal("0"),
        fee_rates_version="test",
    )


# ---------------------------------------------------------------------------
# Test 1: snapshot includes unrealised P&L when marks are present
# ---------------------------------------------------------------------------

def test_snapshot_includes_unrealised_pnl():
    """current_equity_inr = initial + realised + unrealised."""
    rt = _make_runtime(initial=Decimal("100000"))

    # Open a 10-share position at avg_price 200.
    rt._positions.apply_fill(_buy_fill("INFY.NS", 10, Decimal("200")))

    # Mark INFY.NS at 220 -> unrealised = (220-200)*10 = 200.
    rt._last_marks["INFY.NS"] = Decimal("220")

    snap = rt._account_snapshot()

    assert snap.daily_unrealised_pnl_inr == Decimal("200")
    assert snap.current_equity_inr == Decimal("100200")  # 100k + 0 + 200


# ---------------------------------------------------------------------------
# Test 2: ticker absent from _last_marks contributes 0 (no crash)
# ---------------------------------------------------------------------------

def test_snapshot_no_mark_contributes_zero():
    """Ticker with no entry in _last_marks is skipped -- unrealised=0."""
    rt = _make_runtime(initial=Decimal("50000"))

    rt._positions.apply_fill(_buy_fill("RELIANCE.NS", 5, Decimal("2000")))
    # No entry in _last_marks for RELIANCE.NS.

    snap = rt._account_snapshot()

    assert snap.daily_unrealised_pnl_inr == Decimal("0")
    assert snap.current_equity_inr == Decimal("50000")


# ---------------------------------------------------------------------------
# Test 3: _size_via_composer passes cash = nav - deployed_cost
# ---------------------------------------------------------------------------

def test_size_via_composer_passes_cash_minus_deployed():
    """SizingContext.cash must equal nav - deployed_cost, not nav."""
    rt = _make_runtime(initial=Decimal("100000"))

    # Open 10 shares at 500 -> deployed_cost = 5000.
    rt._positions.apply_fill(_buy_fill("TCS.NS", 10, Decimal("500")))
    rt._last_marks["TCS.NS"] = Decimal("510")

    captured: list[Any] = []

    def fake_compose_qty(qty_spec, ctx):  # noqa: ANN001
        captured.append(ctx)
        return 5

    with patch(
        "backend.algo.paper.runtime.compose_qty",
        side_effect=fake_compose_qty,
    ):
        rt._size_via_composer(
            qty_spec={"type": "fixed_qty", "qty": 5},
            ticker="TCS.NS",
            bar_date_ns=1_750_000_000_000_000_000,
            last_price=Decimal("510"),
        )

    assert len(captured) == 1, "compose_qty was not called"
    ctx = captured[0]

    # nav = initial + realised = 100000 + 0 = 100000
    # deployed_cost = 10 * 500 = 5000
    # cash = nav - deployed_cost = 95000
    assert ctx.nav == Decimal("100000")
    assert ctx.cash == Decimal("95000")


# ---------------------------------------------------------------------------
# Task 6.2: directional slippage in PaperBroker.execute()
# ---------------------------------------------------------------------------

def _make_signal(side: str) -> Signal:
    return Signal(
        strategy_id=uuid4(),
        user_id=uuid4(),
        ticker="INFY.NS",
        side=side,  # type: ignore[arg-type]
        qty=1,
        emitted_at_ns=0,
    )


def _make_broker() -> PaperBroker:
    return PaperBroker(fee_as_of=date(2026, 6, 24))


def test_buy_slippage_bps50(monkeypatch: pytest.MonkeyPatch) -> None:
    """BUY fill price = last_price * (1 + 50/10000) = 100.50."""
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "50")
    broker = _make_broker()
    fill = broker.execute(
        signal=_make_signal("BUY"),
        last_price=Decimal("100"),
        fill_date=date(2026, 6, 24),
    )
    assert fill.fill_price == Decimal("100.50")


def test_sell_slippage_bps50(monkeypatch: pytest.MonkeyPatch) -> None:
    """SELL fill price = last_price * (1 - 50/10000) = 99.50."""
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "50")
    broker = _make_broker()
    fill = broker.execute(
        signal=_make_signal("SELL"),
        last_price=Decimal("100"),
        fill_date=date(2026, 6, 24),
    )
    assert fill.fill_price == Decimal("99.50")


def test_zero_bps_no_slippage(monkeypatch: pytest.MonkeyPatch) -> None:
    """bps=0 (default) -> fill_price == last_price exactly."""
    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "0")
    broker = _make_broker()
    last_price = Decimal("250.75")
    fill = broker.execute(
        signal=_make_signal("BUY"),
        last_price=last_price,
        fill_date=date(2026, 6, 24),
    )
    assert fill.fill_price == last_price


def test_fees_use_last_price_not_slipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fee base is last_price regardless of slippage bps."""
    last_price = Decimal("100")

    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "0")
    broker_no_slip = _make_broker()
    fill_no_slip = broker_no_slip.execute(
        signal=_make_signal("BUY"),
        last_price=last_price,
        fill_date=date(2026, 6, 24),
    )

    monkeypatch.setenv("ALGO_PAPER_SLIPPAGE_BPS", "50")
    broker_slip = _make_broker()
    fill_slip = broker_slip.execute(
        signal=_make_signal("BUY"),
        last_price=last_price,
        fill_date=date(2026, 6, 24),
    )

    # fees_inr must be identical — fee base is last_price in both cases
    assert fill_no_slip.fees_inr == fill_slip.fees_inr


# ---------------------------------------------------------------------------
# Task 6.3: signal_rejected emitted on qty=0 drop (paper<->live parity)
# ---------------------------------------------------------------------------

# bar_date_ns chosen so date() == 2026-06-24 UTC (1_750_723_200_000_000_000 ns)
_BAR_DATE_NS = 1_750_723_200_000_000_000
_LAST_PRICE = Decimal("5000")
_TICKER = "RELIANCE.NS"


def _parse_event(evt: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return (type, payload_dict) from an event_row dict.

    event_row() stores type as 'type' and payload as 'payload_json'
    (a JSON string).  This helper decodes both for assertions.
    """
    import json

    return evt["type"], json.loads(evt["payload_json"])


def test_set_target_weight_qty_zero_emits_signal_rejected() -> None:
    """set_target_weight that rounds to qty=0 emits signal_rejected.

    equity=100_000 INR, weight=0.001, last_price=5000
    -> target_qty = int(100*0.001//1) == 0  (can't afford one share)
    -> _action_to_signal returns None AND appends signal_rejected event.
    """
    rt = _make_runtime(initial=Decimal("100000"))
    action = {"type": "set_target_weight", "weight": "0.001"}

    result = rt._action_to_signal(
        action,
        ticker=_TICKER,
        bar_date_ns=_BAR_DATE_NS,
        last_price=_LAST_PRICE,
    )

    assert result is None, "expected None (qty=0 drop)"
    assert len(rt._events) == 1, "expected exactly one signal_rejected event"
    evt = rt._events[0]
    etype, payload = _parse_event(evt)
    assert etype == "signal_rejected"
    assert payload["reason"] == "insufficient_capital_qty_zero"
    assert payload["ticker"] == _TICKER
    assert evt["mode"] == "paper"


def test_set_target_weight_qty_positive_no_rejection() -> None:
    """set_target_weight that sizes to qty>0 returns a Signal, no event.

    equity=100_000 INR, weight=0.5, last_price=5000
    -> target_qty = int(50_000//5000) = 10 -> BUY Signal.
    """
    rt = _make_runtime(initial=Decimal("100000"))
    action = {"type": "set_target_weight", "weight": "0.5"}

    result = rt._action_to_signal(
        action,
        ticker=_TICKER,
        bar_date_ns=_BAR_DATE_NS,
        last_price=_LAST_PRICE,
    )

    assert isinstance(result, Signal), "expected a BUY Signal"
    assert result.qty == 10
    assert len(rt._events) == 0, "no rejection event when qty>0"


def test_buy_via_composer_qty_zero_emits_signal_rejected() -> None:
    """buy via composer that sizes to 0 emits signal_rejected before None.

    Monkeypatches _size_via_composer to return 0.
    """
    rt = _make_runtime(initial=Decimal("100000"))
    action = {
        "type": "buy",
        "qty": {"vol_target_pct": 0.02},
    }

    with patch.object(rt, "_size_via_composer", return_value=0):
        result = rt._action_to_signal(
            action,
            ticker=_TICKER,
            bar_date_ns=_BAR_DATE_NS,
            last_price=_LAST_PRICE,
        )

    assert result is None, "expected None when composer returns 0"
    assert len(rt._events) == 1, "expected exactly one signal_rejected event"
    evt = rt._events[0]
    etype, payload = _parse_event(evt)
    assert etype == "signal_rejected"
    assert payload["reason"] == "insufficient_capital_qty_zero"
    assert evt["mode"] == "paper"


def test_signal_rejected_payload_keys() -> None:
    """signal_rejected payload has all documented keys; mode=='paper'."""
    rt = _make_runtime(initial=Decimal("100000"))
    action = {"type": "set_target_weight", "weight": "0.001"}

    rt._action_to_signal(
        action,
        ticker=_TICKER,
        bar_date_ns=_BAR_DATE_NS,
        last_price=_LAST_PRICE,
    )

    assert len(rt._events) == 1
    evt = rt._events[0]
    etype, payload = _parse_event(evt)

    assert evt["mode"] == "paper"
    assert etype == "signal_rejected"

    required_keys = {
        "reason",
        "ticker",
        "symbol",
        "side",
        "qty",
        "last_price",
        "current_equity_inr",
        "bar_date",
    }
    missing = required_keys - set(payload.keys())
    assert not missing, f"payload missing keys: {missing}"

    assert payload["reason"] == "insufficient_capital_qty_zero"
    assert payload["ticker"] == _TICKER
    assert payload["symbol"] == "RELIANCE"  # .NS stripped, uppercased
    assert payload["side"] == "BUY"
    assert payload["qty"] == 0
    assert payload["last_price"] == str(_LAST_PRICE)
    assert payload["bar_date"] == "2025-06-24"
    # no dry_run key in paper mode
    assert "dry_run" not in payload

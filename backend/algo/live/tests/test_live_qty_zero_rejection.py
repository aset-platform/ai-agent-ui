"""Observability — silent qty=0 entry drop surfaces as an event.

A daily strategy whose ``set_target_weight`` BUY intent sizes to
qty=0 (account cannot afford a single share at the bar price) is
otherwise a silent no-op: ``_action_to_signal`` returns ``None`` with
no signal and no event, so the events panel looks frozen even though
the entry conditions were met. ``LiveRuntime._maybe_emit_qty_zero_
rejection`` turns that into a ``signal_rejected`` event with reason
``insufficient_capital_qty_zero``.

Kite + all I/O deps are mocked; no SDK calls leak out.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# Same Docker-only gate as the sibling live suites (pyarrow + PEP 604).
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


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "qty-zero observability test strategy",
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
        "root": {
            "type": "if",
            "cond": {
                "type": "compare",
                "op": "<=",
                "left": {"feature": "rsi_2"},
                "right": {"literal": 5},
            },
            "then": {"type": "set_target_weight", "weight": 0.2},
            "else": {"type": "hold"},
        },
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


def _make_runtime(*, initial_capital_inr: Decimal):
    """LiveRuntime with all I/O deps mocked — ₹``initial_capital_inr``
    of equity, no open positions, no hydration probe."""
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(_strategy_payload())
    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "allowed_tickers": ["GRANULES.NS"],
    }
    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False
    kite = MagicMock()
    kite.dry_run = False

    with patch(
        "backend.algo.live.position_hydration.hydrate",
        return_value=[],
    ):
        return LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=initial_capital_inr,
            fee_as_of=date(2026, 4, 1),
            kite=kite,
            caps={"live_orders_enabled": True, "allowed_tickers": []},
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )


_BUY_ACTION = {"type": "set_target_weight", "weight": 0.2}


def test_qty_zero_emits_signal_rejected_event():
    """ERROR PATH: ₹3000 equity × 0.2 = ₹600 budget; a ₹741 share
    sizes to qty=0 → one ``signal_rejected`` event, reason
    ``insufficient_capital_qty_zero``."""
    runtime = _make_runtime(initial_capital_inr=Decimal("3000"))

    emitted = runtime._maybe_emit_qty_zero_rejection(
        action=_BUY_ACTION,
        ticker="GRANULES.NS",
        last_price=Decimal("741.40"),
        bar_date=date(2026, 6, 17),
    )

    assert emitted is True
    assert len(runtime._events) == 1
    row = runtime._events[0]
    assert row["type"] == "signal_rejected"
    payload = json.loads(row["payload_json"])
    assert payload["reason"] == "insufficient_capital_qty_zero"
    assert payload["ticker"] == "GRANULES.NS"
    assert payload["symbol"] == "GRANULES"
    assert payload["side"] == "BUY"
    assert payload["qty"] == 0


def test_affordable_target_does_not_emit():
    """HAPPY PATH: same ₹600 budget, a ₹100 share sizes to qty=6 (>0)
    so the entry routes normally — no rejection event."""
    runtime = _make_runtime(initial_capital_inr=Decimal("3000"))

    emitted = runtime._maybe_emit_qty_zero_rejection(
        action=_BUY_ACTION,
        ticker="SOUTHBANK.NS",
        last_price=Decimal("100"),
        bar_date=date(2026, 6, 17),
    )

    assert emitted is False
    assert runtime._events == []


def test_non_target_weight_action_is_ignored():
    """A plain ``buy`` action is sized elsewhere and must not trip the
    target-weight observability path."""
    runtime = _make_runtime(initial_capital_inr=Decimal("3000"))

    emitted = runtime._maybe_emit_qty_zero_rejection(
        action={"type": "buy", "qty": {"shares": 1}},
        ticker="GRANULES.NS",
        last_price=Decimal("741.40"),
        bar_date=date(2026, 6, 17),
    )

    assert emitted is False
    assert runtime._events == []

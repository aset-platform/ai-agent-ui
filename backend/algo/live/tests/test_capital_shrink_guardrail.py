"""Capital-shrink guardrail — prevent surprise sell-off on restart.

On 2026-06-25 a live runtime started with ₹20k while its real
positions had been built under ~₹100k. ``set_target_weight`` sizes
the target off ``self._initial``, so with the smaller capital every
held position became "overweight" and the strategy issued SELLs that
trimmed real shares.

This suite locks the two-part guardrail in ``LiveRuntime``:

  1. ``_detect_capital_below_deployed`` — at start, when configured
     capital < deployed cost-basis, set ``_capital_below_deployed``
     and emit a HIGH-severity ``capital_below_deployed`` event.
  2. ``_action_to_signal`` ``set_target_weight`` branch — while the
     flag is set, SUPPRESS rebalance-DOWN trims (diff<0) and emit a
     ``rebalance_down_suppressed_capital_shrink`` event, UNLESS env
     ``ALGO_ALLOW_REBALANCE_DOWN_ON_SHRINK`` is truthy. BUYs are
     still allowed. Protective exits never touch this branch.

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

# Same Docker-only gate as the sibling live suites.
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
        "name": "capital-shrink guardrail test strategy",
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
    """LiveRuntime with all I/O deps mocked, no hydration probe."""
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


class _FakePosition:
    """Minimal stand-in for PositionTracker's Position."""

    def __init__(self, qty: int, avg_price: Decimal):
        self.qty = qty
        self.avg_price = avg_price


def _set_open_positions(runtime, positions: dict) -> None:
    """Mock ``open_positions()`` on the position tracker."""
    runtime._positions.open_positions = MagicMock(  # type: ignore
        return_value=positions
    )


_TW_ACTION = {"type": "set_target_weight", "weight": 0.5}
_BAR_NS = 1_700_000_000_000_000_000


# --- Part 1: detection at start ------------------------------------


def test_detect_flags_when_capital_below_deployed():
    """#1: ₹20k start vs ₹100k deployed → flag True + HIGH event."""
    runtime = _make_runtime(initial_capital_inr=Decimal("20000"))
    _set_open_positions(
        runtime,
        {
            "GRANULES.NS": _FakePosition(100, Decimal("1000")),
        },
    )

    runtime._detect_capital_below_deployed()

    assert runtime._capital_below_deployed is True
    assert len(runtime._events) == 1
    row = runtime._events[0]
    assert row["type"] == "capital_below_deployed"
    payload = json.loads(row["payload_json"])
    assert payload["severity"] == "high"
    assert payload["initial"] == 20000.0
    assert payload["deployed_cost"] == 100000.0
    assert payload["ratio"] == pytest.approx(0.2)


def test_detect_no_flag_when_capital_above_deployed():
    """#2: ₹200k start vs ₹100k deployed → flag False, no event."""
    runtime = _make_runtime(initial_capital_inr=Decimal("200000"))
    _set_open_positions(
        runtime,
        {
            "GRANULES.NS": _FakePosition(100, Decimal("1000")),
        },
    )

    runtime._detect_capital_below_deployed()

    assert runtime._capital_below_deployed is False
    assert runtime._events == []


def test_flag_defaults_false_in_init():
    """The flag must exist and default False before detection runs."""
    runtime = _make_runtime(initial_capital_inr=Decimal("20000"))
    assert runtime._capital_below_deployed is False


# --- Part 2: suppression in set_target_weight ----------------------


def test_flagged_trim_down_is_suppressed():
    """#3: flagged + a target-weight resolving to a SELL (diff<0) →
    no signal + ``rebalance_down_suppressed_capital_shrink`` event."""
    runtime = _make_runtime(initial_capital_inr=Decimal("20000"))
    runtime._capital_below_deployed = True
    # equity ≈20k, weight 0.5 → target ₹10k @ ₹100 = 100 sh; hold
    # 200 sh → diff = -100 (trim-down SELL).
    _set_open_positions(
        runtime,
        {"GRANULES.NS": _FakePosition(200, Decimal("100"))},
    )

    sig = runtime._action_to_signal(
        _TW_ACTION,
        ticker="GRANULES.NS",
        bar_date_ns=_BAR_NS,
        last_price=Decimal("100"),
    )

    assert sig is None
    types = [e["type"] for e in runtime._events]
    assert "rebalance_down_suppressed_capital_shrink" in types
    row = next(
        e
        for e in runtime._events
        if e["type"] == "rebalance_down_suppressed_capital_shrink"
    )
    payload = json.loads(row["payload_json"])
    assert payload["ticker"] == "GRANULES.NS"
    assert payload["current_qty"] == 200
    assert payload["target_qty"] == 100


def test_flagged_buy_up_is_allowed():
    """#4: flagged + a target-weight BUY (diff>0) → signal produced."""
    runtime = _make_runtime(initial_capital_inr=Decimal("20000"))
    runtime._capital_below_deployed = True
    # target ₹10k @ ₹100 = 100 sh; hold 10 sh → diff = +90 BUY.
    _set_open_positions(
        runtime,
        {"GRANULES.NS": _FakePosition(10, Decimal("100"))},
    )

    sig = runtime._action_to_signal(
        _TW_ACTION,
        ticker="GRANULES.NS",
        bar_date_ns=_BAR_NS,
        last_price=Decimal("100"),
    )

    assert sig is not None
    assert sig.side == "BUY"
    assert sig.qty == 90
    types = [e["type"] for e in runtime._events]
    assert "rebalance_down_suppressed_capital_shrink" not in types


def test_env_override_allows_trim_down(monkeypatch):
    """#5: flagged + ALGO_ALLOW_REBALANCE_DOWN_ON_SHRINK=1 → the trim
    SELL is produced (user genuinely wants to reduce capital)."""
    monkeypatch.setenv("ALGO_ALLOW_REBALANCE_DOWN_ON_SHRINK", "1")
    runtime = _make_runtime(initial_capital_inr=Decimal("20000"))
    runtime._capital_below_deployed = True
    _set_open_positions(
        runtime,
        {"GRANULES.NS": _FakePosition(200, Decimal("100"))},
    )

    sig = runtime._action_to_signal(
        _TW_ACTION,
        ticker="GRANULES.NS",
        bar_date_ns=_BAR_NS,
        last_price=Decimal("100"),
    )

    assert sig is not None
    assert sig.side == "SELL"
    assert sig.qty == 100
    types = [e["type"] for e in runtime._events]
    assert "rebalance_down_suppressed_capital_shrink" not in types


def test_unflagged_trim_down_is_allowed():
    """Sanity: when NOT flagged, a trim-down SELL is produced as
    before (no behaviour change off the unhappy path)."""
    runtime = _make_runtime(initial_capital_inr=Decimal("20000"))
    assert runtime._capital_below_deployed is False
    _set_open_positions(
        runtime,
        {"GRANULES.NS": _FakePosition(200, Decimal("100"))},
    )

    sig = runtime._action_to_signal(
        _TW_ACTION,
        ticker="GRANULES.NS",
        bar_date_ns=_BAR_NS,
        last_price=Decimal("100"),
    )

    assert sig is not None
    assert sig.side == "SELL"
    assert sig.qty == 100


# --- Part 3: SAFETY — protective exits never blocked ---------------


def test_stop_loss_exit_unaffected_by_flag():
    """#6 SAFETY: a protective ``exit`` (the path stop_loss/time_stop
    take) produces its SELL normally even when flagged — the
    guardrail must NEVER block a protective exit."""
    runtime = _make_runtime(initial_capital_inr=Decimal("20000"))
    runtime._capital_below_deployed = True
    _set_open_positions(
        runtime,
        {"GRANULES.NS": _FakePosition(200, Decimal("100"))},
    )

    sig = runtime._action_to_signal(
        {"type": "exit"},
        ticker="GRANULES.NS",
        bar_date_ns=_BAR_NS,
        last_price=Decimal("100"),
    )

    assert sig is not None
    assert sig.side == "SELL"
    assert sig.qty == 200
    assert sig.reason == "exit"
    types = [e["type"] for e in runtime._events]
    assert "rebalance_down_suppressed_capital_shrink" not in types

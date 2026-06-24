"""Kite balance cap gate in LiveRuntime._on_bar_close.

Three cases:
1. available_inr covers full qty → no adjustment, signal_generated only
2. available_inr covers partial qty → signal_adjusted event, reduced qty sent to Kite
3. available_inr < 1 share → signal_rejected(reason=insufficient_balance), no Kite call

The cap applies to BUY only. SELL signals are unaffected.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import json

import pytest

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="Requires pyarrow + Python >=3.10 (run inside Docker backend container)",
)

_TICKER = "SKYGOLD.NS"
_PRICE = Decimal("614.50")


def _strategy_payload(*, buy_qty: int = 5) -> dict:
    return {
        "id": str(uuid4()),
        "name": "balance cap test strategy",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {"type": "bar_close", "interval": "1d", "time": "15:25 IST"},
        "rebalance": {"type": "daily", "max_positions": 5},
        "root": {"type": "buy", "qty": {"shares": buy_qty}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {"max_exposure_pct": 80, "max_concentration_pct": 25},
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


def _make_bar(*, ticker: str = _TICKER, close: float = float(_PRICE)):
    from backend.algo.stream.types import Bar

    return Bar(
        ticker=ticker,
        interval_sec=86400,
        bar_open_ts_ns=1_000_000_000,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=10000,
        written_at=datetime(2026, 6, 24, 10, 0, tzinfo=timezone.utc),
    )


def _make_runtime(*, buy_qty: int = 5):
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(_strategy_payload(buy_qty=buy_qty))

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "max_inr": Decimal("10000000"),
        "max_orders_per_day": 100,
        "allowed_tickers": [_TICKER],
        "cumulative_inr_today": Decimal("0"),
        "orders_count_today": 0,
        "gtt_limit_headroom_pct": Decimal("0.005"),
    }
    caps_repo.update_in_flight = AsyncMock()
    caps_repo.increment_daily_counters = AsyncMock()

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    kite = MagicMock()
    kite.dry_run = False
    kite.place_order = MagicMock(return_value="KITE_ORDER_99")

    caps = {"live_orders_enabled": True, "allowed_tickers": [_TICKER]}

    with patch("backend.algo.live.position_hydration.hydrate", return_value=[]):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=date(2026, 6, 24),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime, kite


def _seed_bars(runtime, *, ticker: str = _TICKER, close: float = float(_PRICE)):
    from backend.algo.backtest.types import BarData as _BackBar

    runtime._bars_by_ticker[ticker] = [
        _BackBar(
            ticker=ticker,
            date=date(2026, 6, d),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=10000,
        )
        for d in range(1, 21)
    ]


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_balance_no_adjustment():
    """Available ₹ covers full qty → no signal_adjusted event."""
    runtime, kite = _make_runtime(buy_qty=5)
    _seed_bars(runtime)

    with patch(
        "backend.algo.live.runtime.fetch_kite_available_cash",
        new=AsyncMock(return_value=Decimal("10000")),  # covers 5 × 614.50 = 3072.50
    ), patch(
        "backend.algo.live.runtime.budget_reserve",
        new=AsyncMock(return_value=uuid4()),
    ), patch(
        "backend.algo.live.runtime.budget_transition",
        new=AsyncMock(),
    ):
        await runtime._on_bar_close(bar=_make_bar(), last_price=_PRICE)

    adj = [e for e in runtime._events if e["type"] == "signal_adjusted"]
    assert adj == [], "No adjustment expected when balance covers full qty"

    gen = [e for e in runtime._events if e["type"] == "signal_generated"]
    assert len(gen) == 1
    assert json.loads(gen[0]["payload_json"])["qty"] == 5


@pytest.mark.asyncio
async def test_partial_balance_reduces_qty():
    """Available ₹1800 → can only afford 2 shares at 614.50 → qty reduced 5→2."""
    from backend.algo.paper.types import RiskDecision

    runtime, kite = _make_runtime(buy_qty=5)
    _seed_bars(runtime)

    with patch(
        "backend.algo.live.runtime.fetch_kite_available_cash",
        new=AsyncMock(return_value=Decimal("1800")),  # 1800 // 614.50 = 2
    ), patch(
        # Skip the full pre_trade_check safety pipeline (budget tables not
        # seeded in test env) — the gate under test is the balance cap above.
        "backend.algo.live.runtime.pre_trade_check",
        new=AsyncMock(return_value=RiskDecision(outcome="accept")),
    ), patch(
        "backend.algo.live.runtime.budget_reserve",
        new=AsyncMock(return_value=uuid4()),
    ), patch(
        "backend.algo.live.runtime.budget_transition",
        new=AsyncMock(),
    ):
        await runtime._on_bar_close(bar=_make_bar(), last_price=_PRICE)

    adj = [e for e in runtime._events if e["type"] == "signal_adjusted"]
    assert len(adj) == 1
    p = json.loads(adj[0]["payload_json"])
    assert p["old_qty"] == 5
    assert p["new_qty"] == 2
    assert p["reason"] == "insufficient_kite_balance"

    # Kite must have been called with the reduced qty
    assert kite.place_order.called
    call_kwargs = kite.place_order.call_args
    assert call_kwargs.kwargs["quantity"] == 2 or (
        len(call_kwargs.args) > 3 and call_kwargs.args[3] == 2
    )


@pytest.mark.asyncio
async def test_zero_balance_rejects_signal():
    """Available ₹400 < 614.50/share → rejected, no Kite call."""
    runtime, kite = _make_runtime(buy_qty=5)
    _seed_bars(runtime)

    with patch(
        "backend.algo.live.runtime.fetch_kite_available_cash",
        new=AsyncMock(return_value=Decimal("400")),  # 400 // 614.50 = 0
    ), patch(
        "backend.algo.live.runtime.budget_reserve",
        new=AsyncMock(return_value=uuid4()),
    ):
        result = await runtime._on_bar_close(bar=_make_bar(), last_price=_PRICE)

    assert result == 0
    rejected = [
        e for e in runtime._events
        if e["type"] == "signal_rejected"
        and json.loads(e["payload_json"]).get("reason") == "insufficient_balance"
    ]
    assert len(rejected) == 1
    p = json.loads(rejected[0]["payload_json"])
    assert p["ticker"] == _TICKER
    assert "available_inr" in p

    kite.place_order.assert_not_called()

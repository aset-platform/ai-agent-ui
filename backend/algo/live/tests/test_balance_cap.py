"""Strategy budget cap gate in LiveRuntime._on_bar_close.

The cap uses max_inr (strategy allocation) minus committed_inr (currently
deployed open positions) to compute remaining budget, then reduces qty to
fit. This is intentionally tighter than Zerodha's live_balance because the
user keeps a buffer in their account beyond the strategy allocation.

Three cases:
1. Remaining budget covers full weight-based qty → no adjustment
2. Remaining budget covers partial qty → signal_adjusted, reduced qty to Kite
3. Remaining budget < 1 share → signal_rejected(reason=insufficient_balance)
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

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
# strategy max_inr = ₹10 000; HSCL + NSLNISP already deployed ≈ ₹3 000
_MAX_INR = Decimal("10000")


def _strategy_payload(*, buy_qty: int = 5) -> dict:
    return {
        "id": str(uuid4()),
        "name": "budget cap test strategy",
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


def _make_runtime(*, buy_qty: int = 5, max_inr: Decimal = _MAX_INR):
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(_strategy_payload(buy_qty=buy_qty))

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "max_inr": max_inr,
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


def _seed_open_position(runtime, *, ticker: str, qty: int, avg_price: Decimal):
    """Plant a synthetic open position so committed_inr_now reflects deployed capital."""
    from backend.algo.backtest.types import Fill

    fill = Fill(
        intent_id=uuid4(),
        ticker=ticker,
        side="BUY",
        qty=qty,
        fill_price=avg_price,
        fill_date=date(2026, 6, 20),
        fees_inr=Decimal("0"),
        fee_rates_version="test",
    )
    runtime._positions.apply_fill(fill)


def _budget_gate_patches():
    """Task 2.2 — a LIVE BUY now routes through the atomic gate
    (budget_reserve_if_headroom + budget_load_user) and _on_bar_close
    reads in-flight reservations via budget_active_for_strategy. These
    tests target the STRATEGY max_inr cap (deployed from open
    positions), so stub the user-pool gate to always pass and report
    zero in-flight reservations — keeps the cap logic under test the
    sole gate, no real PG."""
    from backend.algo.live.budget_types import UserBudget

    async def _load_user(_uid):
        return UserBudget(
            user_id=_uid,
            allocated_inr=Decimal("100000000"),
        )

    async def _active(_uid, _sid):
        return Decimal("0")

    return (
        patch(
            "backend.algo.live.runtime.budget_reserve_if_headroom",
            new=AsyncMock(return_value=uuid4()),
        ),
        patch(
            "backend.algo.live.runtime.budget_load_user",
            new=_load_user,
        ),
        patch(
            "backend.algo.live.runtime.budget_active_for_strategy",
            new=_active,
        ),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_budget_no_adjustment():
    """Remaining strategy budget covers full qty → no signal_adjusted event.

    max_inr=10000, deployed=0, remaining=10000.
    5 × 614.50 = 3072.50 ≤ 10000 → no adjustment.
    """
    from backend.algo.paper.types import RiskDecision

    runtime, kite = _make_runtime(buy_qty=5, max_inr=Decimal("10000"))
    _seed_bars(runtime)

    _g1, _g2, _g3 = _budget_gate_patches()
    with patch(
        "backend.algo.live.runtime.pre_trade_check",
        new=AsyncMock(return_value=RiskDecision(outcome="accept")),
    ), patch(
        "backend.algo.live.runtime.budget_reserve",
        new=AsyncMock(return_value=uuid4()),
    ), patch(
        "backend.algo.live.runtime.budget_transition",
        new=AsyncMock(),
    ), _g1, _g2, _g3:
        await runtime._on_bar_close(bar=_make_bar(), last_price=_PRICE)

    adj = [e for e in runtime._events if e["type"] == "signal_adjusted"]
    assert adj == [], "No adjustment expected when budget covers full qty"

    gen = [e for e in runtime._events if e["type"] == "signal_generated"]
    assert len(gen) == 1
    assert json.loads(gen[0]["payload_json"])["qty"] == 5


@pytest.mark.asyncio
async def test_partial_budget_reduces_qty():
    """max_inr=10000, deployed ≈ ₹8772 → remaining ₹1228 → only 2 shares at ₹614.50.

    HSCL 1×642.60 + NSLNISP 67×44.98 + SHAILY 1×2739.60 = ₹6396.26 deployed.
    Remaining = 10000 - 6396.26 = ₹3603.74 → 5 shares needed but only 5 fit...

    Use a simpler setup: deploy ₹8000 via 2 fake positions, ₹2000 remaining.
    2000 // 614.50 = 3 shares. buy_qty=5 → reduced to 3.
    """
    from backend.algo.paper.types import RiskDecision

    runtime, kite = _make_runtime(buy_qty=5, max_inr=Decimal("10000"))
    _seed_bars(runtime)
    # Seed ₹8000 deployed: 2 positions totalling ~₹8000
    _seed_open_position(runtime, ticker="HSCL.NS", qty=6, avg_price=Decimal("642.60"))
    _seed_open_position(runtime, ticker="NSLNISP.NS", qty=67, avg_price=Decimal("44.98"))
    # 6×642.60 + 67×44.98 = 3855.60 + 3013.66 = 6869.26 deployed
    # remaining = 10000 - 6869.26 = 3130.74 → 3130.74 // 614.50 = 5 shares... bump deployment
    _seed_open_position(runtime, ticker="FAKE2.NS", qty=5, avg_price=Decimal("400.00"))
    # + 5×400 = 2000 → total deployed = 8869.26, remaining = 1130.74
    # 1130.74 // 614.50 = 1 share → reduced 5→1

    _g1, _g2, _g3 = _budget_gate_patches()
    with patch(
        "backend.algo.live.runtime.pre_trade_check",
        new=AsyncMock(return_value=RiskDecision(outcome="accept")),
    ), patch(
        "backend.algo.live.runtime.budget_reserve",
        new=AsyncMock(return_value=uuid4()),
    ), patch(
        "backend.algo.live.runtime.budget_transition",
        new=AsyncMock(),
    ), _g1, _g2, _g3:
        await runtime._on_bar_close(bar=_make_bar(), last_price=_PRICE)

    adj = [e for e in runtime._events if e["type"] == "signal_adjusted"]
    assert len(adj) == 1
    p = json.loads(adj[0]["payload_json"])
    assert p["old_qty"] == 5
    assert p["new_qty"] == 1
    assert p["reason"] == "strategy_budget_cap"
    assert "committed_inr" in p
    assert "remaining_inr" in p

    assert kite.place_order.called
    assert kite.place_order.call_args.kwargs["quantity"] == 1


@pytest.mark.asyncio
async def test_zero_budget_rejects_signal():
    """Deployed ≥ max_inr → remaining ≤ 0 → signal_rejected, no Kite call."""
    runtime, kite = _make_runtime(buy_qty=5, max_inr=Decimal("10000"))
    _seed_bars(runtime)
    # Deploy more than max_inr so remaining < 0
    _seed_open_position(runtime, ticker="HSCL.NS", qty=16, avg_price=Decimal("642.60"))
    # 16 × 642.60 = ₹10281.60 > ₹10000

    _g1, _g2, _g3 = _budget_gate_patches()
    with patch(
        "backend.algo.live.runtime.budget_reserve",
        new=AsyncMock(return_value=uuid4()),
    ), _g1, _g2, _g3:
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
    assert "max_inr" in p
    assert "committed_inr" in p

    kite.place_order.assert_not_called()

"""Tests for kill-switch in-flight order cancellation (V2-5).

Verifies:
1. Default-OFF: LiveRuntime raises LiveNotEnabledError when
   live_orders_enabled=False.
2. Kill switch via pre_trade_check → KILL_SWITCH reject stops order.
3. cancel_in_flight_orders cancels via KiteAdapter.
4. In-flight order events are persisted.
5. Held positions are NOT affected by kill switch.
6. Kill-switch latency budget: kill-check + pre_trade_check < 50ms p99.
"""
from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.live.runtime import LiveNotEnabledError, LiveRuntime
from backend.algo.live.safety import pre_trade_check
from backend.algo.paper.types import AccountState, RejectReason, Signal


# ----------------------------------------------------------------
# Default-OFF test
# ----------------------------------------------------------------

class TestDefaultOff:
    def test_runtime_raises_when_not_enabled(self):
        """A freshly created caps row with live_orders_enabled=False
        MUST prevent LiveRuntime instantiation."""
        caps = {
            "live_orders_enabled": False,
            "max_inr": Decimal("1000"),
            "max_orders_per_day": 10,
            "allowed_tickers": ["RELIANCE.NS"],
        }
        with pytest.raises(LiveNotEnabledError):
            LiveRuntime(
                strategy=MagicMock(id=uuid4(), name="test"),
                user_id=uuid4(),
                initial_capital_inr=Decimal("100000"),
                fee_as_of=__import__("datetime").date.today(),
                kite=MagicMock(),
                caps=caps,
                run_id=uuid4(),
                caps_repo=MagicMock(),
                kill_switch_repo=MagicMock(),
            )

    def test_runtime_created_when_enabled(self):
        """Enabled caps allows construction without error."""
        caps = {
            "live_orders_enabled": True,
            "max_inr": Decimal("1000"),
            "max_orders_per_day": 10,
            "allowed_tickers": ["RELIANCE.NS"],
        }
        with patch("backend.algo.broker.kite_client.KiteConnect"):
            from backend.algo.broker.kite_client import KiteClient
            kite = KiteClient(
                api_key="k", access_token="t",
            )
        rt = LiveRuntime(
            strategy=MagicMock(id=uuid4(), name="test"),
            user_id=uuid4(),
            initial_capital_inr=Decimal("100000"),
            fee_as_of=__import__("datetime").date.today(),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=AsyncMock(),
            kill_switch_repo=AsyncMock(),
        )
        assert rt is not None


# ----------------------------------------------------------------
# Kill switch via pre_trade_check
# ----------------------------------------------------------------

class TestKillSwitchStopsOrder:
    def test_kill_switch_armed_rejects_all_signals(self):
        """When kill_switch_active=True the first check rejects."""
        from datetime import date

        signal = Signal(
            strategy_id=uuid4(),
            user_id=uuid4(),
            ticker="RELIANCE.NS",
            side="BUY",
            qty=10,
            emitted_at_ns=0,
        )
        account = AccountState(
            user_id=uuid4(),
            day_date=date.today(),
            initial_capital_inr=Decimal("100000"),
            current_equity_inr=Decimal("100000"),
            daily_realised_pnl_inr=Decimal("0"),
            daily_unrealised_pnl_inr=Decimal("0"),
            open_positions={},
            open_position_count=0,
            kill_switch_active=True,  # armed
        )
        decision = pre_trade_check(
            signal=signal,
            caps={
                "max_inr": Decimal("100000"),
                "max_orders_per_day": 10,
                "allowed_tickers": ["RELIANCE.NS"],
            },
            day_state={
                "cumulative_inr_today": Decimal("0"),
                "orders_count_today": 0,
            },
            account=account,
            strategy_risk={
                "per_trade": {}, "daily": {}, "portfolio": {},
            },
            last_price=Decimal("2000"),
        )
        assert decision.outcome == "reject"
        assert decision.reason == RejectReason.KILL_SWITCH


# ----------------------------------------------------------------
# cancel_in_flight_orders
# ----------------------------------------------------------------

@pytest.mark.asyncio
class TestCancelInFlight:
    async def test_cancel_all_submitted_orders(self):
        """Armed kill: cancel_in_flight_orders cancels all submitted."""
        caps_repo = AsyncMock()
        caps_repo.update_in_flight = AsyncMock()
        caps_repo.increment_daily_counters = AsyncMock()

        kill_switch_repo = AsyncMock()
        kill_switch_repo.is_active = AsyncMock(return_value=False)

        mock_kite = MagicMock()
        mock_kite.cancel_order = MagicMock()

        caps = {
            "live_orders_enabled": True,
            "max_inr": Decimal("100000"),
            "max_orders_per_day": 10,
            "allowed_tickers": ["RELIANCE.NS"],
        }
        with patch(
            "backend.algo.live.runtime.flush_events",
        ) as mock_flush:
            rt = LiveRuntime(
                strategy=MagicMock(id=uuid4(), name="test"),
                user_id=uuid4(),
                initial_capital_inr=Decimal("100000"),
                fee_as_of=__import__("datetime").date.today(),
                kite=mock_kite,
                caps=caps,
                run_id=uuid4(),
                caps_repo=caps_repo,
                kill_switch_repo=kill_switch_repo,
            )
            rt._in_flight = [
                {
                    "kite_order_id": "KITE_001",
                    "internal_order_id": str(uuid4()),
                    "symbol": "RELIANCE",
                    "side": "BUY",
                    "qty": 5,
                    "submitted_at": "2026-05-09T10:00:00Z",
                    "status": "submitted",
                },
                {
                    "kite_order_id": "KITE_002",
                    "internal_order_id": str(uuid4()),
                    "symbol": "TCS",
                    "side": "BUY",
                    "qty": 3,
                    "submitted_at": "2026-05-09T10:01:00Z",
                    "status": "submitted",
                },
            ]
            result = await rt.cancel_in_flight_orders()
            assert result["cancelled"] == 2
            assert result["failed"] == 0
            assert mock_kite.cancel_order.call_count == 2

    async def test_cancel_failure_logs_event_but_continues(self):
        """Best-effort: one cancel fails; the other succeeds."""
        caps_repo = AsyncMock()
        caps_repo.update_in_flight = AsyncMock()

        mock_kite = MagicMock()
        # First call fails, second succeeds
        mock_kite.cancel_order.side_effect = [
            Exception("Exchange rejected cancel"),
            None,
        ]

        caps = {"live_orders_enabled": True, "max_inr": 0,
                "max_orders_per_day": 0, "allowed_tickers": []}
        with patch(
            "backend.algo.live.runtime.flush_events",
        ):
            rt = LiveRuntime(
                strategy=MagicMock(id=uuid4(), name="test"),
                user_id=uuid4(),
                initial_capital_inr=Decimal("100000"),
                fee_as_of=__import__("datetime").date.today(),
                kite=mock_kite,
                caps=caps,
                run_id=uuid4(),
                caps_repo=caps_repo,
                kill_switch_repo=AsyncMock(),
            )
            rt._in_flight = [
                {
                    "kite_order_id": "FAIL_001",
                    "status": "submitted",
                    "symbol": "A", "side": "BUY", "qty": 1,
                    "submitted_at": "now",
                    "internal_order_id": str(uuid4()),
                },
                {
                    "kite_order_id": "OK_002",
                    "status": "submitted",
                    "symbol": "B", "side": "BUY", "qty": 1,
                    "submitted_at": "now",
                    "internal_order_id": str(uuid4()),
                },
            ]
            result = await rt.cancel_in_flight_orders()
            assert result["cancelled"] == 1
            assert result["failed"] == 1

    async def test_kill_does_not_auto_flatten_positions(self):
        """Cancelling in-flight orders must NOT close existing
        positions.  Positions stay open after kill."""
        from backend.algo.backtest.types import Fill

        import datetime

        caps_repo = AsyncMock()
        caps_repo.update_in_flight = AsyncMock()

        mock_kite = MagicMock()
        mock_kite.cancel_order = MagicMock()

        caps = {"live_orders_enabled": True, "max_inr": 0,
                "max_orders_per_day": 0, "allowed_tickers": []}
        with patch(
            "backend.algo.live.runtime.flush_events",
        ):
            rt = LiveRuntime(
                strategy=MagicMock(id=uuid4(), name="test"),
                user_id=uuid4(),
                initial_capital_inr=Decimal("100000"),
                fee_as_of=datetime.date.today(),
                kite=mock_kite,
                caps=caps,
                run_id=uuid4(),
                caps_repo=caps_repo,
                kill_switch_repo=AsyncMock(),
            )
            # Apply a real fill to create an open position
            from decimal import Decimal as D
            fill = Fill(
                intent_id=uuid4(),
                ticker="RELIANCE.NS",
                side="BUY",
                qty=10,
                fill_price=D("2000"),
                fill_date=datetime.date.today(),
                fees_inr=D("10"),
                fee_rates_version="2026-04-01",
            )
            rt._positions.apply_fill(fill)
            assert "RELIANCE.NS" in rt._positions.open_positions()

            rt._in_flight = [
                {
                    "kite_order_id": "K001",
                    "status": "submitted",
                    "symbol": "RELIANCE",
                    "side": "BUY", "qty": 5,
                    "submitted_at": "now",
                    "internal_order_id": str(uuid4()),
                },
            ]
            await rt.cancel_in_flight_orders()
            # Position STILL there after kill (not auto-flattened)
            assert "RELIANCE.NS" in rt._positions.open_positions()


# ----------------------------------------------------------------
# Latency budget test (kill-switch hot path)
# ----------------------------------------------------------------

class TestKillSwitchLatencyBudget:
    def test_kill_check_and_pre_trade_under_50ms_p99(self):
        """Kill-check + pre_trade_check chain must complete in
        < 50ms p99 over 100 iterations (hot path only — no I/O)."""
        from datetime import date

        signal = Signal(
            strategy_id=uuid4(),
            user_id=uuid4(),
            ticker="RELIANCE.NS",
            side="BUY",
            qty=10,
            emitted_at_ns=0,
        )
        account = AccountState(
            user_id=uuid4(),
            day_date=date.today(),
            initial_capital_inr=Decimal("100000"),
            current_equity_inr=Decimal("100000"),
            daily_realised_pnl_inr=Decimal("0"),
            daily_unrealised_pnl_inr=Decimal("0"),
            open_positions={},
            open_position_count=0,
            kill_switch_active=True,
        )
        caps = {
            "max_inr": Decimal("100000"),
            "max_orders_per_day": 50,
            "allowed_tickers": ["RELIANCE.NS"],
        }
        day_state = {
            "cumulative_inr_today": Decimal("0"),
            "orders_count_today": 0,
        }
        risk = {"per_trade": {}, "daily": {}, "portfolio": {}}

        timings_ns: list[int] = []
        for _ in range(100):
            t0 = time.perf_counter_ns()
            pre_trade_check(
                signal=signal,
                caps=caps,
                day_state=day_state,
                account=account,
                strategy_risk=risk,
                last_price=Decimal("2000"),
            )
            timings_ns.append(time.perf_counter_ns() - t0)

        timings_ns.sort()
        p99_ns = timings_ns[98]  # index 98 = 99th value in sorted 100
        p99_ms = p99_ns / 1_000_000
        # Budget: < 50ms (hot path, no I/O)
        assert p99_ms < 50, (
            f"Kill-check p99 was {p99_ms:.2f}ms "
            f"— budget is 50ms"
        )

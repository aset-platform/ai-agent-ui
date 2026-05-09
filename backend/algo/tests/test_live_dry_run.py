"""Tests for dry-run mode — KiteClient + LiveRuntime (V2-5).

Covers:
  1. place_order returns DRY_ id when dry_run=True
  2. place_order does NOT call kite_connect.place_order when dry_run=True
  3. cancel_order short-circuits and logs
  4. modify_order short-circuits and logs
  5. ALGO_LIVE_DRY_RUN=true env var is honoured when no explicit kwarg
  6. Explicit dry_run=False overrides env=true
  7. LiveRuntime emits order_filled_live synthetic fill within 200 ms
  8. Synthetic fill carries dry_run: true in event payload

Tests 1-6 run on the host (no Docker required).
Tests 7-8 require the full backend stack (pyarrow, pydantic ≥3.10
eval_type_backport) and are skipped when those deps are absent.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from backend.algo.broker.kite_client import KiteClient

# Guard: tests 7+8 need modules only available inside the Docker
# backend (pyarrow, paper.types with Python ≥3.10 union syntax).
_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)


# ---------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------

def _make_client(
    dry_run: bool | None = True,
    access_token: str = "tok",
) -> tuple[KiteClient, MagicMock]:
    """Return a (KiteClient, mock_kc_instance) pair."""
    with patch(
        "backend.algo.broker.kite_client.KiteConnect",
    ) as MockKC:
        kc_instance = MagicMock()
        MockKC.return_value = kc_instance
        client = KiteClient(
            api_key="test_api_key",
            access_token=access_token,
            dry_run=dry_run,
        )
        client._kc = kc_instance
        return client, kc_instance


# ---------------------------------------------------------------
# 1. place_order returns DRY_ id in dry-run mode
# ---------------------------------------------------------------

class TestDryRunPlaceOrder:
    def test_returns_dry_prefix_id(self):
        client, _ = _make_client(dry_run=True)
        order_id = client.place_order(
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=10,
            order_type="MARKET",
        )
        assert order_id.startswith("DRY_"), (
            f"Expected DRY_ prefix, got {order_id!r}"
        )

    # 2. kite_connect.place_order NOT called in dry-run
    def test_kite_sdk_not_called(self):
        client, mock_kc = _make_client(dry_run=True)
        client.place_order(
            tradingsymbol="INFY",
            exchange="NSE",
            transaction_type="BUY",
            quantity=5,
            order_type="MARKET",
        )
        mock_kc.place_order.assert_not_called()

    def test_dry_id_is_unique_per_call(self):
        client, _ = _make_client(dry_run=True)
        ids = {
            client.place_order(
                tradingsymbol="TCS",
                exchange="NSE",
                transaction_type="BUY",
                quantity=1,
                order_type="MARKET",
            )
            for _ in range(5)
        }
        # All 5 calls must produce distinct ids
        assert len(ids) == 5


# ---------------------------------------------------------------
# 3. cancel_order short-circuits in dry-run
# ---------------------------------------------------------------

class TestDryRunCancelOrder:
    def test_cancel_returns_order_id_without_kite_call(self):
        client, mock_kc = _make_client(dry_run=True)
        result = client.cancel_order("DRY_abc123456789")
        assert result == "DRY_abc123456789"
        mock_kc.cancel_order.assert_not_called()


# ---------------------------------------------------------------
# 4. modify_order short-circuits in dry-run
# ---------------------------------------------------------------

class TestDryRunModifyOrder:
    def test_modify_returns_order_id_without_kite_call(self):
        client, mock_kc = _make_client(dry_run=True)
        result = client.modify_order(
            "DRY_abc123456789",
            price=200.0,
        )
        assert result == "DRY_abc123456789"
        mock_kc.modify_order.assert_not_called()


# ---------------------------------------------------------------
# 5. ALGO_LIVE_DRY_RUN=true env var honoured
# ---------------------------------------------------------------

class TestDryRunEnvVar:
    def test_env_true_activates_dry_run(self, monkeypatch):
        monkeypatch.setenv("ALGO_LIVE_DRY_RUN", "true")
        with patch(
            "backend.algo.broker.kite_client.KiteConnect",
        ) as MockKC:
            kc_instance = MagicMock()
            MockKC.return_value = kc_instance
            # No explicit dry_run kwarg — must read from env
            client = KiteClient(
                api_key="k", access_token="tok",
            )
            client._kc = kc_instance
        assert client.dry_run is True
        order_id = client.place_order(
            tradingsymbol="WIPRO",
            exchange="NSE",
            transaction_type="BUY",
            quantity=2,
            order_type="MARKET",
        )
        assert order_id.startswith("DRY_")
        kc_instance.place_order.assert_not_called()

    # 6. Explicit dry_run=False overrides env=true
    def test_explicit_false_overrides_env_true(self, monkeypatch):
        monkeypatch.setenv("ALGO_LIVE_DRY_RUN", "true")
        with patch(
            "backend.algo.broker.kite_client.KiteConnect",
        ) as MockKC:
            kc_instance = MagicMock()
            MockKC.return_value = kc_instance
            kc_instance.place_order.return_value = {
                "order_id": "REAL_ORDER",
            }
            client = KiteClient(
                api_key="k",
                access_token="tok",
                dry_run=False,  # explicit override
            )
            client._kc = kc_instance
        assert client.dry_run is False
        order_id = client.place_order(
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=1,
            order_type="MARKET",
        )
        assert order_id == "REAL_ORDER"
        kc_instance.place_order.assert_called_once()


# ---------------------------------------------------------------
# 7 + 8. LiveRuntime emits synthetic fill within 200 ms
#        and the payload carries dry_run: true
# ---------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason=(
        "Requires pyarrow + Python ≥3.10 "
        "(run inside Docker backend container)"
    ),
)
class TestLiveRuntimeDryFill:
    """Integration test: LiveRuntime._submit_order → synthetic fill."""

    def _make_runtime(self) -> "LiveRuntime":
        """Return a LiveRuntime with all heavy deps mocked."""
        from backend.algo.live.runtime import LiveRuntime
        from backend.algo.strategy.ast import Strategy

        # Minimal strategy stub
        strategy_id = uuid4()
        strategy = MagicMock(spec=Strategy)
        strategy.id = strategy_id
        strategy.risk = MagicMock()
        strategy.risk.model_dump.return_value = {}

        caps_repo = AsyncMock()
        caps_repo.get.return_value = {
            "live_orders_enabled": True,
            "max_inr": Decimal("100000"),
            "max_orders_per_day": 10,
            "allowed_tickers": ["RELIANCE.NS"],
            "cumulative_inr_today": Decimal("0"),
            "orders_count_today": 0,
        }
        caps_repo.update_in_flight = AsyncMock()
        caps_repo.increment_daily_counters = AsyncMock()

        kill_switch_repo = AsyncMock()
        kill_switch_repo.is_active.return_value = False

        # KiteClient in dry_run mode (no SDK needed)
        with patch(
            "backend.algo.broker.kite_client.KiteConnect",
        ) as MockKC:
            kc_instance = MagicMock()
            MockKC.return_value = kc_instance
            kite = KiteClient(
                api_key="k",
                access_token="tok",
                dry_run=True,
            )
            kite._kc = kc_instance

        caps = {"live_orders_enabled": True}
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

    async def test_synthetic_fill_emitted_within_200ms(self):
        from backend.algo.paper.types import Signal

        runtime = self._make_runtime()
        signal = Signal(
            strategy_id=runtime._strategy.id,
            user_id=runtime._user_id,
            ticker="RELIANCE.NS",
            side="BUY",
            qty=5,
            emitted_at_ns=1_000_000_000,
        )

        # Shorten delay to 20 ms for speed
        runtime._DRY_FILL_DELAY_S = 0.02

        await runtime._submit_order(
            signal=signal,
            last_price=Decimal("2500"),
        )

        # Wait up to 200 ms for the synthetic fill task
        await asyncio.wait_for(
            _wait_for_fill_event(runtime),
            timeout=0.2,
        )

        fill_events = [
            e for e in runtime._events
            if e["type"] == "order_filled_live"
        ]
        assert len(fill_events) == 1, (
            "Expected exactly one order_filled_live event"
        )

    async def test_synthetic_fill_payload_has_dry_run_true(self):
        from backend.algo.paper.types import Signal

        runtime = self._make_runtime()
        signal = Signal(
            strategy_id=runtime._strategy.id,
            user_id=runtime._user_id,
            ticker="RELIANCE.NS",
            side="BUY",
            qty=3,
            emitted_at_ns=1_000_000_000,
        )
        runtime._DRY_FILL_DELAY_S = 0.02

        await runtime._submit_order(
            signal=signal,
            last_price=Decimal("2500"),
        )

        await asyncio.wait_for(
            _wait_for_fill_event(runtime),
            timeout=0.2,
        )

        fill_event = next(
            e for e in runtime._events
            if e["type"] == "order_filled_live"
        )
        import json
        payload = json.loads(fill_event["payload_json"])
        assert payload.get("dry_run") is True, (
            f"Expected dry_run=True in fill payload, got: {payload}"
        )


async def _wait_for_fill_event(runtime) -> None:
    """Poll runtime._events until an order_filled_live appears."""
    for _ in range(50):
        await asyncio.sleep(0.01)
        if any(
            e["type"] == "order_filled_live"
            for e in runtime._events
        ):
            return
    raise AssertionError(
        "order_filled_live event never appeared within timeout",
    )

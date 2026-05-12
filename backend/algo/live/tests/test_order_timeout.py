"""Tests for backend.algo.live.order_timeout — PR #3 of order
safety hardening.

Covers the ``_OrderTimeoutWatcher`` background task that polls
``kite.orders()`` every 15s and cancels session-tagged LIMITs older
than the configured TTL still in OPEN / TRIGGER PENDING state.

Each scenario asserts both the cancel-call shape AND the emitted
``algo.events`` rows so the audit panel can surface the cancellation.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.algo.live import order_timeout
from backend.algo.live.order_timeout import _OrderTimeoutWatcher


UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def _kite_ts(now_minus_seconds: float) -> str:
    """Build a Kite-shaped order_timestamp string (IST local naive).

    Kite returns ``"2026-05-12 09:18:42"`` — space-separated, no
    timezone suffix, in IST local time (container runs TZ=Asia/Kolkata
    per CLAUDE.md §4.5 #32).
    """
    when = datetime.now(IST) - timedelta(seconds=now_minus_seconds)
    return when.strftime("%Y-%m-%d %H:%M:%S")


def _build_order(
    *,
    order_id: str = "240512000123",
    tag: str = "algo-12345678",
    status: str = "OPEN",
    age_seconds: float = 120.0,
) -> dict:
    """Construct a Kite-shaped order dict for the watcher to inspect."""
    return {
        "order_id": order_id,
        "status": status,
        "order_timestamp": _kite_ts(age_seconds),
        "tag": tag,
        "tradingsymbol": "ITC",
        "transaction_type": "BUY",
        "quantity": 8,
        "filled_quantity": 0,
    }


def _make_watcher(
    *,
    kite_orders: list[dict] | None = None,
    cancel_side_effect=None,
    strategy_id=None,
    ttl_seconds: int = 90,
    poll_seconds: int = 0,  # tests short-circuit sleep
):
    """Build a watcher with a MagicMock kite_client + capture sink."""
    kite_client = MagicMock()
    kite_client._kc = MagicMock()
    kite_client._kc.orders = MagicMock(
        return_value=list(kite_orders or []),
    )
    if cancel_side_effect is not None:
        kite_client.cancel_order = MagicMock(
            side_effect=cancel_side_effect,
        )
    else:
        kite_client.cancel_order = MagicMock(
            return_value="cancel-ack",
        )

    sid = uuid4()
    strat = strategy_id or uuid4()
    uid = uuid4()
    events: list[dict] = []

    watcher = _OrderTimeoutWatcher(
        kite_client=kite_client,
        session_id=sid,
        strategy_id=strat,
        user_id=uid,
        events_sink=events.append,
        ttl_seconds=ttl_seconds,
        poll_seconds=poll_seconds,
    )
    return watcher, kite_client, events


def _payload(event_row: dict) -> dict:
    """Helper: parse the JSON payload column of an event row."""
    return json.loads(event_row["payload_json"])


def _strategy_tag(strategy_id) -> str:
    return f"algo-{str(strategy_id)[:8]}"


# ----------------------------------------------------------------
# Cancellation behaviour
# ----------------------------------------------------------------


class TestCancellationBehaviour:
    @pytest.mark.asyncio
    async def test_open_and_aged_is_cancelled(self) -> None:
        """status=OPEN + age > ttl + tag matches → cancel + event."""
        strat = uuid4()
        order = _build_order(
            tag=_strategy_tag(strat),
            status="OPEN",
            age_seconds=120.0,
        )
        watcher, kite, events = _make_watcher(
            kite_orders=[order], strategy_id=strat,
        )

        await watcher._tick_once()

        # cancel_order called exactly once with the order_id +
        # variety="regular".
        kite.cancel_order.assert_called_once()
        call_kwargs = kite.cancel_order.call_args.kwargs
        # Support both keyword-only and positional-order_id styles —
        # the watcher uses keyword args.
        assert (
            call_kwargs.get("order_id") == order["order_id"]
            or kite.cancel_order.call_args.args[0] == order["order_id"]
        )
        assert call_kwargs.get("variety", "regular") == "regular"

        # One order_cancelled_timeout event with the expected shape.
        timeout_events = [
            e for e in events
            if e["type"] == "order_cancelled_timeout"
        ]
        assert len(timeout_events) == 1
        payload = _payload(timeout_events[0])
        assert payload["kite_order_id"] == order["order_id"]
        assert payload["status_at_cancel"] == "OPEN"
        assert payload["tag"] == _strategy_tag(strat)
        assert payload["age_seconds"] >= 90.0

    @pytest.mark.asyncio
    async def test_trigger_pending_is_also_cancelled(self) -> None:
        """status=TRIGGER PENDING + aged → also cancelled."""
        strat = uuid4()
        order = _build_order(
            tag=_strategy_tag(strat),
            status="TRIGGER PENDING",
            age_seconds=120.0,
        )
        watcher, kite, events = _make_watcher(
            kite_orders=[order], strategy_id=strat,
        )

        await watcher._tick_once()

        kite.cancel_order.assert_called_once()
        types = [e["type"] for e in events]
        assert "order_cancelled_timeout" in types

    @pytest.mark.asyncio
    async def test_filled_and_aged_is_ignored(self) -> None:
        """status=COMPLETE → no cancel even if ancient."""
        strat = uuid4()
        order = _build_order(
            tag=_strategy_tag(strat),
            status="COMPLETE",
            age_seconds=600.0,
        )
        watcher, kite, events = _make_watcher(
            kite_orders=[order], strategy_id=strat,
        )

        await watcher._tick_once()

        kite.cancel_order.assert_not_called()
        assert not [
            e for e in events
            if e["type"] == "order_cancelled_timeout"
        ]

    @pytest.mark.asyncio
    async def test_other_strategy_tag_is_ignored(self) -> None:
        """Tag prefix mismatches → ignored even if open + aged."""
        order = _build_order(
            tag="algo-deadbeef",  # someone else's strategy
            status="OPEN",
            age_seconds=120.0,
        )
        # watcher's strategy_id is a fresh uuid → prefix won't match.
        watcher, kite, events = _make_watcher(
            kite_orders=[order],
        )

        await watcher._tick_once()

        kite.cancel_order.assert_not_called()
        assert events == []

    @pytest.mark.asyncio
    async def test_recent_open_is_ignored(self) -> None:
        """age = 30s, ttl = 90s → ignored."""
        strat = uuid4()
        order = _build_order(
            tag=_strategy_tag(strat),
            status="OPEN",
            age_seconds=30.0,
        )
        watcher, kite, events = _make_watcher(
            kite_orders=[order], strategy_id=strat,
        )

        await watcher._tick_once()

        kite.cancel_order.assert_not_called()
        assert events == []


# ----------------------------------------------------------------
# Cancel failure handling
# ----------------------------------------------------------------


class TestCancelFailure:
    @pytest.mark.asyncio
    async def test_cancel_exception_emits_failure_event(
        self,
    ) -> None:
        """cancel_order raises → order_cancel_failed event, no
        propagation."""
        strat = uuid4()
        order = _build_order(
            tag=_strategy_tag(strat),
            status="OPEN",
            age_seconds=120.0,
        )

        class KiteException(Exception):
            pass

        watcher, kite, events = _make_watcher(
            kite_orders=[order],
            cancel_side_effect=KiteException("boom: rate limited"),
            strategy_id=strat,
        )

        await watcher._tick_once()  # must NOT raise

        types = [e["type"] for e in events]
        # No success event, exactly one failure event.
        assert "order_cancelled_timeout" not in types
        failed = [
            e for e in events if e["type"] == "order_cancel_failed"
        ]
        assert len(failed) == 1
        payload = _payload(failed[0])
        assert payload["kite_order_id"] == order["order_id"]
        assert "boom: rate limited" in payload["exc_str"]


# ----------------------------------------------------------------
# Lifecycle / loop
# ----------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_request_stop_terminates_loop(self) -> None:
        """run() exits cleanly after request_stop()."""
        watcher, _kite, _events = _make_watcher(
            kite_orders=[], poll_seconds=0,
        )
        # run() should be a tight loop that checks _stopping each
        # iteration; with poll_seconds=0 it cycles immediately.
        task = asyncio.create_task(watcher.run())
        # Yield once so the task gets a chance to start.
        await asyncio.sleep(0)
        watcher.request_stop()
        # Must complete quickly — well under poll_seconds + 1.
        await asyncio.wait_for(task, timeout=2.0)


# ----------------------------------------------------------------
# Env-var configuration
# ----------------------------------------------------------------


class TestEnvVarOverrides:
    @pytest.mark.asyncio
    async def test_env_var_overrides_ttl(self, monkeypatch) -> None:
        """ALGO_ORDER_TTL_S=10 → a 15s-old order gets cancelled."""
        monkeypatch.setenv("ALGO_ORDER_TTL_S", "10")
        # ttl from env, not the constructor default.
        ttl = order_timeout._read_ttl_s()
        assert ttl == 10

        strat = uuid4()
        order = _build_order(
            tag=_strategy_tag(strat),
            status="OPEN",
            age_seconds=15.0,
        )
        watcher, kite, events = _make_watcher(
            kite_orders=[order],
            strategy_id=strat,
            ttl_seconds=ttl,
        )

        await watcher._tick_once()

        kite.cancel_order.assert_called_once()
        assert any(
            e["type"] == "order_cancelled_timeout" for e in events
        )

    def test_env_var_poll_default(self, monkeypatch) -> None:
        monkeypatch.delenv("ALGO_ORDER_TIMEOUT_POLL_S", raising=False)
        assert order_timeout._read_poll_s() == 15

    def test_env_var_poll_override(self, monkeypatch) -> None:
        monkeypatch.setenv("ALGO_ORDER_TIMEOUT_POLL_S", "5")
        assert order_timeout._read_poll_s() == 5

    def test_env_var_ttl_default(self, monkeypatch) -> None:
        monkeypatch.delenv("ALGO_ORDER_TTL_S", raising=False)
        assert order_timeout._read_ttl_s() == 90

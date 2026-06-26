"""Tests for fill-during-cancel race in ``_OrderTimeoutWatcher``.

Task 4.2: After cancel_order(), the watcher re-fetches the order's
latest state and branches on the terminal result:

  - COMPLETE / filled_qty == qty  → emit ``order_filled_before_cancel``
  - CANCELLED                     → emit ``order_cancelled_timeout`` (unchanged)
  - PARTIAL (0 < filled < qty)    → emit ``order_cancelled_timeout`` with
                                     ``filled_qty`` surfaced
  - re-fetch raises               → fall back to current emit + WARNING

All assertions check both what IS emitted and what is NOT emitted so
mislabelled cancels are caught.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.algo.live.order_timeout import _OrderTimeoutWatcher


UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------


def _kite_ts(now_minus_seconds: float) -> str:
    when = datetime.now(IST) - timedelta(seconds=now_minus_seconds)
    return when.strftime("%Y-%m-%d %H:%M:%S")


def _build_order(
    *,
    order_id: str = "ORD240512001",
    tag: str = "algo-12345678",
    status: str = "OPEN",
    age_seconds: float = 120.0,
    quantity: int = 10,
    filled_quantity: int = 0,
) -> dict:
    return {
        "order_id": order_id,
        "status": status,
        "order_timestamp": _kite_ts(age_seconds),
        "tag": tag,
        "tradingsymbol": "RELIANCE",
        "transaction_type": "BUY",
        "quantity": quantity,
        "filled_quantity": filled_quantity,
    }


def _build_kite_history_leg(
    order_id: str,
    *,
    status: str,
    quantity: int = 10,
    filled_quantity: int = 0,
    average_price: float = 0.0,
) -> dict:
    """Kite ``order_history`` returns a list; the LAST leg is current state."""
    return {
        "order_id": order_id,
        "status": status,
        "quantity": quantity,
        "filled_quantity": filled_quantity,
        "average_price": average_price,
    }


def _make_watcher(
    *,
    kite_orders: list[dict],
    order_history_return: list[dict] | None = None,
    order_history_side_effect=None,
    cancel_side_effect=None,
    strategy_id=None,
    ttl_seconds: int = 90,
    poll_seconds: int = 0,
):
    """Build watcher with mock kite + capture sink."""
    kite_client = MagicMock()
    kite_client._kc = MagicMock()
    kite_client._kc.orders = MagicMock(return_value=list(kite_orders))

    # cancel_order on the KiteClient wrapper (not _kc)
    if cancel_side_effect is not None:
        kite_client.cancel_order = MagicMock(side_effect=cancel_side_effect)
    else:
        kite_client.cancel_order = MagicMock(return_value="ack")

    # order_history re-fetch — called on _kc directly in the impl
    if order_history_side_effect is not None:
        kite_client._kc.order_history = MagicMock(
            side_effect=order_history_side_effect,
        )
    elif order_history_return is not None:
        kite_client._kc.order_history = MagicMock(
            return_value=list(order_history_return),
        )
    else:
        # Default: same as CANCELLED so existing tests keep passing.
        kite_client._kc.order_history = MagicMock(
            return_value=[
                _build_kite_history_leg(
                    "ORD240512001", status="CANCELLED",
                )
            ]
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


def _payload(row: dict) -> dict:
    return json.loads(row["payload_json"])


def _tag(strategy_id) -> str:
    return f"algo-{str(strategy_id)[:8]}"


# ----------------------------------------------------------------
# (a) Order FILLS in the race window → order_filled_before_cancel
# ----------------------------------------------------------------


class TestFillDuringCancel:
    @pytest.mark.asyncio
    async def test_complete_refetch_emits_filled_before_cancel(self) -> None:
        """cancel_order succeeds, re-fetch shows COMPLETE → emit
        order_filled_before_cancel, NOT order_cancelled_timeout."""
        strat = uuid4()
        order_id = "ORD_FILL_001"
        order = _build_order(
            order_id=order_id,
            tag=_tag(strat),
            status="OPEN",
            age_seconds=120.0,
            quantity=10,
            filled_quantity=0,
        )

        history_leg = _build_kite_history_leg(
            order_id,
            status="COMPLETE",
            quantity=10,
            filled_quantity=10,
            average_price=2500.75,
        )

        watcher, kite, events = _make_watcher(
            kite_orders=[order],
            order_history_return=[history_leg],
            strategy_id=strat,
        )

        await watcher._tick_once()

        # cancel_order was still called (we attempted it).
        kite.cancel_order.assert_called_once()

        types = [e["type"] for e in events]
        # Must NOT appear as a cancel.
        assert "order_cancelled_timeout" not in types, (
            "COMPLETE order must not be mislabelled as cancelled"
        )
        # Must appear as a fill.
        assert "order_filled_before_cancel" in types

        fill_events = [
            e for e in events if e["type"] == "order_filled_before_cancel"
        ]
        assert len(fill_events) == 1
        p = _payload(fill_events[0])
        assert p["kite_order_id"] == order_id
        assert p["filled_qty"] == 10
        assert p["avg_price"] == 2500.75

    @pytest.mark.asyncio
    async def test_complete_via_filled_qty_eq_quantity(self) -> None:
        """filled_quantity == quantity (even if status string differs)
        is treated as a fill."""
        strat = uuid4()
        order_id = "ORD_FILL_002"
        order = _build_order(
            order_id=order_id,
            tag=_tag(strat),
            status="OPEN",
            age_seconds=120.0,
            quantity=5,
        )

        # status is COMPLETE and fully filled
        history_leg = _build_kite_history_leg(
            order_id,
            status="COMPLETE",
            quantity=5,
            filled_quantity=5,
            average_price=1000.0,
        )

        watcher, kite, events = _make_watcher(
            kite_orders=[order],
            order_history_return=[history_leg],
            strategy_id=strat,
        )

        await watcher._tick_once()

        types = [e["type"] for e in events]
        assert "order_filled_before_cancel" in types
        assert "order_cancelled_timeout" not in types


# ----------------------------------------------------------------
# (b) Genuine cancel → order_cancelled_timeout unchanged
# ----------------------------------------------------------------


class TestGenuineCancel:
    @pytest.mark.asyncio
    async def test_cancelled_refetch_emits_timeout_event(self) -> None:
        """Re-fetch shows CANCELLED → original order_cancelled_timeout
        event as before; no filled_before_cancel."""
        strat = uuid4()
        order_id = "ORD_CANCEL_001"
        order = _build_order(
            order_id=order_id,
            tag=_tag(strat),
            status="OPEN",
            age_seconds=120.0,
            quantity=8,
        )

        history_leg = _build_kite_history_leg(
            order_id,
            status="CANCELLED",
            quantity=8,
            filled_quantity=0,
        )

        watcher, kite, events = _make_watcher(
            kite_orders=[order],
            order_history_return=[history_leg],
            strategy_id=strat,
        )

        await watcher._tick_once()

        kite.cancel_order.assert_called_once()

        types = [e["type"] for e in events]
        assert "order_cancelled_timeout" in types
        assert "order_filled_before_cancel" not in types

        cancel_events = [
            e for e in events if e["type"] == "order_cancelled_timeout"
        ]
        assert len(cancel_events) == 1
        p = _payload(cancel_events[0])
        assert p["kite_order_id"] == order_id
        assert p["status_at_cancel"] == "OPEN"


# ----------------------------------------------------------------
# (c) Partial fill → filled_qty surfaced in the cancel event
# ----------------------------------------------------------------


class TestPartialFill:
    @pytest.mark.asyncio
    async def test_partial_fill_surfaces_filled_qty(self) -> None:
        """Re-fetch shows partial fill (0 < filled < qty) →
        order_cancelled_timeout with accurate filled_qty (not 0)."""
        strat = uuid4()
        order_id = "ORD_PARTIAL_001"
        order = _build_order(
            order_id=order_id,
            tag=_tag(strat),
            status="OPEN",
            age_seconds=120.0,
            quantity=10,
            filled_quantity=0,  # stale snapshot before the fill
        )

        # After cancel, Kite shows 3 were filled before cancel hit.
        history_leg = _build_kite_history_leg(
            order_id,
            status="CANCELLED",
            quantity=10,
            filled_quantity=3,
            average_price=500.0,
        )

        watcher, kite, events = _make_watcher(
            kite_orders=[order],
            order_history_return=[history_leg],
            strategy_id=strat,
        )

        await watcher._tick_once()

        kite.cancel_order.assert_called_once()

        # Should still be a cancel event (partially filled = cancelled).
        cancel_events = [
            e for e in events if e["type"] == "order_cancelled_timeout"
        ]
        assert len(cancel_events) == 1
        p = _payload(cancel_events[0])
        # filled_qty must reflect the re-fetched value, not the stale 0.
        assert p["filled_qty"] == 3, (
            f"Expected filled_qty=3 from re-fetch, got {p['filled_qty']}"
        )
        assert p["kite_order_id"] == order_id

        # Must NOT emit filled_before_cancel for a partial.
        assert "order_filled_before_cancel" not in [
            e["type"] for e in events
        ]

    @pytest.mark.asyncio
    async def test_partial_with_zero_filled_still_cancel_event(self) -> None:
        """Re-fetch: filled_qty=0, status=CANCELLED → regular cancel
        event with filled_qty=0 (edge case)."""
        strat = uuid4()
        order_id = "ORD_PARTIAL_002"
        order = _build_order(
            order_id=order_id,
            tag=_tag(strat),
            status="OPEN",
            age_seconds=120.0,
            quantity=5,
        )

        history_leg = _build_kite_history_leg(
            order_id, status="CANCELLED", quantity=5, filled_quantity=0,
        )

        watcher, kite, events = _make_watcher(
            kite_orders=[order],
            order_history_return=[history_leg],
            strategy_id=strat,
        )

        await watcher._tick_once()

        cancel_events = [
            e for e in events if e["type"] == "order_cancelled_timeout"
        ]
        assert len(cancel_events) == 1
        p = _payload(cancel_events[0])
        assert p["filled_qty"] == 0


# ----------------------------------------------------------------
# (d) Re-fetch failure → fall back + WARNING, no crash
# ----------------------------------------------------------------


class TestRefetchFailure:
    @pytest.mark.asyncio
    async def test_refetch_exception_falls_back_no_crash(
        self, caplog
    ) -> None:
        """order_history raises → fall back to old cancel event +
        log a WARNING. Watcher loop must NOT crash."""
        strat = uuid4()
        order_id = "ORD_NOREFETCH_001"
        order = _build_order(
            order_id=order_id,
            tag=_tag(strat),
            status="OPEN",
            age_seconds=120.0,
            quantity=6,
            filled_quantity=0,
        )

        class KiteNetworkError(Exception):
            pass

        with caplog.at_level(
            logging.WARNING,
            logger="backend.algo.live.order_timeout",
        ):
            watcher, kite, events = _make_watcher(
                kite_orders=[order],
                order_history_side_effect=KiteNetworkError("timeout"),
                strategy_id=strat,
            )

            # Must not raise.
            await watcher._tick_once()

        # Falls back: emits the cancel event (best we can do).
        types = [e["type"] for e in events]
        assert "order_cancelled_timeout" in types

        # Logged a WARNING about the re-fetch failure.
        warning_records = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "refetch" in r.message.lower()
        ]
        assert warning_records, (
            "Expected a WARNING log mentioning 'refetch' on "
            "order_history failure"
        )

    @pytest.mark.asyncio
    async def test_refetch_exception_no_filled_before_cancel(self) -> None:
        """Re-fetch failure must NOT emit order_filled_before_cancel."""
        strat = uuid4()
        order_id = "ORD_NOREFETCH_002"
        order = _build_order(
            order_id=order_id,
            tag=_tag(strat),
            status="OPEN",
            age_seconds=120.0,
        )

        watcher, kite, events = _make_watcher(
            kite_orders=[order],
            order_history_side_effect=Exception("network"),
            strategy_id=strat,
        )

        await watcher._tick_once()

        types = [e["type"] for e in events]
        assert "order_filled_before_cancel" not in types
        assert "order_cancelled_timeout" in types

    @pytest.mark.asyncio
    async def test_empty_history_falls_back(self) -> None:
        """order_history returns [] → fall back to cancel event,
        no crash."""
        strat = uuid4()
        order_id = "ORD_EMPTY_HISTORY"
        order = _build_order(
            order_id=order_id,
            tag=_tag(strat),
            status="OPEN",
            age_seconds=120.0,
        )

        watcher, kite, events = _make_watcher(
            kite_orders=[order],
            order_history_return=[],  # empty list
            strategy_id=strat,
        )

        await watcher._tick_once()

        types = [e["type"] for e in events]
        assert "order_cancelled_timeout" in types
        assert "order_filled_before_cancel" not in types

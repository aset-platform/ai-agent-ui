# backend/algo/broker/exceptions.py
"""Broker-layer custom exceptions.

Kept in a separate module (vs. inlined in ``kite_client``) so the
``runtime`` and ``routes`` layers can catch specific failures
without importing the SDK wrapper. Order-safety hardening
(2026-05-12 spec) introduced ``LtpStaleError``; future siblings
(``DuplicateOrderError``, ``FreezeChunkExceedsDailyCapError``)
land here too.
"""
from __future__ import annotations


class LtpStaleError(Exception):
    """Raised by ``KiteClient.place_order`` when the reference
    ``last_price_ts`` is older than ``ALGO_MAX_LTP_AGE_S`` seconds.

    The submission is blocked BEFORE the SDK call so the order
    never reaches Kite and the daily cap slot is preserved. The
    runtime catches this and surfaces it as a rejection event.
    """


class DuplicateOrderError(Exception):
    """Raised by ``KiteClient.place_order`` when the same
    ``(user, strategy, symbol, side, qty, minute_bucket)`` tuple
    is re-submitted inside the same 60-second window.

    Caught by a Redis SETNX guard BEFORE the SDK call so duplicate
    Kite submissions never happen. Runtime catches this and surfaces
    it as an ``order_duplicate_blocked`` rejection event.
    """


class FreezeChunkExceedsDailyCapError(Exception):
    """Raised by ``KiteClient.place_order`` when splitting an order
    by NSE freeze quantity would produce more chunks than the
    remaining ``max_orders_per_day`` budget.

    Raised BEFORE any chunk is submitted so the daily cap is not
    silently breached partway through a multi-chunk submission.
    """

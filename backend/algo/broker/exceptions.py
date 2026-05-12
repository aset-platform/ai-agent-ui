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

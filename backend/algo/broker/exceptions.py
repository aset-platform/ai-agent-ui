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
    ``(user, strategy, symbol, side)`` tuple is submitted more than
    once within the same 60-second minute bucket (the content-addressed
    pre-submit dedup guard, TTL = ``ALGO_DEDUP_TTL_S``, default 60 s).

    Caught by a Redis SETNX guard BEFORE the SDK call so duplicate
    Kite submissions never happen. Runtime catches this and surfaces
    it as an ``order_duplicate_blocked`` rejection event.
    """


class DedupUnavailableError(Exception):
    """Raised by ``KiteClient.place_order`` when the Redis dedup gate
    cannot be acquired (Redis error) AND the order notional
    (``qty × price``) meets or exceeds ``ALGO_DEDUP_FAILCLOSED_INR``
    (default 100 000 INR).

    For large-notional orders the dedup backstop is a critical safety
    layer; silently degrading to fail-open when Redis is down
    disengages it at exactly the moment it is most needed. Below the
    threshold the old fail-open behaviour is preserved (warning only)
    because the financial impact of a rare duplicate is bounded.
    """


class FreezeChunkExceedsDailyCapError(Exception):
    """Raised by ``KiteClient.place_order`` when splitting an order
    by NSE freeze quantity would produce more chunks than the
    remaining ``max_orders_per_day`` budget.

    Raised BEFORE any chunk is submitted so the daily cap is not
    silently breached partway through a multi-chunk submission.
    """


class BrokerResponseError(Exception):
    """Raised by ``KiteClient._place_single_chunk`` when the Kite SDK
    returns a response that contains no usable ``order_id`` (e.g.
    ``None``, ``{}``, or a dict missing the ``order_id`` key).

    Emitting a submitted event with an empty id would create an
    untrackable, uncancellable phantom order.  This exception surfaces
    the raw SDK response so callers can treat the placement as a hard
    failure and route it through the partial-chunk reconciler.
    """


class TokenExpiredError(Exception):
    """Kite access token expired / invalid — re-auth required.

    Raised instead of silently degrading so the runtime can surface
    a re-authentication prompt rather than masking the failure as a
    transient or empty-result error.
    """


class PartialChunkPlacementError(Exception):
    """Raised by ``KiteClient.place_order`` when a multi-chunk
    (freeze-split) submission fails AFTER one or more chunks are
    already live on the exchange.

    Carries the broker order ids of the chunks that DID reach Kite
    (``placed_order_ids``), the index of the chunk that failed
    (``failed_chunk``), and the underlying SDK error (``cause``).

    The runtime caller MUST NOT blind-retry the full quantity on
    this error — doing so would duplicate the already-live chunks
    into real, doubled exposure. Instead it records the placed ids
    into ``_in_flight`` and moves the budget reservation into a
    needs-reconcile (PARTIAL) state so the order book / reconciler
    settles the placed chunks.
    """

    def __init__(
        self,
        placed_order_ids: list[str],
        failed_chunk: int,
        cause: Exception,
    ) -> None:
        self.placed_order_ids = placed_order_ids
        self.failed_chunk = failed_chunk
        self.cause = cause
        super().__init__(
            f"chunk {failed_chunk} failed after "
            f"{len(placed_order_ids)} live chunk(s) "
            f"(order_ids={placed_order_ids}): {cause}"
        )

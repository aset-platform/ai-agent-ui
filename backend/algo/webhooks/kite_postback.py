"""Kite postback payload model and checksum verifier.

Reference: Kite Connect v3 postback docs.
Checksum formula: SHA-256(order_id + order_timestamp +
    api_secret).hexdigest() — NOT HMAC.
"""
from __future__ import annotations

import hashlib
import hmac

from pydantic import BaseModel


class KitePostbackPayload(BaseModel):
    """Subset of Kite postback fields we persist.

    Full payload stored verbatim in event payload['raw']
    for forensics.
    """

    user_id: str
    order_id: str
    exchange_order_id: str | None = None
    status: str  # COMPLETE | REJECTED | CANCELLED | UPDATE
    status_message: str | None = None
    tradingsymbol: str
    instrument_token: int
    exchange: str
    transaction_type: str
    order_type: str
    product: str
    quantity: int
    filled_quantity: int
    unfilled_quantity: int
    cancelled_quantity: int
    price: float
    trigger_price: float
    average_price: float
    # IST "YYYY-MM-DD HH:MM:SS" — NO TZ suffix.
    # Hash verbatim; do NOT reformat or convert to UTC.
    order_timestamp: str
    checksum: str
    tag: str | None = None
    guid: str  # idempotency key


def verify_checksum(
    payload: dict,
    api_secret: str,
) -> bool:
    """SHA-256(order_id + order_timestamp + api_secret).

    NOT HMAC despite the visual similarity — Kite mixes
    the secret into the hashed string directly.
    Use hmac.compare_digest for constant-time compare to
    prevent timing-oracle attacks.

    Args:
        payload: Raw JSON dict from Kite postback body.
        api_secret: The Kite API secret for this app.

    Returns:
        True if checksum matches, False otherwise.
    """
    order_id = payload.get("order_id", "")
    order_ts = payload.get("order_timestamp", "")
    expected = hashlib.sha256(
        f"{order_id}{order_ts}{api_secret}".encode(
            "utf-8"
        )
    ).hexdigest()
    received = (payload.get("checksum") or "").lower()
    return hmac.compare_digest(expected, received)

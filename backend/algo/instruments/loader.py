"""Pulls Kite ``/instruments`` once per day and bulk-upserts into
``algo.instruments``.

Picks the first available connected user's api_key + access_token
to make the call — Kite's instruments endpoint returns universal
data so per-user fan-out is wasteful and rate-limit-prone.

Returns a dict summary suitable for the @register_job wrapper:
``{"instruments_loaded": N}`` on success, raises on failure.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from backend.algo.broker.credentials_repo import BrokerCredentialsRepo
from backend.algo.broker.kite_client import KiteClient
from backend.algo.instruments.repo import InstrumentsRepo
from backend.db.engine import get_session_factory

_logger = logging.getLogger(__name__)


async def _pick_first_connected_user_creds() -> dict[str, Any] | None:
    """Find any user with a fresh access_token to make the call."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT user_id "
                    "FROM algo.broker_credentials "
                    "WHERE access_token_fernet IS NOT NULL "
                    "  AND access_token_expires_at > :now "
                    "ORDER BY last_login_at DESC NULLS LAST "
                    "LIMIT 1"
                ),
                {"now": datetime.now(timezone.utc)},
            )
        ).mappings().first()
        if row is None:
            return None
        creds_repo = BrokerCredentialsRepo()
        return await creds_repo.load(session, row["user_id"])


# Segments where our_ticker linkage makes sense — equity cash
# market on NSE/BSE. Futures, options, currency, commodities use
# Kite tradingsymbols that don't map to our `<symbol>.<suffix>`
# convention; leave those rows with our_ticker=None.
_LINKABLE_SEGMENTS = {"NSE", "BSE"}

# Exchange → suffix per CLAUDE.md §4.3 #19 ("Indian stocks .NS
# everywhere"). BSE equity uses .BO suffix in our codebase.
_EXCHANGE_SUFFIX = {"NSE": ".NS", "BSE": ".BO"}


def _derive_our_ticker(row: dict[str, Any]) -> str | None:
    """Map a Kite instrument row to our internal ticker convention.

    NSE/BSE equity → ``<tradingsymbol><suffix>``. Anything else
    returns None (FNO, MCX, currency, etc.) so the link only
    fires where the rest of the codebase actually expects it.
    """
    segment = str(row.get("segment") or "")
    exchange = str(row.get("exchange") or "")
    tradingsymbol = str(row.get("tradingsymbol") or "")
    if segment not in _LINKABLE_SEGMENTS:
        return None
    suffix = _EXCHANGE_SUFFIX.get(exchange)
    if not suffix or not tradingsymbol:
        return None
    return f"{tradingsymbol}{suffix}"


async def run_instruments_refresh(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Daily 07:00 IST: pull /instruments, bulk-upsert."""
    creds = await _pick_first_connected_user_creds()
    if creds is None:
        _logger.warning(
            "algo_kite_instruments_refresh: no connected user; "
            "skipping run.",
        )
        return {"instruments_loaded": 0, "skipped": True}

    client = KiteClient(
        api_key=creds["api_key"],
        access_token=creds["access_token"],
    )
    instruments = client.instruments()
    # Enrich each row with our internal ticker convention.
    # Kite's dump never carries our_ticker — it's our internal
    # link, derived from (segment, exchange, tradingsymbol).
    linked = 0
    for row in instruments:
        derived = _derive_our_ticker(row)
        row["our_ticker"] = derived
        if derived is not None:
            linked += 1

    factory = get_session_factory()
    instruments_repo = InstrumentsRepo()
    async with factory() as session:
        loaded = await instruments_repo.bulk_upsert(
            session, instruments,
        )
    _logger.info(
        "algo_kite_instruments_refresh: upserted %d rows "
        "(%d linked to our_ticker)", loaded, linked,
    )
    return {
        "instruments_loaded": loaded,
        "our_ticker_linked": linked,
    }

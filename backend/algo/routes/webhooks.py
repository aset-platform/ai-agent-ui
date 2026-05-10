"""POST /webhooks/kite/postback — Kite order postback handler.

Not behind JWT auth — checksum IS the auth (per Kite
Connect v3 docs). Rate-limited by existing slowapi
middleware (60 req/min per IP).

Environment:
    KITE_POSTBACK_ENABLED: "true" | "false" (default false)
        Route returns 503 when false — checked before
        any crypto or I/O.
"""

from __future__ import annotations

import json
import logging
import os
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from backend.algo.webhooks.kite_postback import (
    verify_checksum,
)
from backend.secret_loader import load_secret

_logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------


def _get_cache():
    """Lazy import to avoid circular deps at module load."""
    from backend.cache import get_cache as _gc

    return _gc()


_KITE_USER_TTL = 300  # 5 min; mapping is static per user


# ---------------------------------------------------------------
# PG lookup helper (thin wrapper for mocking in tests)
# ---------------------------------------------------------------


async def _pg_lookup_kite_user(
    kite_user_id: str,
) -> UUID | None:
    """Query algo.broker_credentials for our user.id.

    Looks up by kite_user_id (Zerodha client ID stored
    during OAuth token exchange in save_access_token).

    Args:
        kite_user_id: Zerodha client ID from postback.

    Returns:
        Our internal UUID or None if not found.
    """
    from backend.db.engine import get_session_factory

    factory = get_session_factory()
    from sqlalchemy import text

    async with factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT user_id "
                        "FROM algo.broker_credentials "
                        "WHERE kite_user_id = :kite_uid "
                        "LIMIT 1"
                    ),
                    {"kite_uid": kite_user_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return row["user_id"]


# ---------------------------------------------------------------
# _resolve_kite_user — Redis-cached PG lookup
# ---------------------------------------------------------------


async def _resolve_kite_user(
    kite_user_id: str,
) -> UUID | None:
    """Resolve Kite user_id → our internal user.id.

    Cached in Redis for 5 min (mapping is static after
    OAuth). On miss, queries algo.broker_credentials.
    On failure, logs WARNING and returns None — handler
    persists event with our_user_id=null for forensics.

    Args:
        kite_user_id: Zerodha client ID (e.g. "AB1234").

    Returns:
        Our UUID or None if mapping unknown.
    """
    cache = _get_cache()
    cache_key = f"kite_user:{kite_user_id}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return UUID(cached)

    our_id = await _pg_lookup_kite_user(kite_user_id)
    if our_id is None:
        _logger.warning(
            "kite postback: no broker_credentials for "
            "kite_user_id=%s — persisting with null "
            "our_user_id",
            kite_user_id,
        )
        return None

    await cache.set(cache_key, str(our_id), ttl=_KITE_USER_TTL)
    return our_id


# ---------------------------------------------------------------
# _is_duplicate — guid idempotency via DuckDB algo.events
# ---------------------------------------------------------------


async def _is_duplicate(guid: str) -> bool:
    """Check if a postback with this guid was already seen.

    Queries algo.events Iceberg via DuckDB.
    ~5-15ms for our expected volume (<=6k rows/month).

    Args:
        guid: Unique-per-postback ID from Kite payload.

    Returns:
        True if already persisted, False otherwise.
    """
    import asyncio

    def _check() -> bool:
        # Same fix as live.py::_query_postback_events — the original
        # ``StockRepository._iceberg_table_path`` method doesn't exist;
        # use the canonical query_iceberg_table helper instead. Until
        # this fix landed every postback was treated as new (False
        # branch in the except), which is safe but not deduplicated.
        from backend.db.duckdb_engine import query_iceberg_table

        try:
            rows = query_iceberg_table(
                "algo.events",
                "SELECT 1 FROM events "
                "WHERE json_extract_string("
                "payload_json, '$.guid') = ? "
                "LIMIT 1",
                [guid],
            )
            return len(rows) > 0
        except Exception:
            _logger.warning(
                "guid dedup query failed for %s "
                "— treating as non-duplicate",
                guid,
                exc_info=True,
            )
            return False

    return await asyncio.to_thread(_check)


# ---------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------

_NULL_UUID = UUID("00000000-0000-0000-0000-000000000000")


@router.post(
    "/webhooks/kite/postback",
    status_code=200,
    tags=["webhooks"],
    # Explicitly no Depends(get_current_user) —
    # auth IS the checksum (see spec §3.2).
)
async def kite_postback(request: Request) -> dict:
    """Kite order postback receiver.

    Verify → dedup → resolve user → persist.
    Must complete under 3s (Kite is fire-and-forget).
    """
    # Gate 1: feature flag — checked BEFORE any I/O.
    if not _postback_enabled():
        raise HTTPException(
            503,
            "kite postback not enabled on this instance",
        )

    # Gate 2: read raw body (Kite sends raw JSON).
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid json")

    # Gate 3: api_secret must be configured (fail-closed
    # per CLAUDE.md §5.11 — 503, not 401).
    # Slug matches the existing Kite OAuth flow (broker.py uses
    # ``algo_kite_api_secret``) so a single Keychain / docker-secret
    # entry serves both signature verification paths.
    api_secret = load_secret("algo_kite_api_secret")
    if not api_secret:
        _logger.error(
            "kite postback: algo_kite_api_secret not "
            "configured — returning 503"
        )
        raise HTTPException(
            503,
            "kite api secret not configured",
        )

    # Gate 4: checksum verification (constant-time).
    if not verify_checksum(payload, api_secret):
        _logger.warning(
            "kite postback checksum failed for " "order_id=%s",
            payload.get("order_id", "UNKNOWN"),
        )
        raise HTTPException(401, "bad checksum")

    # Gate 5: guid must be present.
    guid = payload.get("guid", "")
    if not guid:
        raise HTTPException(400, "missing guid")

    # Idempotency: second delivery of same guid → 200 ok.
    if await _is_duplicate(guid):
        _logger.info(
            "kite postback: duplicate guid=%s, " "skipping persist",
            guid,
        )
        return {"ok": True, "deduplicated": True}

    # Resolve Kite user → our internal user.id.
    our_user_id = await _resolve_kite_user(payload.get("user_id", ""))

    # Persist into algo.events (same schema as live fills).
    import asyncio

    from backend.algo.backtest.event_writer import (
        event_row,
        flush_events,
    )

    row = event_row(
        session_id=_NULL_UUID,
        user_id=our_user_id or _NULL_UUID,
        strategy_id=None,
        mode="live",
        type_="kite_postback_received",
        payload={
            "guid": guid,
            "order_id": payload.get("order_id", ""),
            "status": payload.get("status", ""),
            "filled_quantity": payload.get("filled_quantity", 0),
            "average_price": payload.get("average_price", 0.0),
            "tradingsymbol": payload.get("tradingsymbol", ""),
            "our_user_id": (str(our_user_id) if our_user_id else None),
            "raw": payload,  # full payload for forensics
        },
    )
    await asyncio.to_thread(flush_events, [row])

    # Cache invalidation per CLAUDE.md §5.13.
    cache = _get_cache()
    if our_user_id:
        cache.invalidate(f"cache:algo:postbacks:{our_user_id}")

    return {"ok": True}


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


def _postback_enabled() -> bool:
    """Read KITE_POSTBACK_ENABLED env var."""
    return os.environ.get("KITE_POSTBACK_ENABLED", "false").lower() == "true"


def create_webhooks_router() -> APIRouter:
    """Return the configured webhooks router."""
    return router

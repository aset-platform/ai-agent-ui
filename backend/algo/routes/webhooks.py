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
    # backend.cache.get_cache() returns a synchronous interface
    # (matches the rest of the cache call sites — see CLAUDE.md
    # §5.13). OBS-2 originally `await`ed both .get and .set which
    # raised "object NoneType can't be used in 'await' expression".
    cache = _get_cache()
    cache_key = f"kite_user:{kite_user_id}"
    cached = cache.get(cache_key)
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

    cache.set(cache_key, str(our_id), ttl=_KITE_USER_TTL)
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

    # Gate 2: read body. Kite Connect v3 quirk: advertises
    # ``application/x-www-form-urlencoded`` in the Content-Type
    # header but POSTs the order fields as a JSON object in the
    # body (verified 2026-05-12 against live postbacks: body
    # starts with ``{`` despite the form-encoded header). Trust
    # the body shape over the header — try JSON first when the
    # body LOOKS like JSON, fall back to form-urlencoded for
    # any well-behaved senders or older Kite deliveries.
    raw = await request.body()
    content_type = (
        request.headers.get("content-type") or ""
    ).lower()
    payload: dict
    body_starts_jsonlike = raw[:1] in (b"{", b"[")
    try:
        if body_starts_jsonlike or "application/json" in content_type:
            payload = json.loads(raw)
            # Coerce to dict regardless — JSON array bodies are
            # not a Kite shape and would crash downstream.
            if not isinstance(payload, dict):
                raise ValueError(
                    "postback JSON body is not an object",
                )
        else:
            # form-urlencoded — convert to plain dict; values
            # come back as strings, the downstream verifier +
            # consumers stringify-compare so that's fine.
            from urllib.parse import parse_qs
            parsed = parse_qs(raw.decode("utf-8"))
            payload = {
                k: (v[0] if len(v) == 1 else v)
                for k, v in parsed.items()
            }
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        _logger.warning(
            "kite postback: body parse failed (ct=%s, "
            "starts=%s): %s",
            content_type, raw[:32], exc,
        )
        raise HTTPException(400, "invalid body")

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

    rows_to_persist: list[dict] = [event_row(
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
    )]

    # Terminal-status postbacks (COMPLETE / REJECTED / CANCELLED)
    # → reconcile against the in-flight order list and emit the
    # matching derived event so the Events panel + P&L summary
    # surface the outcome. Without this the user sees the order
    # submission but no resolution, even after Kite confirms.
    status = str(payload.get("status", "")).upper()
    if status in ("COMPLETE", "REJECTED", "CANCELLED") and (
        our_user_id is not None
    ):
        try:
            await _reconcile_terminal_with_in_flight(
                user_id=our_user_id,
                status=status,
                kite_order_id=str(payload.get("order_id", "")),
                tradingsymbol=str(
                    payload.get("tradingsymbol", ""),
                ),
                side=str(payload.get("transaction_type", "")),
                qty=int(payload.get("filled_quantity", 0) or 0),
                avg_price=float(
                    payload.get("average_price", 0) or 0,
                ),
                status_message=str(
                    payload.get("status_message")
                    or payload.get("status_message_raw")
                    or "",
                ),
                rows_to_persist=rows_to_persist,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "kite postback: in-flight reconcile failed for "
                "order_id=%s status=%s: %s",
                payload.get("order_id", ""), status, exc,
                exc_info=True,
            )

    await asyncio.to_thread(flush_events, rows_to_persist)

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


async def _reconcile_terminal_with_in_flight(
    *,
    user_id: UUID,
    status: str,
    kite_order_id: str,
    tradingsymbol: str,
    side: str,
    qty: int,
    avg_price: float,
    status_message: str,
    rows_to_persist: list[dict],
) -> None:
    """Find the matching in-flight order across this user's
    live runs, transition it to the terminal status, and append
    the matching derived event to ``rows_to_persist``.

    Supports the three terminal Kite statuses:
      COMPLETE  → in-flight.status='filled'    → order_filled_live
      REJECTED  → in-flight.status='rejected'  → order_rejected_live
      CANCELLED → in-flight.status='cancelled' → order_cancelled_live

    Match is by (user_id, kite_order_id) — Kite IDs are unique
    per user. We scan recent live runs (up to 5 most-recent) so
    a postback that lands after the run row was created but
    before the runtime drained still hits the right entry.
    """
    if not kite_order_id or not user_id:
        return
    if status not in ("COMPLETE", "REJECTED", "CANCELLED"):
        return

    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy import bindparam
    from backend.db.engine import get_session_factory
    from backend.algo.backtest.event_writer import event_row
    from datetime import datetime as _dt, UTC

    # Per-status mapping — keeps the loop body small.
    in_flight_status = {
        "COMPLETE": "filled",
        "REJECTED": "rejected",
        "CANCELLED": "cancelled",
    }[status]
    event_type = {
        "COMPLETE": "order_filled_live",
        "REJECTED": "order_rejected_live",
        "CANCELLED": "order_cancelled_live",
    }[status]

    factory = get_session_factory()
    async with factory() as session:
        runs = (await session.execute(text("""
            SELECT id, live_orders_in_flight
            FROM algo.runs
            WHERE user_id = :uid AND mode = 'live'
              AND live_orders_in_flight IS NOT NULL
              AND jsonb_array_length(live_orders_in_flight) > 0
            ORDER BY started_at DESC LIMIT 5
        """), {"uid": user_id})).mappings().all()

        for run in runs:
            in_flight = run["live_orders_in_flight"]
            if isinstance(in_flight, str):
                in_flight = json.loads(in_flight)
            matched = False
            for entry in in_flight:
                if entry.get("kite_order_id") != kite_order_id:
                    continue
                entry["status"] = in_flight_status
                now_iso = _dt.now(UTC).isoformat()
                if status == "COMPLETE":
                    entry["fill_price"] = str(avg_price)
                    entry["fill_qty"] = qty
                    entry["filled_at"] = now_iso
                elif status == "REJECTED":
                    entry["rejected_at"] = now_iso
                    entry["rejection_reason"] = status_message
                elif status == "CANCELLED":
                    entry["cancelled_at"] = now_iso
                    entry["cancel_reason"] = status_message
                matched = True

                # Common audit envelope — ASETPLTFRM-374 epic:
                # omit dry_run on Live events (postbacks fire
                # only for real Kite orders, so absence is
                # already accurate).
                base_payload: dict[str, Any] = {
                    "kite_order_id": kite_order_id,
                    "internal_order_id": entry.get(
                        "internal_order_id",
                    ),
                    "symbol": tradingsymbol,
                    "side": side or entry.get("side", ""),
                    "qty": qty,
                    "source": "kite_postback",
                    # Carry from in-flight entry so the
                    # Positions tab Reason column + the
                    # (symbol, product) attribution join both
                    # populate. Both nullable for entries
                    # written before the runtime started
                    # stamping reason/product.
                    "reason": entry.get("reason"),
                    "product": entry.get("product"),
                }
                if status == "COMPLETE":
                    base_payload["price"] = str(avg_price)
                    base_payload["fees_inr"] = "0"
                elif status == "REJECTED":
                    base_payload["rejection_reason"] = (
                        status_message or "rejected_by_kite"
                    )
                elif status == "CANCELLED":
                    base_payload["cancel_reason"] = (
                        status_message or "cancelled_by_kite"
                    )

                rows_to_persist.append(event_row(
                    session_id=_NULL_UUID,
                    user_id=user_id,
                    strategy_id=None,
                    mode="live",
                    type_=event_type,
                    payload=base_payload,
                ))
                break
            if matched:
                stmt = text("""
                    UPDATE algo.runs
                    SET live_orders_in_flight = :payload
                    WHERE id = :rid
                """).bindparams(bindparam("payload", type_=JSONB))
                await session.execute(stmt, {
                    "payload": in_flight, "rid": run["id"],
                })
                await session.commit()
                _logger.info(
                    "kite postback reconciled: order_id=%s "
                    "run_id=%s status=%s sym=%s qty=%d @₹%.2f",
                    kite_order_id, run["id"], status,
                    tradingsymbol, qty, avg_price,
                )
                return  # stop after first match


def create_webhooks_router() -> APIRouter:
    """Return the configured webhooks router."""
    return router

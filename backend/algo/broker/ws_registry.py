"""Process-local registry: user_id → KiteWsMultiplexer.

A single multiplexer per user is created lazily on first
paper-mode-live-ws run and torn down when all strategies for that
user have stopped.

This is process-local (not Redis-backed) because the KiteTicker
WebSocket object is inherently process-local — we can't share an
open socket across processes.  Single-process assumption holds for
the current Uvicorn/gunicorn deployment (single worker).
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from backend.algo.broker.ws_multiplexer import KiteWsMultiplexer

_logger = logging.getLogger(__name__)

# process-local registry
_registry: dict[UUID, KiteWsMultiplexer] = {}


def get_multiplexer(user_id: UUID) -> KiteWsMultiplexer | None:
    """Return the active multiplexer for *user_id*, or None."""
    return _registry.get(user_id)


def get_multiplexer_if_exists(
    user_id: UUID,
) -> KiteWsMultiplexer | None:
    """Non-creating lookup — used by GET /v1/algo/live/ws-health.

    Distinct from ``get_multiplexer`` only by intent: this name
    makes the read-only / no-side-effect contract explicit at
    call sites that must never spin up a Kite WS connection
    just to answer a poll.
    """
    return _registry.get(user_id)


async def get_or_create_multiplexer(
    *,
    user_id: UUID,
    api_key: str,
    access_token: str,
) -> KiteWsMultiplexer:
    """Return an existing multiplexer or start a new one.

    The caller is responsible for calling ``subscribe()`` after
    obtaining the multiplexer.

    A cached multiplexer is reused only if it is healthy. One that
    is closed OR has hit a non-retryable 403 (``auth_failed``) holds
    a stale access token and will never reconnect — reusing it makes
    a fresh Zerodha login a silent no-op (the new token is dropped),
    leaving live LTPs dead until a process restart. Tear such an
    instance down and rebuild with the new token.
    """
    mux = _registry.get(user_id)
    if mux is not None and not mux._closed and not mux.auth_failed:
        return mux
    if mux is not None:
        _logger.info(
            "ws_registry: discarding stale multiplexer "
            "user=%s (closed=%s auth_failed=%s)",
            user_id, mux._closed, mux.auth_failed,
        )
        try:
            await mux.close()
        except Exception:
            _logger.warning(
                "ws_registry: error closing stale multiplexer "
                "user=%s", user_id, exc_info=True,
            )
        _registry.pop(user_id, None)
    mux = KiteWsMultiplexer(
        user_id=user_id,
        api_key=api_key,
        access_token=access_token,
    )
    _registry[user_id] = mux
    await mux.start()
    _logger.info(
        "ws_registry: created multiplexer user=%s", user_id,
    )
    return mux


async def teardown_user(user_id: UUID) -> None:
    """Close and remove the multiplexer for *user_id*."""
    mux = _registry.pop(user_id, None)
    if mux is not None:
        await mux.close()
        _logger.info(
            "ws_registry: torn down multiplexer user=%s", user_id,
        )


async def shutdown_all() -> None:
    """Close all multiplexers — called from FastAPI lifespan shutdown."""
    user_ids = list(_registry.keys())
    for uid in user_ids:
        mux = _registry.pop(uid, None)
        if mux is not None:
            try:
                await mux.close()
            except Exception:
                _logger.warning(
                    "ws_registry: shutdown_all error user=%s",
                    uid, exc_info=True,
                )
    _logger.info(
        "ws_registry: shutdown_all complete (%d closed)",
        len(user_ids),
    )

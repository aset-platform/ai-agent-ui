"""Lazy singleton async Redis client for the algo module.

Used by KillSwitchRepo for sub-ms is_active() reads from the
PaperRuntime hot path. Falls back to None when REDIS_URL is
empty so the repo runs PG-only — graceful degradation matches
the rest of the codebase.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

_logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_async_redis():  # noqa: ANN201
    """Return an ``redis.asyncio.Redis`` client (or None).

    Cached after the first call so all algo callers share a
    single connection pool. On any construction error returns
    None and logs a warning — the caller (KillSwitchRepo)
    handles a None client gracefully.
    """
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        _logger.info(
            "algo redis_async: no REDIS_URL; running PG-only",
        )
        return None
    try:
        import redis.asyncio as redis_async
        client = redis_async.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=2,
        )
        _logger.info("algo redis_async: connected to %s", redis_url)
        return client
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "algo redis_async: client construction failed: %s",
            exc,
        )
        return None

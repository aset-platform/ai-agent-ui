"""Per-user dry-run mode flag, Redis-backed.

Lets the UI arm / disarm dry-run mode without a backend restart
that the env-var-only design would have required.

Resolution order:
  1. Redis ``algo:dry_run:{user_id}`` ("1" / "0") if set
  2. ``ALGO_LIVE_DRY_RUN`` env var (default fallback when no
     per-user Redis state exists)
  3. ``False`` (real money) if neither source has an opinion

The KiteAdapter still respects whatever the caller passes to
its ``dry_run`` constructor kwarg — this module just provides the
per-user lookup that the start_run endpoint uses.
"""
from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

_logger = logging.getLogger(__name__)


def _key(user_id: UUID) -> str:
    return f"algo:dry_run:{user_id}"


def _env_default() -> bool:
    raw = os.environ.get("ALGO_LIVE_DRY_RUN", "false")
    return raw.lower() in ("true", "1", "yes")


def _coerce(raw: Any) -> bool | None:
    """Decode a Redis value to bool. ``None`` if the key is absent
    so the caller can fall through to env."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not isinstance(raw, str):
        raw = str(raw)
    raw = raw.strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


async def is_armed(user_id: UUID, redis_client: Any) -> bool:
    """Return True if dry-run is armed for the user.

    Falls back to the ``ALGO_LIVE_DRY_RUN`` env var if Redis is
    unavailable or the per-user key is absent.
    """
    if redis_client is None:
        return _env_default()
    try:
        raw = await redis_client.get(_key(user_id))
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "dry_run_flag: redis read failed user=%s: %s "
            "(falling back to env default)",
            user_id, exc,
        )
        return _env_default()
    decoded = _coerce(raw)
    if decoded is None:
        return _env_default()
    return decoded


async def arm(user_id: UUID, redis_client: Any) -> bool:
    """Set the per-user dry-run flag to true. Returns the new state."""
    if redis_client is None:
        return _env_default()
    try:
        await redis_client.set(_key(user_id), "1")
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "dry_run_flag: arm failed user=%s: %s",
            user_id, exc,
        )
        return _env_default()
    return True


async def disarm(user_id: UUID, redis_client: Any) -> bool:
    """Set the per-user dry-run flag to false. Returns False."""
    if redis_client is None:
        return _env_default()
    try:
        await redis_client.set(_key(user_id), "0")
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "dry_run_flag: disarm failed user=%s: %s",
            user_id, exc,
        )
        return _env_default()
    return False

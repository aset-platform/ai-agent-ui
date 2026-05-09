"""Repository for ``algo.live_drift_state`` — read/write drift counters.

Design choices:
- Uses ``get_session_factory()`` (not NullPool) because these writes
  happen in an async context (uvicorn or asyncio.to_thread worker).
- Upsert is a plain INSERT … ON CONFLICT UPDATE so it is idempotent
  on repeated runs.
- ``drift_threshold_shares`` lives on ``algo.kill_switch`` (one row per
  user); a helper fetches it here to keep the reconciliation loop clean.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from backend.db.engine import get_session_factory

_logger = logging.getLogger(__name__)

UTC = timezone.utc


class DriftRepo:
    """Async PG repo for ``algo.live_drift_state``."""

    # ----------------------------------------------------------
    # Read helpers
    # ----------------------------------------------------------

    async def get_open_drifts(
        self, user_id: UUID,
    ) -> list[dict]:
        """Return all unresolved drift rows for a user."""
        factory = get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT user_id, symbol, "
                        "       first_seen_at, consecutive_runs, "
                        "       last_diff, resolved_at, updated_at "
                        "FROM algo.live_drift_state "
                        "WHERE user_id = :uid "
                        "  AND resolved_at IS NULL "
                        "ORDER BY first_seen_at ASC"
                    ),
                    {"uid": user_id},
                )
            ).mappings().all()
        return [dict(r) for r in rows]

    async def get_drift(
        self, user_id: UUID, symbol: str,
    ) -> dict | None:
        """Fetch a single drift row (open or resolved)."""
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT user_id, symbol, "
                        "       first_seen_at, consecutive_runs, "
                        "       last_diff, resolved_at, updated_at "
                        "FROM algo.live_drift_state "
                        "WHERE user_id = :uid "
                        "  AND symbol = :sym"
                    ),
                    {"uid": user_id, "sym": symbol},
                )
            ).mappings().one_or_none()
        return dict(row) if row else None

    async def get_drift_threshold(
        self, user_id: UUID,
    ) -> int:
        """Return the per-user drift threshold (default 0)."""
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT drift_threshold_shares "
                        "FROM algo.kill_switch "
                        "WHERE user_id = :uid"
                    ),
                    {"uid": user_id},
                )
            ).one_or_none()
        if row is None:
            return 0
        return int(row[0])

    # ----------------------------------------------------------
    # Write helpers
    # ----------------------------------------------------------

    async def upsert_drift(
        self,
        user_id: UUID,
        symbol: str,
        diff_payload: dict,
    ) -> int:
        """Insert or bump consecutive_runs for an open drift.

        Returns the NEW consecutive_runs value.
        """
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "INSERT INTO algo.live_drift_state "
                    "  (user_id, symbol, first_seen_at, "
                    "   consecutive_runs, last_diff, "
                    "   resolved_at, updated_at) "
                    "VALUES "
                    "  (:uid, :sym, :now, 1, "
                    "   :payload::jsonb, NULL, :now) "
                    "ON CONFLICT (user_id, symbol) DO UPDATE "
                    "  SET consecutive_runs = "
                    "        CASE WHEN "
                    "          algo.live_drift_state.resolved_at "
                    "          IS NOT NULL "
                    "        THEN 1 "
                    "        ELSE "
                    "          algo.live_drift_state"
                    "          .consecutive_runs + 1 "
                    "        END, "
                    "      last_diff = :payload::jsonb, "
                    "      resolved_at = NULL, "
                    "      updated_at = :now "
                    "RETURNING consecutive_runs"
                ),
                {
                    "uid": user_id,
                    "sym": symbol,
                    "now": now,
                    "payload": json.dumps(
                        diff_payload, default=str,
                    ),
                },
            )
            await session.commit()
            row = result.one()
        return int(row[0])

    async def resolve_drift(
        self, user_id: UUID, symbol: str,
    ) -> bool:
        """Mark a drift as resolved. Returns True if a row was updated."""
        now = datetime.now(UTC)
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "UPDATE algo.live_drift_state "
                    "SET resolved_at = :now, "
                    "    updated_at  = :now "
                    "WHERE user_id = :uid "
                    "  AND symbol   = :sym "
                    "  AND resolved_at IS NULL"
                ),
                {"uid": user_id, "sym": symbol, "now": now},
            )
            await session.commit()
        return result.rowcount > 0

    async def set_drift_threshold(
        self, user_id: UUID, threshold: int,
    ) -> None:
        """Upsert the drift threshold on kill_switch row."""
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO algo.kill_switch "
                    "  (user_id, active, "
                    "   drift_threshold_shares) "
                    "VALUES (:uid, false, :thr) "
                    "ON CONFLICT (user_id) DO UPDATE "
                    "  SET drift_threshold_shares = :thr"
                ),
                {"uid": user_id, "thr": threshold},
            )
            await session.commit()

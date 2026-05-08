"""Daily 05:30 IST job that flags users whose Kite access_token
is past or imminent its 06:00 IST expiry, so the UI can prompt
re-authentication BEFORE strategies need a fresh token.

Emits an audit-event row per affected user; the frontend WS
broker-status hook picks it up via existing event fan-out. v2
adds an optional one-tap re-auth email link; v1 ships the
audit-row only.

Idempotent — running twice on the same morning produces the
same set of audit events because the predicate is over
``access_token_expires_at`` which is a stable column.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from backend.audit_persistence import write_audit_event
from backend.db.engine import get_session_factory

_logger = logging.getLogger(__name__)

# Re-auth notice fires when expiry is within the window below.
_NOTICE_WINDOW = timedelta(hours=1)


async def run_reauth_notify_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan algo.broker_credentials for tokens expiring soon."""
    factory = get_session_factory()
    cutoff = datetime.now(timezone.utc) + _NOTICE_WINDOW
    notified: list[str] = []

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT user_id, kite_user_id, "
                        "access_token_expires_at "
                        "FROM algo.broker_credentials "
                        "WHERE access_token_fernet IS NOT NULL "
                        "  AND access_token_expires_at <= :cutoff"
                    ),
                    {"cutoff": cutoff},
                )
            )
            .mappings()
            .all()
        )

        for row in rows:
            user_id = str(row["user_id"])
            await write_audit_event(
                session=session,
                user_id=user_id,
                event_type="ALGO_BROKER_REAUTH_REQUIRED",
                metadata={
                    "kite_user_id": row["kite_user_id"],
                    "expires_at": (
                        row["access_token_expires_at"].isoformat()
                        if row["access_token_expires_at"]
                        else None
                    ),
                },
            )
            notified.append(user_id)

    _logger.info(
        "algo_kite_reauth_notify: notified %d user(s)",
        len(notified),
    )
    return {"notified_count": len(notified), "user_ids": notified}

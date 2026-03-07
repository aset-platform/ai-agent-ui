"""Audit log append and query operations for auth.

Functions
---------
- :func:`append_audit_event`
- :func:`list_audit_events`
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pyarrow as pa

from auth.repo.catalog import audit_table
from auth.repo.schemas import _AUDIT_PA_SCHEMA, _from_ts, _now_utc, _to_ts

# Module-level logger; kept at module scope intentionally
# (not a mutable data global).
logger = logging.getLogger(__name__)


def append_audit_event(
    cat,
    event_type: str,
    actor_user_id: str,
    target_user_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Append an immutable event row to the ``auth.audit_log`` table.

    Args:
        cat: The loaded Iceberg catalog.
        event_type: One of ``USER_CREATED``, ``USER_UPDATED``,
            ``USER_DELETED``, ``LOGIN``, ``PASSWORD_RESET``, ``OAUTH_LOGIN``.
        actor_user_id: UUID of the user who performed the action.
        target_user_id: UUID of the user who was affected.
        metadata: Optional dict of extra context (serialised to JSON).
    """
    row = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "actor_user_id": actor_user_id,
        "target_user_id": target_user_id,
        "event_timestamp": _to_ts(_now_utc()),
        "metadata": json.dumps(metadata) if metadata else None,
    }
    arrow_table = pa.table(
        {k: [v] for k, v in row.items()}, schema=_AUDIT_PA_SCHEMA
    )
    audit_table(cat).append(arrow_table)
    logger.debug(
        "Audit event type=%s actor=%s target=%s",
        event_type,
        actor_user_id,
        target_user_id,
    )


def list_audit_events(cat) -> List[Dict[str, Any]]:
    """Return all audit log events, sorted newest-first.

    Args:
        cat: The loaded Iceberg catalog.

    Returns:
        A list of audit event dicts sorted descending by ``event_timestamp``.
    """
    tbl = audit_table(cat)
    arrow = tbl.scan().to_arrow()
    rows = arrow.to_pylist()
    result = []
    for row in rows:
        d = dict(row)
        d["event_timestamp"] = _from_ts(d.get("event_timestamp"))
        result.append(d)
    result.sort(
        key=lambda r: r.get("event_timestamp")
        or datetime(1970, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )
    return result

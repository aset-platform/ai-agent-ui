"""Per-answer async chat persistence to Iceberg.

Supplements the full-session flush on logout
(``useChatSession.ts:flush()``) by writing each
turn immediately after the response is sent.
Reduces data loss on browser crashes or tab close.

All writes are fire-and-forget — errors are logged
but never propagated to the user.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)


async def persist_chat_turn(
    user_id: str,
    session_id: str,
    user_input: str,
    response: str,
    agent_id: str = "",
) -> None:
    """Append a single chat turn to Iceberg audit log.

    This runs as a fire-and-forget asyncio task
    scheduled from the WS worker thread.

    Args:
        user_id: Authenticated user UUID.
        session_id: Chat session UUID.
        user_input: The user's message.
        response: The assistant's response.
        agent_id: Agent that handled the turn.
    """
    try:
        from tools._stock_shared import _get_repo

        repo = _get_repo()
        if repo is None:
            return

        now = datetime.now(timezone.utc)
        messages = [
            {
                "role": "user",
                "content": user_input,
                "timestamp": now.isoformat(),
                "agent_id": "",
            },
            {
                "role": "assistant",
                "content": response,
                "timestamp": now.isoformat(),
                "agent_id": agent_id,
            },
        ]

        session = {
            "session_id": session_id,
            "user_id": user_id,
            "started_at": now,
            "ended_at": now,
            "message_count": 2,
            "messages_json": json.dumps(messages),
            "agent_ids_used": json.dumps(
                [agent_id] if agent_id else [],
            ),
        }
        repo.save_chat_session(session)
        _logger.debug(
            "Persisted turn for session %s",
            session_id[:8],
        )
    except Exception:
        _logger.debug(
            "Chat turn persistence failed",
            exc_info=True,
        )

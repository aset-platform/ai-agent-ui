"""Chat audit log endpoints.

Stores and retrieves chat session transcripts for the
audit trail.  Partition-isolated by user_id in Iceberg
via :class:`~stocks.repository.StockRepository`.
"""

import json
import logging

from fastapi import APIRouter, Depends

from auth.dependencies import get_current_user
from auth.models import UserContext
from dashboard_models import (
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionSummary,
)

_logger = logging.getLogger(__name__)


def _get_stock_repo():
    """Lazy import to avoid circular imports."""
    from stocks.repository import StockRepository

    return StockRepository()


def create_audit_router() -> APIRouter:
    """Build the ``/audit`` router."""
    router = APIRouter(
        prefix="/audit",
        tags=["audit"],
    )

    @router.post(
        "/chat-sessions",
        status_code=201,
    )
    async def save_chat_session(
        body: ChatSessionCreate,
        user: UserContext = Depends(get_current_user),
    ):
        """Flush chat transcript to Iceberg audit log."""
        stock_repo = _get_stock_repo()

        timestamps = [
            m.timestamp for m in body.messages
        ]
        started = min(timestamps) if timestamps else ""
        ended = max(timestamps) if timestamps else ""

        agent_ids = list(
            {
                m.agent_id
                for m in body.messages
                if m.agent_id
            }
        )

        session = {
            "session_id": body.session_id,
            "user_id": user.user_id,
            "started_at": started,
            "ended_at": ended,
            "message_count": len(body.messages),
            "messages_json": json.dumps(
                [m.model_dump() for m in body.messages]
            ),
            "agent_ids_used": json.dumps(agent_ids),
        }

        try:
            stock_repo.save_chat_session(session)
            _logger.info(
                "Audit: saved chat session %s"
                " for user=%s (%d messages)",
                body.session_id,
                user.user_id,
                len(body.messages),
            )
        except Exception as exc:
            _logger.error(
                "Audit: failed to save session %s: %s",
                body.session_id,
                exc,
            )
            # Don't block logout — return success anyway
            # Error is logged for investigation

        return {
            "status": "saved",
            "session_id": body.session_id,
        }

    @router.get(
        "/chat-sessions",
        response_model=list[ChatSessionSummary],
    )
    async def list_chat_sessions(
        start_date: str | None = None,
        end_date: str | None = None,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
        user: UserContext = Depends(
            get_current_user,
        ),
    ):
        """List user's own past chat sessions."""
        stock_repo = _get_stock_repo()

        try:
            rows = stock_repo.list_chat_sessions(
                user_id=user.user_id,
                start_date=start_date,
                end_date=end_date,
                keyword=keyword,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            _logger.error(
                "Audit: failed to list sessions"
                " for user=%s: %s",
                user.user_id,
                exc,
            )
            return []

        result: list[ChatSessionSummary] = []
        for r in rows:
            agent_ids = []
            raw_agents = r.get("agent_ids_used", "[]")
            try:
                agent_ids = json.loads(raw_agents)
            except (json.JSONDecodeError, TypeError):
                pass

            result.append(
                ChatSessionSummary(
                    session_id=str(
                        r.get("session_id", "")
                    ),
                    started_at=str(
                        r.get("started_at", "")
                    ),
                    ended_at=str(
                        r.get("ended_at", "")
                    ),
                    message_count=int(
                        r.get("message_count", 0)
                    ),
                    preview=str(
                        r.get("preview", "")
                    ),
                    agent_ids_used=agent_ids,
                )
            )

        return result

    @router.get(
        "/chat-sessions/{session_id}",
        response_model=ChatSessionDetail,
    )
    async def get_chat_session_detail(
        session_id: str,
        user: UserContext = Depends(
            get_current_user,
        ),
    ):
        """Fetch a single chat session with
        full message history."""
        stock_repo = _get_stock_repo()

        try:
            detail = (
                stock_repo.get_chat_session_detail(
                    user_id=user.user_id,
                    session_id=session_id,
                )
            )
        except Exception as exc:
            _logger.error(
                "Audit: failed to get session "
                "%s for user=%s: %s",
                session_id,
                user.user_id,
                exc,
            )
            from fastapi.responses import (
                JSONResponse,
            )

            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Failed to load "
                    "session"
                },
            )

        if detail is None:
            from fastapi.responses import (
                JSONResponse,
            )

            return JSONResponse(
                status_code=404,
                content={
                    "detail": "Session not found"
                },
            )

        agent_ids = detail.get(
            "agent_ids_used", []
        )
        if isinstance(agent_ids, str):
            try:
                agent_ids = json.loads(
                    agent_ids
                )
            except (
                json.JSONDecodeError,
                TypeError,
            ):
                agent_ids = []

        messages = detail.get("messages", [])
        return ChatSessionDetail(
            session_id=str(
                detail.get("session_id", "")
            ),
            started_at=str(
                detail.get("started_at", "")
            ),
            ended_at=str(
                detail.get("ended_at", "")
            ),
            message_count=int(
                detail.get("message_count", 0)
            ),
            preview=str(
                detail.get("preview", "")
            ),
            agent_ids_used=agent_ids,
            messages=messages,
        )

    return router

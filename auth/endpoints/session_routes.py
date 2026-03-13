"""Session management endpoints — list, revoke, revoke-all.

Functions
---------
- :func:`register` — attach session routes to the router
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import get_auth_service, get_current_user
from auth.models import UserContext
from auth.service import AuthService

_logger = logging.getLogger(__name__)


def register(router: APIRouter) -> None:
    """Register session management routes.

    Args:
        router: The :class:`~fastapi.APIRouter` to attach to.
    """

    @router.get(
        "/auth/sessions",
        tags=["auth"],
    )
    def list_sessions(
        service: AuthService = Depends(get_auth_service),
        current_user: UserContext = Depends(
            get_current_user,
        ),
    ) -> List[Dict[str, Any]]:
        """Return all active sessions for the current user.

        Returns:
            A list of session metadata dicts with keys:
            ``session_id``, ``user_id``, ``ip_address``,
            ``user_agent``, ``created_at``,
            ``last_activity_at``.
        """
        return service.list_sessions(current_user.user_id)

    @router.delete(
        "/auth/sessions/{session_id}",
        tags=["auth"],
    )
    def revoke_session(
        session_id: str,
        service: AuthService = Depends(get_auth_service),
        current_user: UserContext = Depends(
            get_current_user,
        ),
    ) -> Dict[str, str]:
        """Revoke a single session by its ID.

        Args:
            session_id: The session (JTI) to revoke.

        Returns:
            A dict with ``"detail"`` message.

        Raises:
            HTTPException: 404 if the session is not found.
        """
        revoked = service.revoke_session(current_user.user_id, session_id)
        if not revoked:
            raise HTTPException(
                status_code=404,
                detail="Session not found",
            )
        _logger.info(
            "Session revoked by user_id=%s" " session_id=%s",
            current_user.user_id,
            session_id,
        )
        return {"detail": "Session revoked"}

    @router.post(
        "/auth/sessions/revoke-all",
        tags=["auth"],
    )
    def revoke_all_sessions(
        service: AuthService = Depends(get_auth_service),
        current_user: UserContext = Depends(
            get_current_user,
        ),
    ) -> Dict[str, Any]:
        """Revoke all sessions for the current user.

        Returns:
            A dict with ``"detail"`` and ``"revoked_count"``.
        """
        count = service.revoke_all_sessions(current_user.user_id)
        _logger.info(
            "All sessions revoked by user_id=%s" " count=%d",
            current_user.user_id,
            count,
        )
        return {
            "detail": "All sessions revoked",
            "revoked_count": count,
        }

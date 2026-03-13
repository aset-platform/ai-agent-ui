"""Admin endpoint registrations (superuser only).

Functions
---------
- :func:`register` — attach admin routes to the router
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_auth_service, superuser_only
from auth.models import AdminPasswordResetBody, UserContext
from auth.service import AuthService

_logger = logging.getLogger(__name__)


def register(router: APIRouter) -> None:
    """Register admin-only routes.

    Args:
        router: The :class:`~fastapi.APIRouter` to attach routes to.
    """

    @router.get("/admin/audit-log", tags=["admin"])
    def get_audit_log(
        _: UserContext = Depends(superuser_only),
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return all audit log events, sorted newest-first.

        Args:
            _: Superuser guard.

        Returns:
            A dict ``{"events": [...]}`` where each element is an audit event
            with ISO-8601 ``event_timestamp``.
        """
        repo = _helpers._get_repo()
        raw_events = repo.list_audit_events()
        events = []
        for ev in raw_events:
            d = dict(ev)
            ts = d.get("event_timestamp")
            if ts is not None and hasattr(ts, "isoformat"):
                d["event_timestamp"] = ts.isoformat()
            events.append(d)
        return {"events": events}

    @router.post(
        "/users/{user_id}/reset-password",
        tags=["admin"],
    )
    def admin_reset_password(
        user_id: str,
        body: AdminPasswordResetBody,
        current_user: UserContext = Depends(superuser_only),
        service: AuthService = Depends(get_auth_service),
    ) -> Dict[str, str]:
        """Reset any user's password (superuser only).

        Validates the new password, hashes it, and updates
        the user record.  Also clears any pending self-service
        reset token.

        Args:
            user_id: UUID of the target user.
            body: Request body with ``new_password``.
            current_user: Authenticated superuser context.
            service: Injected :class:`~auth.service.AuthService`.

        Returns:
            A dict ``{"detail": "Password reset successfully"}``.

        Raises:
            HTTPException: 404 if the target user is not found.
        """
        AuthService.validate_password_strength(body.new_password)
        repo = _helpers._get_repo()
        user = repo.get_by_id(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        new_hash = service.hash_password(body.new_password)
        repo.update(
            user_id,
            {
                "hashed_password": new_hash,
                "password_reset_token": None,
                "password_reset_expiry": None,
            },
        )
        repo.append_audit_event(
            "ADMIN_PASSWORD_RESET",
            actor_user_id=current_user.user_id,
            target_user_id=user_id,
        )
        _logger.info(
            "Admin %s reset password for user %s",
            current_user.user_id,
            user_id,
        )
        return {"detail": "Password reset successfully"}

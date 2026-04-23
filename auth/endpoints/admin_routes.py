"""Admin endpoint registrations (superuser only).

Functions
---------
- :func:`register` — attach admin routes to the router
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Response

import auth.endpoints.helpers as _helpers
from auth.dependencies import (
    get_auth_service,
    pro_or_superuser,
    superuser_only,
)
from auth.models import (
    AdminPasswordResetBody,
    UserContext,
)
from auth.service import AuthService

_logger = logging.getLogger(__name__)


def register(router: APIRouter) -> None:
    """Register admin-only routes.

    Args:
        router: The :class:`~fastapi.APIRouter` to attach routes to.
    """

    @router.get("/admin/audit-log", tags=["admin"])
    async def get_audit_log(
        scope: str = Query(
            "self",
            description=(
                "'self' returns only events involving"
                " the caller; 'all' is superuser-only."
            ),
        ),
        caller: UserContext = Depends(pro_or_superuser),
    ) -> Any:
        """Return audit events, newest-first.

        * ``scope='self'`` — always allowed. Events where
          the caller is the actor OR the target.
        * ``scope='all'`` — superuser only. 403 otherwise.

        Cached in Redis for 60 s; cache is keyed by
        scope so self and all don't collide.
        """
        if scope not in ("self", "all"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "scope must be 'self' or 'all'"
                ),
            )
        if scope == "all" and caller.role != "superuser":
            raise HTTPException(
                status_code=403,
                detail=(
                    "scope='all' requires superuser"
                ),
            )

        try:
            from cache import get_cache, TTL_VOLATILE
        except ImportError:
            get_cache = None  # type: ignore[assignment]

        cache = get_cache() if get_cache else None
        cache_key = (
            "cache:admin:audit:all" if scope == "all"
            else f"cache:admin:audit:self:{caller.user_id}"
        )

        if cache is not None:
            hit = cache.get(cache_key)
            if hit is not None:
                return Response(
                    content=hit,
                    media_type="application/json",
                )

        repo = _helpers._get_repo()
        raw_events = await repo.list_audit_events()
        events = []
        uid = str(caller.user_id)
        for ev in raw_events:
            d = dict(ev)
            if scope == "self":
                actor = str(d.get("actor_user_id") or "")
                target = str(d.get("target_user_id") or "")
                if actor != uid and target != uid:
                    continue
            ts = d.get("event_timestamp")
            if ts is not None and hasattr(
                ts, "isoformat"
            ):
                d["event_timestamp"] = ts.isoformat()
            events.append(d)

        result = {"events": events, "scope": scope}
        if cache is not None:
            cache.set(
                cache_key,
                json.dumps(result),
                TTL_VOLATILE,
            )
        return result

    @router.post(
        "/users/{user_id}/reset-password",
        tags=["admin"],
    )
    async def admin_reset_password(
        user_id: str,
        body: AdminPasswordResetBody,
        current_user: UserContext = Depends(
            superuser_only,
        ),
        service: AuthService = Depends(
            get_auth_service,
        ),
    ) -> Dict[str, str]:
        """Reset any user's password (superuser only).

        Validates the new password, hashes it, and
        updates the user record.  Also clears any
        pending self-service reset token.

        Args:
            user_id: UUID of the target user.
            body: Request body with ``new_password``.
            current_user: Authenticated superuser.
            service: Injected AuthService.

        Returns:
            ``{"detail": "Password reset successfully"}``

        Raises:
            HTTPException: 404 if not found.
        """
        AuthService.validate_password_strength(
            body.new_password,
        )
        repo = _helpers._get_repo()
        user = await repo.get_by_id(user_id)
        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not found",
            )
        new_hash = service.hash_password(
            body.new_password,
        )
        await repo.update(
            user_id,
            {
                "hashed_password": new_hash,
                "password_reset_token": None,
                "password_reset_expiry": None,
            },
        )
        await repo.append_audit_event(
            "ADMIN_PASSWORD_RESET",
            actor_user_id=current_user.user_id,
            target_user_id=user_id,
        )
        _logger.info(
            "Admin %s reset password for user %s",
            current_user.user_id,
            user_id,
        )
        return {
            "detail": "Password reset successfully",
        }

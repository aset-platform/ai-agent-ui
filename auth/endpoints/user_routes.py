"""User management endpoint registrations (superuser only).

Functions
---------
- :func:`register` — attach user CRUD routes to the router
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_auth_service, superuser_only
from auth.models import (
    UserContext,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from auth.service import AuthService

# Module-level logger; kept at module scope as a private convention.
_logger = logging.getLogger(__name__)


def register(router: APIRouter) -> None:
    """Register user CRUD routes (superuser only).

    Args:
        router: The :class:`~fastapi.APIRouter` to attach routes to.
    """

    @router.get("/users", response_model=List[UserResponse], tags=["users"])
    def list_users(
        _: UserContext = Depends(superuser_only),
    ) -> List[UserResponse]:
        """Return all users in the system.

        Args:
            _: Superuser guard.

        Returns:
            A list of :class:`~auth.models.UserResponse` objects.
        """
        repo = _helpers._get_repo()
        return [_helpers._user_to_response(u) for u in repo.list_all()]

    @router.post(
        "/users", response_model=UserResponse, status_code=201, tags=["users"]
    )
    def create_user(
        body: UserCreateRequest,
        caller: UserContext = Depends(superuser_only),
        service: AuthService = Depends(get_auth_service),
    ) -> UserResponse:
        """Create a new user account.

        Args:
            body: User creation request body.
            caller: Superuser guard.
            service: Injected :class:`~auth.service.AuthService`.

        Returns:
            The newly created :class:`~auth.models.UserResponse`.

        Raises:
            HTTPException: 400 if the password is too weak.
            HTTPException: 409 if a user with that email already exists.
        """
        AuthService.validate_password_strength(body.password)
        repo = _helpers._get_repo()
        if repo.get_by_email(str(body.email)) is not None:
            raise HTTPException(
                status_code=409,
                detail="A user with email '{}' already exists.".format(
                    body.email
                ),
            )
        # Enforce max 5 active superusers on creation
        if body.role == "superuser":
            all_users = repo.list_all()
            su_count = sum(
                1
                for u in all_users
                if u.get("role") == "superuser"
                and u.get("is_active")
            )
            if su_count >= 5:
                raise HTTPException(
                    status_code=400,
                    detail="Maximum 5 superusers are allowed.",
                )
        hashed = service.hash_password(body.password)
        user = repo.create(
            {
                "email": str(body.email),
                "hashed_password": hashed,
                "full_name": body.full_name,
                "role": body.role,
            }
        )
        repo.append_audit_event(
            "USER_CREATED",
            actor_user_id=caller.user_id,
            target_user_id=user["user_id"],
            metadata={"email": user["email"], "role": user["role"]},
        )
        _logger.info(
            "User created: user_id=%s by superuser=%s",
            user["user_id"],
            caller.user_id,
        )
        return _helpers._user_to_response(user)

    @router.get(
        "/users/{user_id}", response_model=UserResponse, tags=["users"]
    )
    def get_user(
        user_id: str, _: UserContext = Depends(superuser_only)
    ) -> UserResponse:
        """Fetch a single user by UUID.

        Args:
            user_id: UUID string from the URL path.
            _: Superuser guard.

        Returns:
            The :class:`~auth.models.UserResponse` for the requested user.

        Raises:
            HTTPException: 404 if no user with that ID exists.
        """
        repo = _helpers._get_repo()
        user = repo.get_by_id(user_id)
        if user is None:
            raise HTTPException(
                status_code=404, detail="User '{}' not found".format(user_id)
            )
        return _helpers._user_to_response(user)

    @router.patch(
        "/users/{user_id}", response_model=UserResponse, tags=["users"]
    )
    def update_user(
        user_id: str,
        body: UserUpdateRequest,
        caller: UserContext = Depends(superuser_only),
    ) -> UserResponse:
        """Edit a user's details.

        Args:
            user_id: UUID string from the URL path.
            body: Update fields (all optional).
            caller: Superuser guard.

        Returns:
            The updated :class:`~auth.models.UserResponse`.

        Raises:
            HTTPException: 404 if no user with that ID exists.
            HTTPException: 409 if the new email is already in use.
        """
        repo = _helpers._get_repo()
        if repo.get_by_id(user_id) is None:
            raise HTTPException(
                status_code=404, detail="User '{}' not found".format(user_id)
            )
        updates: Dict[str, Any] = {
            k: v for k, v in body.model_dump().items() if v is not None
        }
        if "email" in updates:
            existing = repo.get_by_email(str(updates["email"]))
            if existing is not None and existing["user_id"] != user_id:
                raise HTTPException(
                    status_code=409,
                    detail="Email '{}' is already in use.".format(
                        updates["email"]
                    ),
                )
        # Enforce max 5 active superusers
        if updates.get("role") == "superuser":
            all_users = repo.list_all()
            su_count = sum(
                1
                for u in all_users
                if u.get("role") == "superuser"
                and u.get("is_active")
                and u.get("user_id") != user_id
            )
            if su_count >= 5:
                raise HTTPException(
                    status_code=400,
                    detail="Maximum 5 superusers are allowed.",
                )
        # Serialize page_permissions dict → JSON string for storage
        if "page_permissions" in updates and isinstance(
            updates["page_permissions"], dict
        ):
            updates["page_permissions"] = _json.dumps(
                updates["page_permissions"]
            )
        updated = repo.update(user_id, updates)
        repo.append_audit_event(
            "USER_UPDATED",
            actor_user_id=caller.user_id,
            target_user_id=user_id,
            metadata={"fields_changed": list(updates.keys())},
        )
        _logger.info(
            "User updated: user_id=%s by superuser=%s", user_id, caller.user_id
        )
        return _helpers._user_to_response(updated)

    @router.delete("/users/{user_id}", tags=["users"])
    def delete_user(
        user_id: str, caller: UserContext = Depends(superuser_only)
    ) -> Dict[str, str]:
        """Soft-delete a user by setting ``is_active = False``.

        Args:
            user_id: UUID string from the URL path.
            caller: Superuser guard.

        Returns:
            A dict ``{"detail": "User deactivated"}``.

        Raises:
            HTTPException: 400 if the caller tries to delete themselves.
            HTTPException: 404 if no user with that ID exists.
        """
        if user_id == caller.user_id:
            raise HTTPException(
                status_code=400,
                detail="Superusers cannot deactivate their own account.",
            )
        repo = _helpers._get_repo()
        if repo.get_by_id(user_id) is None:
            raise HTTPException(
                status_code=404, detail="User '{}' not found".format(user_id)
            )
        repo.delete(user_id)
        repo.append_audit_event(
            "USER_DELETED",
            actor_user_id=caller.user_id,
            target_user_id=user_id,
        )
        _logger.info(
            "User soft-deleted: user_id=%s by superuser=%s",
            user_id,
            caller.user_id,
        )
        return {"detail": "User deactivated"}

"""Profile self-service endpoint registrations (any authenticated user).

Functions
---------
- :func:`register` — attach profile routes to the router
"""

import logging
import os
from typing import Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_current_user
from auth.models import ProfileUpdateRequest, UserContext, UserResponse

# Module-level logger — kept at module scope intentionally; not mutable state.
logger = logging.getLogger(__name__)

# Module-level path constant — prefixed with _ to signal internal use.
# Kept at module level because it is derived from the file-system layout and
# must be available before any class or function is instantiated.
_AVATARS_DIR: str = os.path.join(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
    "data",
    "avatars",
)
_MAX_AVATAR_BYTES: int = 10 * 1024 * 1024  # 10 MB


def register(router: APIRouter) -> None:
    """Register profile self-service routes.

    Args:
        router: The :class:`~fastapi.APIRouter` to attach routes to.
    """

    @router.get("/auth/me", response_model=UserResponse, tags=["profile"])
    def get_me(
        current_user: UserContext = Depends(get_current_user),
    ) -> UserResponse:
        """Return the authenticated user's own profile.

        Args:
            current_user: Authenticated :class:`~auth.models.UserContext`.

        Returns:
            The caller's :class:`~auth.models.UserResponse`.

        Raises:
            HTTPException: 404 if the user record is not found.
        """
        repo = _helpers._get_repo()
        user = repo.get_by_id(current_user.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return _helpers._user_to_response(user)

    @router.patch("/auth/me", response_model=UserResponse, tags=["profile"])
    def patch_me(
        body: ProfileUpdateRequest,
        current_user: UserContext = Depends(get_current_user),
    ) -> UserResponse:
        """Update the authenticated user's own display name or avatar URL.

        Args:
            body: Update fields — ``full_name`` and/or ``avatar_url``.
            current_user: Authenticated :class:`~auth.models.UserContext`.

        Returns:
            The updated :class:`~auth.models.UserResponse`.

        Raises:
            HTTPException: 404 if the user record is not found.
        """
        repo = _helpers._get_repo()
        if repo.get_by_id(current_user.user_id) is None:
            raise HTTPException(status_code=404, detail="User not found")
        updates: Dict[str, object] = {}
        if body.full_name is not None:
            updates["full_name"] = body.full_name
        if body.avatar_url is not None:
            updates["profile_picture_url"] = body.avatar_url
        if not updates:
            user = repo.get_by_id(current_user.user_id)
            return _helpers._user_to_response(user)
        updated = repo.update(current_user.user_id, updates)
        logger.info("Profile updated for user_id=%s", current_user.user_id)
        return _helpers._user_to_response(updated)

    @router.post("/auth/upload-avatar", tags=["profile"])
    async def upload_avatar(
        file: UploadFile = File(...),
        target_user_id: Optional[str] = Query(default=None, alias="user_id"),
        current_user: UserContext = Depends(get_current_user),
    ) -> Dict[str, str]:
        """Upload a profile avatar image (≤10 MB, image/* only).

        Saves the file to ``data/avatars/{user_id}.{ext}`` and updates the
        user's ``profile_picture_url`` in the repository.  Superusers may
        supply a ``user_id`` query parameter to upload on behalf of another
        user; non-superusers may only upload for themselves.

        Args:
            file: The uploaded image file.
            target_user_id: Optional target ``user_id`` (superuser override).
            current_user: Authenticated :class:`~auth.models.UserContext`.

        Returns:
            A dict ``{"avatar_url": "/avatars/{user_id}.{ext}"}``.

        Raises:
            HTTPException: 400 if the content type is not ``image/*``.
            HTTPException: 413 if the file exceeds 10 MB.
        """
        # Resolve which user's avatar we're updating.
        if target_user_id and current_user.role == "superuser":
            resolved_id = target_user_id
        else:
            resolved_id = current_user.user_id
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(
                status_code=400, detail="Only image files are accepted."
            )
        _unsupported = {"image/heic", "image/heif", "image/tiff", "image/bmp"}
        ct_lower = (file.content_type or "").lower()
        fn_lower = (file.filename or "").lower()
        if ct_lower in _unsupported or any(
            fn_lower.endswith(s)
            for s in (".heic", ".heif", ".tiff", ".tif", ".bmp")
        ):
            raise HTTPException(
                status_code=415,
                detail="Unsupported image format. Please upload JPEG, PNG, GIF, or WebP.",
            )
        data = await file.read()
        if len(data) > _MAX_AVATAR_BYTES:
            raise HTTPException(
                status_code=413, detail="Avatar file exceeds 10 MB limit."
            )
        ext = (file.filename or "jpg").rsplit(".", 1)[-1].lower()
        os.makedirs(_AVATARS_DIR, exist_ok=True)
        dest = os.path.join(_AVATARS_DIR, "{}.{}".format(resolved_id, ext))
        with open(dest, "wb") as fh:
            fh.write(data)
        avatar_url = "/avatars/{}.{}".format(resolved_id, ext)
        repo = _helpers._get_repo()
        repo.update(resolved_id, {"profile_picture_url": avatar_url})
        logger.info(
            "Avatar uploaded for user_id=%s url=%s", resolved_id, avatar_url
        )
        return {"avatar_url": avatar_url}

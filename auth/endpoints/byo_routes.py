"""BYO provider-key routes (self-service, any authenticated user).

Endpoints
---------
- ``GET    /v1/users/me/llm-keys``     — list configured providers
- ``PUT    /v1/users/me/llm-keys/{p}`` — upsert key for provider
- ``DELETE /v1/users/me/llm-keys/{p}`` — remove provider key
- ``PATCH  /v1/users/me/byo-settings`` — update ``byo_monthly_limit``

All endpoints are guarded by :func:`auth.dependencies.get_current_user`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Response,
)
from pydantic import BaseModel, Field

import auth.endpoints.helpers as _helpers
from auth.dependencies import get_current_user
from auth.models import UserContext
from auth.repo.byo_repo import ALLOWED_PROVIDERS

_logger = logging.getLogger(__name__)


class KeyUpsertBody(BaseModel):
    """Payload for ``PUT /v1/users/me/llm-keys/{provider}``."""

    key: str = Field(..., min_length=8, max_length=512)
    label: str | None = Field(default=None, max_length=120)


class BYOSettingsBody(BaseModel):
    """Payload for ``PATCH /v1/users/me/byo-settings``."""

    monthly_limit: int = Field(..., ge=0, le=1_000_000)


class LLMKeyRow(BaseModel):
    """Display-safe key metadata."""

    provider: str
    label: str | None
    masked_key: str
    last_used_at: datetime | None
    request_count_30d: int
    created_at: datetime
    updated_at: datetime


def _row_to_model(row: dict[str, Any]) -> LLMKeyRow:
    return LLMKeyRow(**row)


def register(router: APIRouter) -> None:
    """Attach BYO routes to the given router."""

    @router.get(
        "/users/me/llm-keys",
        response_model=list[LLMKeyRow],
        tags=["byo"],
    )
    async def list_my_keys(
        current_user: UserContext = Depends(get_current_user),
    ) -> list[LLMKeyRow]:
        repo = _helpers._get_repo()
        rows = await repo.list_llm_keys(current_user.user_id)
        return [_row_to_model(r) for r in rows]

    @router.put(
        "/users/me/llm-keys/{provider}",
        response_model=LLMKeyRow,
        tags=["byo"],
    )
    async def upsert_my_key(
        body: KeyUpsertBody,
        provider: str = Path(..., min_length=2, max_length=32),
        current_user: UserContext = Depends(get_current_user),
    ) -> LLMKeyRow:
        if provider.lower() not in ALLOWED_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "provider must be one of "
                    f"{sorted(ALLOWED_PROVIDERS)}"
                ),
            )
        repo = _helpers._get_repo()
        try:
            row, created = await repo.upsert_llm_key(
                current_user.user_id,
                provider,
                body.key,
                body.label,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=str(exc),
            ) from exc

        event_type = (
            "BYO_KEY_ADDED" if created else "BYO_KEY_UPDATED"
        )
        await repo.append_audit_event(
            event_type,
            actor_user_id=current_user.user_id,
            target_user_id=current_user.user_id,
            metadata={"provider": row["provider"]},
        )
        _logger.info(
            "BYO key %s user=%s provider=%s",
            "added" if created else "updated",
            current_user.user_id,
            row["provider"],
        )
        return _row_to_model(row)

    @router.delete(
        "/users/me/llm-keys/{provider}",
        tags=["byo"],
    )
    async def delete_my_key(
        provider: str = Path(..., min_length=2, max_length=32),
        current_user: UserContext = Depends(get_current_user),
    ) -> Response:
        if provider.lower() not in ALLOWED_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "provider must be one of "
                    f"{sorted(ALLOWED_PROVIDERS)}"
                ),
            )
        repo = _helpers._get_repo()
        removed = await repo.delete_llm_key(
            current_user.user_id, provider,
        )
        if not removed:
            raise HTTPException(
                status_code=404, detail="Key not configured.",
            )
        await repo.append_audit_event(
            "BYO_KEY_DELETED",
            actor_user_id=current_user.user_id,
            target_user_id=current_user.user_id,
            metadata={"provider": provider.lower()},
        )
        _logger.info(
            "BYO key deleted user=%s provider=%s",
            current_user.user_id,
            provider.lower(),
        )
        return Response(status_code=204)

    @router.patch(
        "/users/me/byo-settings",
        tags=["byo"],
    )
    async def patch_byo_settings(
        body: BYOSettingsBody,
        current_user: UserContext = Depends(get_current_user),
    ) -> dict[str, int]:
        repo = _helpers._get_repo()
        value = await repo.set_byo_monthly_limit(
            current_user.user_id, body.monthly_limit,
        )
        await repo.append_audit_event(
            "USER_UPDATED",
            actor_user_id=current_user.user_id,
            target_user_id=current_user.user_id,
            metadata={
                "fields_changed": ["byo_monthly_limit"],
                "new_limit": value,
                "self_edit": True,
            },
        )
        return {"monthly_limit": value}

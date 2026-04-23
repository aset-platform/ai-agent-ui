"""CRUD for user-supplied LLM provider keys.

Stores one encrypted key per (user_id, provider) in ``user_llm_keys``.
Every mutation fires a corresponding ``BYO_KEY_*`` audit event.
Decryption is used only by the Phase-B cascade override.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.crypto.byo_secrets import (
    decrypt_key,
    encrypt_key,
    mask_key,
)
from backend.db.models.user_llm_key import UserLLMKey

_logger = logging.getLogger(__name__)

ALLOWED_PROVIDERS: frozenset[str] = frozenset({"groq", "anthropic"})
_PROVIDER_PREFIX: dict[str, str] = {
    "groq": "gsk_",
    "anthropic": "sk-ant-",
}


def validate_provider(provider: str) -> str:
    """Return a canonical provider or raise ``ValueError``."""
    p = (provider or "").strip().lower()
    if p not in ALLOWED_PROVIDERS:
        raise ValueError(
            f"provider must be one of {sorted(ALLOWED_PROVIDERS)}",
        )
    return p


def validate_key_format(provider: str, key: str) -> None:
    """Light server-side format check — prevents obvious paste errors."""
    stripped = (key or "").strip()
    if not stripped:
        raise ValueError("key is empty")
    prefix = _PROVIDER_PREFIX[provider]
    if not stripped.startswith(prefix):
        raise ValueError(
            f"{provider} keys should start with '{prefix}'",
        )


async def list_keys(
    session: AsyncSession, user_id: str,
) -> list[dict[str, Any]]:
    """Return display-safe key metadata for the user (no plaintext)."""
    stmt = (
        select(UserLLMKey)
        .where(UserLLMKey.user_id == user_id)
        .order_by(UserLLMKey.provider)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    out: list[dict[str, Any]] = []
    for row in rows:
        # Decrypt only to build the mask — never leaves this function.
        try:
            plaintext = decrypt_key(row.encrypted_key)
            masked = mask_key(plaintext)
        except Exception:
            _logger.warning(
                "Cannot decrypt stored key user=%s provider=%s",
                user_id,
                row.provider,
            )
            masked = "****"
        out.append(
            {
                "provider": row.provider,
                "label": row.label,
                "masked_key": masked,
                "last_used_at": row.last_used_at,
                "request_count_30d": row.request_count_30d,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
    return out


async def upsert_key(
    session: AsyncSession,
    user_id: str,
    provider: str,
    key: str,
    label: str | None,
) -> tuple[dict[str, Any], bool]:
    """Insert or update a user's provider key.

    Returns ``(display_row, created)`` where ``created`` is True when
    a new row was inserted (caller uses it to pick the audit event).
    """
    provider = validate_provider(provider)
    validate_key_format(provider, key)

    stmt = select(UserLLMKey).where(
        UserLLMKey.user_id == user_id,
        UserLLMKey.provider == provider,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    ciphertext = encrypt_key(key.strip())
    now = datetime.now(timezone.utc)
    created = existing is None

    if existing is None:
        row = UserLLMKey(
            user_id=user_id,
            provider=provider,
            encrypted_key=ciphertext,
            label=(label or None),
        )
        session.add(row)
    else:
        existing.encrypted_key = ciphertext
        existing.label = label or None
        existing.updated_at = now
        row = existing

    await session.flush()
    await session.refresh(row)
    masked = mask_key(key.strip())
    return (
        {
            "provider": row.provider,
            "label": row.label,
            "masked_key": masked,
            "last_used_at": row.last_used_at,
            "request_count_30d": row.request_count_30d,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        },
        created,
    )


async def delete_key(
    session: AsyncSession, user_id: str, provider: str,
) -> bool:
    """Delete a user's provider key. Returns True when a row was removed."""
    provider = validate_provider(provider)
    stmt = delete(UserLLMKey).where(
        UserLLMKey.user_id == user_id,
        UserLLMKey.provider == provider,
    )
    res = await session.execute(stmt)
    return (res.rowcount or 0) > 0


async def get_decrypted_key(
    session: AsyncSession, user_id: str, provider: str,
) -> str | None:
    """Return the plaintext key for a user+provider, or None.

    Used by Phase-B cascade override. Callers must treat the result as
    sensitive and never log it.
    """
    provider = validate_provider(provider)
    stmt = select(UserLLMKey).where(
        UserLLMKey.user_id == user_id,
        UserLLMKey.provider == provider,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return decrypt_key(row.encrypted_key)


async def set_byo_monthly_limit(
    session: AsyncSession, user_id: str, monthly_limit: int,
) -> int:
    """Set ``users.byo_monthly_limit`` for a user. Returns new value."""
    if monthly_limit < 0:
        raise ValueError("monthly_limit must be >= 0")
    from backend.db.models.user import User

    stmt = (
        update(User)
        .where(User.user_id == user_id)
        .values(byo_monthly_limit=monthly_limit)
    )
    await session.execute(stmt)
    return monthly_limit


async def increment_chat_counter(
    session: AsyncSession, user_id: str,
) -> None:
    """Atomically bump ``users.chat_request_count``.

    Called from the chat entry point. Non-blocking usage recommended
    (wrap in a fire-and-forget task) so chat latency is unaffected.
    """
    from backend.db.models.user import User

    stmt = (
        update(User)
        .where(User.user_id == user_id)
        .values(
            chat_request_count=User.chat_request_count + 1,
        )
    )
    await session.execute(stmt)

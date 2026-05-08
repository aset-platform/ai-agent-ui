"""Async CRUD over ``algo.broker_credentials`` with Fernet-encrypted
api_key + access_token columns.

Reuses the existing ``BYO_SECRET_KEY`` Fernet from
``backend.crypto.byo_secrets`` — a single master key keeps the
secret-management surface small. Plaintext leaves the repo only
inside the Kite SDK call path; never returned in API responses.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.crypto.byo_secrets import decrypt_key, encrypt_key

_logger = logging.getLogger(__name__)


class BrokerCredentialsRepo:
    """One row per (user_id) in ``algo.broker_credentials``."""

    async def save_api_key(
        self,
        session: AsyncSession,
        user_id: UUID,
        api_key: str,
    ) -> None:
        """Persist the user's Kite API key (encrypted)."""
        ciphertext = encrypt_key(api_key)
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                "INSERT INTO algo.broker_credentials "
                "(user_id, api_key_fernet, created_at, updated_at) "
                "VALUES (:user_id, :api_key_fernet, :now, :now) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "api_key_fernet = EXCLUDED.api_key_fernet, "
                "updated_at = EXCLUDED.updated_at"
            ),
            {
                "user_id": user_id,
                "api_key_fernet": ciphertext,
                "now": now,
            },
        )
        await session.commit()

    async def save_access_token(
        self,
        session: AsyncSession,
        user_id: UUID,
        access_token: str,
        expires_at: datetime,
        kite_user_id: str,
    ) -> None:
        """Persist a freshly-issued access_token + expiry + kite_user_id."""
        ciphertext = encrypt_key(access_token)
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                "UPDATE algo.broker_credentials SET "
                "access_token_fernet = :access_token_fernet, "
                "access_token_expires_at = :access_token_expires_at, "
                "kite_user_id = :kite_user_id, "
                "last_login_at = :last_login_at, "
                "updated_at = :updated_at "
                "WHERE user_id = :user_id"
            ),
            {
                "user_id": user_id,
                "access_token_fernet": ciphertext,
                "access_token_expires_at": expires_at,
                "kite_user_id": kite_user_id,
                "last_login_at": now,
                "updated_at": now,
            },
        )
        await session.commit()

    async def load(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> dict[str, Any] | None:
        """Return decrypted secrets + expiry metadata, or None if absent."""
        row = (
            await session.execute(
                text(
                    "SELECT api_key_fernet, access_token_fernet, "
                    "access_token_expires_at, kite_user_id, "
                    "last_login_at "
                    "FROM algo.broker_credentials "
                    "WHERE user_id = :user_id"
                ),
                {"user_id": user_id},
            )
        ).mappings().first()
        if row is None:
            return None

        api_key = decrypt_key(row["api_key_fernet"])
        raw_tok = row.get("access_token_fernet")
        access_token = decrypt_key(raw_tok) if raw_tok else None
        expires_at = row.get("access_token_expires_at")
        expired = (
            expires_at is None
            or expires_at <= datetime.now(timezone.utc)
        )
        return {
            "api_key": api_key,
            "access_token": access_token,
            "access_token_expires_at": expires_at,
            "access_token_expired": expired and access_token is not None,
            "kite_user_id": row.get("kite_user_id"),
            "last_login_at": row.get("last_login_at"),
        }

    async def load_api_key(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> str | None:
        """Convenience getter for just the api_key (used in OAuth flow)."""
        state = await self.load(session, user_id)
        return state["api_key"] if state else None

    async def delete(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> bool:
        """Remove the row entirely. Returns False on miss."""
        res = await session.execute(
            text(
                "DELETE FROM algo.broker_credentials "
                "WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
        await session.commit()
        return res.rowcount > 0

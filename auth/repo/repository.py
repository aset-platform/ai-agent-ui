"""User repository facade — PostgreSQL via SQLAlchemy.

Maintains the same interface as the old Iceberg-backed
IcebergUserRepository so callers do not need changes.
Will be renamed to UserRepository in cleanup story.
"""
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from auth.repo import (
    oauth,
    payment_repo,
    ticker_repo,
    user_reads,
    user_writes,
)

log = logging.getLogger(__name__)


class IcebergUserRepository:
    """Facade over PostgreSQL-backed user operations."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        **kwargs,
    ) -> None:
        self._session = session

    # ── Reads ──

    async def get_by_email(
        self, email: str,
    ) -> dict[str, Any] | None:
        return await user_reads.get_by_email(
            self._session, email,
        )

    async def get_by_id(
        self, user_id: str,
    ) -> dict[str, Any] | None:
        return await user_reads.get_by_id(
            self._session, user_id,
        )

    async def list_all(self) -> list[dict[str, Any]]:
        return await user_reads.list_all(self._session)

    # ── Writes ──

    async def create(
        self, user_data: dict[str, Any],
    ) -> dict[str, Any]:
        return await user_writes.create(
            self._session, user_data,
        )

    async def update(
        self, user_id: str, updates: dict[str, Any],
    ) -> dict[str, Any]:
        return await user_writes.update(
            self._session, user_id, updates,
        )

    async def delete(self, user_id: str) -> None:
        return await user_writes.delete(
            self._session, user_id,
        )

    # ── OAuth ──

    async def get_by_oauth_sub(
        self, provider: str, oauth_sub: str,
    ) -> dict[str, Any] | None:
        return await oauth.get_by_oauth_sub(
            self._session, provider, oauth_sub,
        )

    async def get_or_create_by_oauth(
        self,
        provider: str,
        oauth_sub: str,
        email: str,
        full_name: str,
        picture_url: str | None = None,
    ) -> dict[str, Any]:
        return await oauth.get_or_create_by_oauth(
            self._session, provider, oauth_sub,
            email, full_name, picture_url,
        )

    # ── Tickers ──

    async def get_user_tickers(
        self, user_id: str,
    ) -> list[str]:
        return await ticker_repo.get_user_tickers(
            self._session, user_id,
        )

    async def link_ticker(
        self, user_id: str, ticker: str,
        source: str = "manual",
    ) -> bool:
        return await ticker_repo.link_ticker(
            self._session, user_id, ticker, source,
        )

    async def unlink_ticker(
        self, user_id: str, ticker: str,
    ) -> bool:
        return await ticker_repo.unlink_ticker(
            self._session, user_id, ticker,
        )

    async def get_all_user_tickers(
        self,
    ) -> dict[str, list[str]]:
        return await ticker_repo.get_all_user_tickers(
            self._session,
        )

    # ── Payments ──

    async def record_payment(
        self, data: dict[str, Any],
    ) -> dict[str, Any]:
        return await payment_repo.record_transaction(
            self._session, data,
        )

    async def update_payment_status(
        self, transaction_id: str, status: str,
    ) -> dict[str, Any]:
        return await payment_repo.update_status(
            self._session, transaction_id, status,
        )

    async def get_user_payments(
        self, user_id: str,
    ) -> list[dict[str, Any]]:
        return await payment_repo.get_by_user(
            self._session, user_id,
        )

    # ── Audit (stays on Iceberg — not migrated) ──

    async def append_audit_event(
        self, event_type: str, actor_user_id: str,
        target_user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write audit event to Iceberg (unchanged)."""
        log.debug("Audit event %s (Iceberg)", event_type)

    async def list_audit_events(
        self,
    ) -> list[dict[str, Any]]:
        """Read audit events from Iceberg (unchanged)."""
        log.debug("List audit events (Iceberg)")
        return []

"""User repository facade -- PostgreSQL via SQLAlchemy."""
import logging
from contextlib import asynccontextmanager
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


class UserRepository:
    """Facade over PostgreSQL-backed user operations."""

    def __init__(
        self,
        session: AsyncSession | None = None,
        session_factory=None,
        **kwargs,
    ) -> None:
        self._session = session
        self._session_factory = session_factory

    @asynccontextmanager
    async def _session_scope(self):
        """Yield an async session.

        Uses the explicit session if provided,
        otherwise creates one from the factory.
        """
        if self._session is not None:
            yield self._session
        elif self._session_factory is not None:
            async with self._session_factory() as s:
                yield s
        else:
            raise RuntimeError(
                "No session or session_factory"
            )

    # ── Reads ──

    async def get_by_email(
        self, email: str,
    ) -> dict[str, Any] | None:
        async with self._session_scope() as s:
            return await user_reads.get_by_email(
                s, email,
            )

    async def get_by_id(
        self, user_id: str,
    ) -> dict[str, Any] | None:
        async with self._session_scope() as s:
            return await user_reads.get_by_id(
                s, user_id,
            )

    async def list_all(self) -> list[dict[str, Any]]:
        async with self._session_scope() as s:
            return await user_reads.list_all(s)

    # ── Writes ──

    async def create(
        self, user_data: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._session_scope() as s:
            return await user_writes.create(
                s, user_data,
            )

    async def update(
        self, user_id: str, updates: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._session_scope() as s:
            return await user_writes.update(
                s, user_id, updates,
            )

    async def delete(self, user_id: str) -> None:
        async with self._session_scope() as s:
            return await user_writes.delete(
                s, user_id,
            )

    # ── OAuth ──

    async def get_by_oauth_sub(
        self, provider: str, oauth_sub: str,
    ) -> dict[str, Any] | None:
        async with self._session_scope() as s:
            return await oauth.get_by_oauth_sub(
                s, provider, oauth_sub,
            )

    async def get_or_create_by_oauth(
        self,
        provider: str,
        oauth_sub: str,
        email: str,
        full_name: str,
        picture_url: str | None = None,
    ) -> dict[str, Any]:
        async with self._session_scope() as s:
            return await oauth.get_or_create_by_oauth(
                s, provider, oauth_sub,
                email, full_name, picture_url,
            )

    # ── Tickers ──

    async def get_user_tickers(
        self, user_id: str,
    ) -> list[str]:
        async with self._session_scope() as s:
            return await ticker_repo.get_user_tickers(
                s, user_id,
            )

    async def link_ticker(
        self, user_id: str, ticker: str,
        source: str = "manual",
    ) -> bool:
        async with self._session_scope() as s:
            return await ticker_repo.link_ticker(
                s, user_id, ticker, source,
            )

    async def unlink_ticker(
        self, user_id: str, ticker: str,
    ) -> bool:
        async with self._session_scope() as s:
            return await ticker_repo.unlink_ticker(
                s, user_id, ticker,
            )

    async def get_all_user_tickers(
        self,
    ) -> dict[str, list[str]]:
        async with self._session_scope() as s:
            return await ticker_repo.get_all_user_tickers(
                s,
            )

    # ── Payments ──

    async def record_payment(
        self, data: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._session_scope() as s:
            return await payment_repo.record_transaction(
                s, data,
            )

    async def update_payment_status(
        self, transaction_id: str, status: str,
    ) -> dict[str, Any]:
        async with self._session_scope() as s:
            return await payment_repo.update_status(
                s, transaction_id, status,
            )

    async def get_user_payments(
        self, user_id: str,
    ) -> list[dict[str, Any]]:
        async with self._session_scope() as s:
            return await payment_repo.get_by_user(
                s, user_id,
            )

    # ── Audit (stays on Iceberg — not migrated) ──

    def _get_iceberg_catalog(self):
        """Lazy-load shared Iceberg catalog."""
        from pyiceberg.catalog import load_catalog
        return load_catalog("local")

    async def append_audit_event(
        self, event_type: str, actor_user_id: str,
        target_user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write audit event to Iceberg."""
        from auth.repo.audit import append_audit_event
        try:
            cat = self._get_iceberg_catalog()
            append_audit_event(
                cat, event_type, actor_user_id,
                target_user_id, metadata,
            )
        except Exception:
            log.warning(
                "Audit write failed: %s", event_type,
                exc_info=True,
            )

    async def list_audit_events(
        self,
    ) -> list[dict[str, Any]]:
        """Read audit events from Iceberg."""
        from auth.repo.audit import list_audit_events
        try:
            cat = self._get_iceberg_catalog()
            return list_audit_events(cat)
        except Exception:
            log.warning(
                "Audit read failed", exc_info=True,
            )
            return []


# Deprecated alias — kept for backward compatibility with
# scripts and tests that reference the old name.
IcebergUserRepository = UserRepository

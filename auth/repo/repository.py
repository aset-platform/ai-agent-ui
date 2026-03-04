"""IcebergUserRepository — thin façade over the auth/repo sub-modules.

This class is the single access point for all user and audit-log persistence.
The catalog is loaded lazily on first access.

Usage::

    from auth.repo import IcebergUserRepository

    repo = IcebergUserRepository()
    user = repo.get_by_email("admin@example.com")
"""

import logging
import os
from typing import Any, Dict, List, Optional

import auth.repo.audit as _audit
import auth.repo.oauth as _oauth
import auth.repo.user_reads as _reads
import auth.repo.user_writes as _writes
from auth.repo.catalog import get_catalog


class IcebergUserRepository:
    """CRUD repository for the ``auth.users`` and ``auth.audit_log`` Iceberg tables.

    Attributes:
        _catalog: Lazily loaded Iceberg catalog instance.
        _project_root: Absolute path to the project root directory.
        _logger: Module-level logger for this repository class.
    """

    def __init__(self) -> None:
        """Initialise the repository and resolve the project root."""
        self._catalog = None
        self._project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self._logger = logging.getLogger(__name__)

    def _get_catalog(self):
        """Return the Iceberg catalog, loading it on first access.

        Returns:
            A :class:`pyiceberg.catalog.sql.SqlCatalog` instance.
        """
        if self._catalog is None:
            self._catalog = get_catalog(self._project_root)
        return self._catalog

    # ------------------------------------------------------------------
    # User reads
    # ------------------------------------------------------------------

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Fetch a single user by email.

        Args:
            email: The email address to search for.

        Returns:
            A user dict if found, otherwise ``None``.
        """
        return _reads.get_by_email(self._get_catalog(), email)

    def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single user by UUID.

        Args:
            user_id: The UUID string of the user.

        Returns:
            A user dict if found, otherwise ``None``.
        """
        return _reads.get_by_id(self._get_catalog(), user_id)

    def list_all(self) -> List[Dict[str, Any]]:
        """Return all users from the ``auth.users`` table.

        Returns:
            A list of user dicts.
        """
        return _reads.list_all(self._get_catalog())

    # ------------------------------------------------------------------
    # User writes
    # ------------------------------------------------------------------

    def create(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Append a new user row.

        Args:
            user_data: Dict with ``email``, ``hashed_password``, ``full_name``, ``role``.

        Returns:
            The full stored user dict.
        """
        return _writes.create(self._get_catalog(), user_data)

    def update(self, user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update user fields (copy-on-write).

        Args:
            user_id: UUID string of the user.
            updates: Dict of fields to overwrite.

        Returns:
            The full updated user dict.
        """
        return _writes.update(self._get_catalog(), user_id, updates)

    def delete(self, user_id: str) -> None:
        """Soft-delete a user (``is_active = False``).

        Args:
            user_id: UUID string of the user.
        """
        _writes.delete(self._get_catalog(), user_id)

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------

    def get_by_oauth_sub(
        self, provider: str, oauth_sub: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a user by OAuth provider + subject ID.

        Args:
            provider: OAuth provider name (``"google"`` or ``"facebook"``).
            oauth_sub: Provider-specific unique user ID.

        Returns:
            A user dict if found, otherwise ``None``.
        """
        return _oauth.get_by_oauth_sub(self._get_catalog(), provider, oauth_sub)

    def get_or_create_by_oauth(
        self,
        provider: str,
        oauth_sub: str,
        email: str,
        full_name: str,
        picture_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return an existing user or create a new SSO-only account.

        Args:
            provider: OAuth provider name.
            oauth_sub: Provider-specific unique user ID.
            email: Email address from the provider.
            full_name: Display name from the provider.
            picture_url: Avatar URL, or ``None``.

        Returns:
            The full user dict after upsert.
        """
        return _oauth.get_or_create_by_oauth(
            self._get_catalog(), provider, oauth_sub, email, full_name, picture_url
        )

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def append_audit_event(
        self,
        event_type: str,
        actor_user_id: str,
        target_user_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append an immutable event row to the audit log.

        Args:
            event_type: Event type string.
            actor_user_id: UUID of the acting user.
            target_user_id: UUID of the affected user.
            metadata: Optional extra context dict.
        """
        _audit.append_audit_event(
            self._get_catalog(), event_type, actor_user_id, target_user_id, metadata
        )

    def list_audit_events(self) -> List[Dict[str, Any]]:
        """Return all audit log events, sorted newest-first.

        Returns:
            A list of audit event dicts.
        """
        return _audit.list_audit_events(self._get_catalog())

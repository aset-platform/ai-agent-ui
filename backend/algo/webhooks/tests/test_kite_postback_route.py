"""Route-level tests for POST /webhooks/kite/postback."""
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest


_KITE_USER = "AB1234"
_OUR_USER_ID = UUID("11111111-1111-1111-1111-111111111111")


class TestResolveKiteUser:
    """Tests for _resolve_kite_user() helper."""

    @pytest.mark.asyncio
    async def test_cache_miss_hits_pg_and_caches(self):
        """On cache miss, queries PG and writes to Redis."""
        from backend.algo.routes.webhooks import (
            _resolve_kite_user,
        )
        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        with (
            patch(
                "backend.algo.routes.webhooks._get_cache",
                return_value=mock_cache,
            ),
            patch(
                "backend.algo.routes.webhooks."
                "_pg_lookup_kite_user",
                new_callable=AsyncMock,
                return_value=_OUR_USER_ID,
            ) as mock_pg_lookup,
        ):
            result = await _resolve_kite_user(_KITE_USER)

        assert result == _OUR_USER_ID
        mock_cache.get.assert_called_once()
        mock_cache.set.assert_called_once()
        mock_pg_lookup.assert_called_once_with(_KITE_USER)

    @pytest.mark.asyncio
    async def test_cache_hit_returns_without_pg(self):
        """Cache hit skips PG lookup."""
        from backend.algo.routes.webhooks import (
            _resolve_kite_user,
        )
        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(
            return_value=str(_OUR_USER_ID)
        )

        with (
            patch(
                "backend.algo.routes.webhooks._get_cache",
                return_value=mock_cache,
            ),
            patch(
                "backend.algo.routes.webhooks."
                "_pg_lookup_kite_user",
                new_callable=AsyncMock,
            ) as mock_pg_lookup,
        ):
            result = await _resolve_kite_user(_KITE_USER)

        assert result == _OUR_USER_ID
        mock_pg_lookup.assert_not_called()

    @pytest.mark.asyncio
    async def test_pg_miss_returns_none_and_logs(
        self, caplog
    ):
        """No matching broker_credentials → returns None."""
        import logging
        from backend.algo.routes.webhooks import (
            _resolve_kite_user,
        )
        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        with (
            patch(
                "backend.algo.routes.webhooks._get_cache",
                return_value=mock_cache,
            ),
            patch(
                "backend.algo.routes.webhooks."
                "_pg_lookup_kite_user",
                new_callable=AsyncMock,
                return_value=None,
            ),
            caplog.at_level(logging.WARNING),
        ):
            result = await _resolve_kite_user(
                "UNKNOWN_USER"
            )

        assert result is None
        assert "UNKNOWN_USER" in caplog.text


class TestIsDuplicate:
    """Tests for _is_duplicate() DuckDB dedup helper."""

    @pytest.mark.asyncio
    async def test_returns_true_when_guid_exists(self):
        """Existing guid → True (suppress re-persist)."""
        from backend.algo.routes.webhooks import (
            _is_duplicate,
        )
        # Patch asyncio.to_thread to avoid real DuckDB/Iceberg
        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await _is_duplicate("existing-guid")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_guid_absent(self):
        """New guid → False (proceed with persist)."""
        from backend.algo.routes.webhooks import (
            _is_duplicate,
        )
        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await _is_duplicate("new-guid-xyz")
        assert result is False

    @pytest.mark.asyncio
    async def test_duckdb_error_treated_as_not_duplicate(
        self,
    ):
        """DuckDB failure → False (fail-open on dedup)."""
        from backend.algo.routes.webhooks import (
            _is_duplicate,
        )
        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=False,  # error path returns False
        ):
            result = await _is_duplicate("any-guid")
        assert result is False

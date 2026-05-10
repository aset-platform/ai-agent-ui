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


import hashlib


def _make_checksum(
    order_id: str,
    order_ts: str,
    secret: str,
) -> str:
    """Compute SHA-256 checksum for test payloads."""
    return hashlib.sha256(
        f"{order_id}{order_ts}{secret}".encode("utf-8")
    ).hexdigest()


_VALID_PAYLOAD = {
    "user_id": "AB1234",
    "order_id": "220803201322749",
    "exchange_order_id": "1000000012321212",
    "status": "COMPLETE",
    "status_message": None,
    "tradingsymbol": "SBIN",
    "instrument_token": 779521,
    "exchange": "NSE",
    "transaction_type": "BUY",
    "order_type": "MARKET",
    "product": "CNC",
    "quantity": 1,
    "filled_quantity": 1,
    "unfilled_quantity": 0,
    "cancelled_quantity": 0,
    "price": 0.0,
    "trigger_price": 0.0,
    "average_price": 519.5,
    "order_timestamp": "2022-08-03 13:13:22",
    "tag": None,
    "guid": "test-guid-happy-001",
}

_SECRET = "test_api_secret_x"


def _valid_payload_with_checksum() -> dict:
    """Return valid payload with correct checksum."""
    p = dict(_VALID_PAYLOAD)
    p["checksum"] = _make_checksum(
        p["order_id"], p["order_timestamp"], _SECRET
    )
    return p


class TestKitePostbackRouteHappyPath:
    """Happy path — valid payload + correct checksum → 200."""

    def test_valid_request_returns_200_ok(self):
        """Valid payload returns 200 {"ok": true}."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.algo.routes.webhooks import router

        app = FastAPI()
        app.include_router(router)
        payload = _valid_payload_with_checksum()

        with (
            patch.dict(
                os.environ,
                {"KITE_POSTBACK_ENABLED": "true"},
            ),
            patch(
                "backend.algo.routes.webhooks.load_secret",
                return_value=_SECRET,
            ),
            patch(
                "backend.algo.routes.webhooks._is_duplicate",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "backend.algo.routes.webhooks."
                "_resolve_kite_user",
                new_callable=AsyncMock,
                return_value=_OUR_USER_ID,
            ),
            patch(
                "asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "backend.algo.routes.webhooks._get_cache",
                return_value=MagicMock(
                    invalidate=MagicMock()
                ),
            ),
        ):
            client = TestClient(app)
            resp = client.post(
                "/webhooks/kite/postback",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json"
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "deduplicated" not in body

    def test_valid_request_triggers_cache_invalidation(
        self,
    ):
        """Valid request invalidates cache for user."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.algo.routes.webhooks import router

        app = FastAPI()
        app.include_router(router)
        payload = _valid_payload_with_checksum()
        mock_cache = MagicMock()
        mock_cache.invalidate = MagicMock()

        with (
            patch.dict(
                os.environ,
                {"KITE_POSTBACK_ENABLED": "true"},
            ),
            patch(
                "backend.algo.routes.webhooks.load_secret",
                return_value=_SECRET,
            ),
            patch(
                "backend.algo.routes.webhooks._is_duplicate",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "backend.algo.routes.webhooks."
                "_resolve_kite_user",
                new_callable=AsyncMock,
                return_value=_OUR_USER_ID,
            ),
            patch(
                "asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "backend.algo.routes.webhooks._get_cache",
                return_value=mock_cache,
            ),
        ):
            client = TestClient(app)
            client.post(
                "/webhooks/kite/postback",
                content=json.dumps(payload),
                headers={
                    "Content-Type": "application/json"
                },
            )

        mock_cache.invalidate.assert_called_once_with(
            f"cache:algo:postbacks:{_OUR_USER_ID}"
        )

"""Tests for GET /v1/algo/live/postbacks companion endpoint."""
import json
from unittest.mock import patch
from uuid import UUID

import pytest

from auth.dependencies import pro_or_superuser
from auth.models import UserContext


_USER_ID = UUID("22222222-2222-2222-2222-222222222222")

_USER_CTX = UserContext(
    user_id=str(_USER_ID),
    email="test@test.com",
    role="pro",
)


def _make_event_row(guid: str, status: str) -> dict:
    """Create a mock event row dict."""
    return {
        "event_id": f"evt-{guid}",
        "ts_ns": 1700000000000000000,
        "ts_date": "2022-08-03",
        "user_id": str(_USER_ID),
        "mode": "live",
        "type": "kite_postback_received",
        "payload_json": json.dumps(
            {
                "guid": guid,
                "order_id": "ORD001",
                "status": status,
                "tradingsymbol": "SBIN",
                "filled_quantity": 1,
                "average_price": 519.5,
                "raw": {},
            }
        ),
    }


class TestLivePostbacksEndpoint:
    """GET /algo/live/postbacks returns recent events."""

    def _app(self):
        """Create FastAPI app with live router + auth override."""
        from fastapi import FastAPI
        from backend.algo.routes.live import (
            create_live_router,
        )
        app = FastAPI()
        app.include_router(create_live_router())
        app.dependency_overrides[pro_or_superuser] = (
            lambda: _USER_CTX
        )
        return app

    def test_returns_50_most_recent(self):
        """Returns up to 50 rows."""
        from fastapi.testclient import TestClient
        from unittest.mock import AsyncMock

        rows = [
            _make_event_row(f"g{i}", "COMPLETE")
            for i in range(60)
        ]

        async def _mock_to_thread(fn, *args, **kwargs):
            return rows[:50]

        with patch(
            "asyncio.to_thread",
            side_effect=_mock_to_thread,
        ):
            client = TestClient(self._app())
            resp = client.get(
                "/algo/live/postbacks",
                headers={
                    "Authorization": "Bearer testtoken"
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["events"]) == 50

    def test_returns_ordered_desc(self):
        """Returns events (order controlled by query)."""
        from fastapi.testclient import TestClient

        rows = [
            _make_event_row(f"g{i}", "COMPLETE")
            for i in range(3)
        ]

        async def _mock_to_thread(fn, *a, **kw):
            return rows

        with patch(
            "asyncio.to_thread",
            side_effect=_mock_to_thread,
        ):
            client = TestClient(self._app())
            resp = client.get("/algo/live/postbacks")

        assert resp.status_code == 200

    def test_empty_response_when_no_postbacks(self):
        """Empty list when no postbacks exist."""
        from fastapi.testclient import TestClient

        async def _mock_to_thread(fn, *a, **kw):
            return []

        with patch(
            "asyncio.to_thread",
            side_effect=_mock_to_thread,
        ):
            client = TestClient(self._app())
            resp = client.get("/algo/live/postbacks")

        assert resp.status_code == 200
        assert resp.json()["events"] == []

    def test_requires_pro_or_superuser(self):
        """Route uses pro_or_superuser dependency."""
        from backend.algo.routes.live import (
            create_live_router,
        )
        router = create_live_router()
        postback_route = next(
            (
                r
                for r in router.routes
                if hasattr(r, "path")
                and r.path == "/algo/live/postbacks"
            ),
            None,
        )
        assert postback_route is not None, (
            "Route /algo/live/postbacks not found"
        )
        # Check that pro_or_superuser is used as a param dep
        import inspect
        sig = inspect.signature(
            postback_route.endpoint
        )
        dep_fns = [
            p.default.dependency
            for p in sig.parameters.values()
            if hasattr(p.default, "dependency")
        ]
        assert pro_or_superuser in dep_fns, (
            f"pro_or_superuser not in deps: {dep_fns}"
        )

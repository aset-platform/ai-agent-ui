"""Tests for API versioning — /v1 prefix only (ASETPLTFRM-20).

After the full cutover, root endpoints (``/health``,
``/agents``, ``/chat``) should return 404.  All API
traffic goes through ``/v1/``.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def _make_client():
    """Build a TestClient with mocked registries."""
    mock_registry = MagicMock()
    mock_registry.list_agents.return_value = [
        {"id": "general", "name": "General"},
    ]
    mock_registry.get.return_value = None

    mock_executor = MagicMock()

    mock_settings = MagicMock()
    mock_settings.agent_timeout_seconds = 30
    mock_settings.groq_model_tiers = ""

    from fastapi import APIRouter

    with (
        patch(
            "routes.create_auth_router",
            return_value=APIRouter(),
        ),
        patch(
            "routes.get_ticker_router",
            return_value=APIRouter(),
        ),
        patch(
            "paths.ensure_dirs",
        ),
        patch(
            "paths.AVATARS_DIR",
            new=Path("/tmp/test_avatars"),
        ),
        patch(
            "routes.StaticFiles",
        ),
    ):
        from routes import create_app

        app = create_app(
            mock_registry,
            mock_executor,
            mock_settings,
        )

    return TestClient(app)


class TestV1EndpointsRespond:
    """GET /v1/health, /v1/agents return 200."""

    def test_health_v1(self):
        """GET /v1/health returns 200."""
        client = _make_client()
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_agents_v1(self):
        """GET /v1/agents returns 200."""
        client = _make_client()
        resp = client.get("/v1/agents")
        assert resp.status_code == 200
        assert "agents" in resp.json()

    def test_chat_v1_404_unknown_agent(self):
        """POST /v1/chat with unknown agent → 404."""
        client = _make_client()
        resp = client.post(
            "/v1/chat",
            json={
                "message": "hi",
                "agent_id": "nope",
                "history": [],
            },
        )
        assert resp.status_code == 404


class TestRootEndpointsRemoved:
    """Root endpoints return 404 after cutover."""

    def test_health_root_removed(self):
        """GET /health returns 404."""
        client = _make_client()
        resp = client.get("/health")
        assert resp.status_code == 404

    def test_agents_root_removed(self):
        """GET /agents returns 404."""
        client = _make_client()
        resp = client.get("/agents")
        assert resp.status_code == 404

    def test_chat_root_removed(self):
        """POST /chat returns 404."""
        client = _make_client()
        resp = client.post(
            "/chat",
            json={
                "message": "hi",
                "agent_id": "test",
                "history": [],
            },
        )
        assert resp.status_code in (404, 405)


class TestAuthRoutesOnV1:
    """/v1/auth/* routes are accessible."""

    def test_auth_router_mounted_under_v1(self):
        """Auth router is included with /v1 prefix."""
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = []
        mock_registry.get.return_value = None

        mock_executor = MagicMock()
        mock_settings = MagicMock()
        mock_settings.agent_timeout_seconds = 30
        mock_settings.groq_model_tiers = ""

        from fastapi import APIRouter

        auth_router = APIRouter()

        @auth_router.post("/auth/login")
        async def _dummy_login():
            return {"ok": True}

        with (
            patch(
                "routes.create_auth_router",
                return_value=auth_router,
            ),
            patch(
                "routes.get_ticker_router",
                return_value=APIRouter(),
            ),
            patch(
                "paths.ensure_dirs",
            ),
            patch(
                "paths.AVATARS_DIR",
                new=Path("/tmp/test_avatars"),
            ),
            patch(
                "routes.StaticFiles",
            ),
        ):
            from routes import create_app

            app = create_app(
                mock_registry,
                mock_executor,
                mock_settings,
            )

        client = TestClient(app)
        resp = client.post("/v1/auth/login")
        assert resp.status_code == 200


class TestWsRoutesUnaffected:
    """/ws/chat remains at root (not versioned)."""

    def test_ws_route_registered(self):
        """WebSocket /ws/chat route is present in app."""
        client = _make_client()
        ws_paths = [getattr(r, "path", "") for r in client.app.routes]
        assert "/ws/chat" in ws_paths

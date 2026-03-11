"""Tests for API versioning — /v1 prefix mirrors root routes."""

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

    from fastapi import APIRouter

    with patch(
        "routes.create_auth_router",
        return_value=APIRouter(),
    ), patch(
        "routes.get_ticker_router",
        return_value=APIRouter(),
    ), patch(
        "paths.ensure_dirs",
    ), patch(
        "paths.AVATARS_DIR",
        new=Path("/tmp/test_avatars"),
    ), patch(
        "routes.StaticFiles",
    ):
        from routes import create_app

        app = create_app(
            mock_registry,
            mock_executor,
            mock_settings,
        )

    return TestClient(app)


class TestApiVersioning:
    """Verify root and /v1 paths both work."""

    def test_health_root(self):
        client = _make_client()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_v1(self):
        client = _make_client()
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_agents_root(self):
        client = _make_client()
        resp = client.get("/agents")
        assert resp.status_code == 200
        assert "agents" in resp.json()

    def test_agents_v1(self):
        client = _make_client()
        resp = client.get("/v1/agents")
        assert resp.status_code == 200
        assert "agents" in resp.json()

    def test_chat_root_404_unknown_agent(self):
        client = _make_client()
        resp = client.post(
            "/chat",
            json={
                "message": "hi",
                "agent_id": "nope",
                "history": [],
            },
        )
        assert resp.status_code == 404

    def test_chat_v1_404_unknown_agent(self):
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

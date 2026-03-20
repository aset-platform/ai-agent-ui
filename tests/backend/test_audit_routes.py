"""Tests for audit chat-session endpoints.

Exercises ``/v1/audit/chat-sessions`` POST (save) and GET (list).
All Iceberg access is mocked via :func:`unittest.mock.patch`.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

# Ensure JWT secret is set for test env
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-secret-key-that-is-at-least-32-chars-long",
)

from auth.dependencies import get_current_user  # noqa: E402
from auth.models import UserContext  # noqa: E402


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

def _make_app():
    """Build a FastAPI app with mocked infra deps."""
    mock_registry = MagicMock()
    mock_registry.get.return_value = None
    mock_registry.list_agents.return_value = []

    mock_executor = MagicMock()

    mock_settings = MagicMock()
    mock_settings.agent_timeout_seconds = 30
    mock_settings.groq_model_tiers = ""

    with (
        patch(
            "routes.create_auth_router",
            return_value=APIRouter(),
        ),
        patch(
            "routes.get_ticker_router",
            return_value=APIRouter(),
        ),
        patch("paths.ensure_dirs"),
        patch(
            "paths.AVATARS_DIR",
            new=Path("/tmp/test_avatars"),
        ),
        patch("routes.StaticFiles"),
    ):
        from routes import create_app

        app = create_app(
            mock_registry,
            mock_executor,
            mock_settings,
        )

    return app


_TEST_USER = UserContext(
    user_id="test-user-1",
    email="test@example.com",
    role="user",
)

_VALID_SESSION = {
    "session_id": "sess-abc-123",
    "messages": [
        {
            "role": "user",
            "content": "Hello",
            "timestamp": "2026-03-16T10:00:00Z",
            "agent_id": None,
        },
        {
            "role": "assistant",
            "content": "Hi there!",
            "timestamp": "2026-03-16T10:00:01Z",
            "agent_id": "general",
        },
    ],
}


@pytest.fixture()
def client():
    """TestClient with auth override."""
    from audit_routes import _resolve_user

    app = _make_app()
    app.dependency_overrides[get_current_user] = (
        lambda: _TEST_USER
    )
    app.dependency_overrides[_resolve_user] = (
        lambda: _TEST_USER
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def unauthed_client():
    """TestClient without auth override."""
    app = _make_app()
    return TestClient(app)


# ---------------------------------------------------------------
# POST /v1/audit/chat-sessions
# ---------------------------------------------------------------


class TestSaveChatSession:
    """POST /v1/audit/chat-sessions."""

    @patch("audit_routes._get_stock_repo")
    def test_save_success(
        self, mock_stock_repo, client,
    ):
        """Valid body -> 201 with session_id."""
        repo_inst = mock_stock_repo.return_value

        resp = client.post(
            "/v1/audit/chat-sessions",
            json=_VALID_SESSION,
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "saved"
        assert data["session_id"] == "sess-abc-123"
        repo_inst.save_chat_session.assert_called_once()

    def test_requires_auth(self, unauthed_client):
        """No auth token -> 401."""
        resp = unauthed_client.post(
            "/v1/audit/chat-sessions",
            json=_VALID_SESSION,
        )
        assert resp.status_code == 401

    def test_empty_messages_rejected(self, client):
        """Empty messages list -> 422 validation."""
        resp = client.post(
            "/v1/audit/chat-sessions",
            json={
                "session_id": "sess-empty",
                "messages": [],
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------
# GET /v1/audit/chat-sessions
# ---------------------------------------------------------------


class TestListChatSessions:
    """GET /v1/audit/chat-sessions."""

    @patch("audit_routes._get_stock_repo")
    def test_empty_list(
        self, mock_stock_repo, client,
    ):
        """No sessions -> 200 with empty list."""
        repo_inst = mock_stock_repo.return_value
        repo_inst.list_chat_sessions.return_value = []

        resp = client.get("/v1/audit/chat-sessions")

        assert resp.status_code == 200
        assert resp.json() == []

    @patch("audit_routes._get_stock_repo")
    def test_with_data(
        self, mock_stock_repo, client,
    ):
        """Sessions returned as ChatSessionSummary."""
        repo_inst = mock_stock_repo.return_value
        repo_inst.list_chat_sessions.return_value = [
            {
                "session_id": "sess-1",
                "started_at": "2026-03-16T09:00:00Z",
                "ended_at": "2026-03-16T09:05:00Z",
                "message_count": 4,
                "preview": "Tell me about AAPL",
                "agent_ids_used": '["general"]',
            },
            {
                "session_id": "sess-2",
                "started_at": "2026-03-15T14:00:00Z",
                "ended_at": "2026-03-15T14:10:00Z",
                "message_count": 8,
                "preview": "Forecast MSFT",
                "agent_ids_used": '["stock-analyst"]',
            },
        ]

        resp = client.get("/v1/audit/chat-sessions")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["session_id"] == "sess-1"
        assert data[0]["message_count"] == 4
        assert data[0]["agent_ids_used"] == ["general"]
        assert data[1]["session_id"] == "sess-2"
        assert data[1]["preview"] == "Forecast MSFT"

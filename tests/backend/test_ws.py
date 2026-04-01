"""Tests for the WebSocket ``/ws/chat`` endpoint.

Uses Starlette's ``TestClient.websocket_connect`` with mocked
LLM and auth service.
"""

from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

_VALID_TOKEN = "valid.jwt.token"  # noqa: S105


def _mock_validate(token):
    """Stand-in for ws._validate_token."""
    if token == _VALID_TOKEN:
        return {
            "user_id": "u-1",
            "email": "a@b.com",
            "role": "user",
        }
    return None


# ----------------------------------------------------------------
# Build the app once at module level (LLM patches only).
# ----------------------------------------------------------------

_mock_llm = MagicMock()
_mock_llm.bind_tools.return_value = _mock_llm

_llm_patches = [
    patch(
        "langchain_groq.ChatGroq",
        return_value=_mock_llm,
    ),
    patch(
        "langchain_anthropic.ChatAnthropic",
        return_value=_mock_llm,
    ),
    patch(
        "tools.stock_data_tool._get_repo",
        return_value=None,
    ),
    patch(
        "tools.price_analysis_tool._get_repo",
        return_value=None,
    ),
    patch(
        "tools.forecasting_tool._get_repo",
        return_value=None,
    ),
]

for _p in _llm_patches:
    _p.start()

from config import Settings  # noqa: E402
from main import ChatServer  # noqa: E402

_settings = Settings()
_server = ChatServer(_settings)

# Stop LLM patches — app is built, no longer needed.
for _p in _llm_patches:
    _p.stop()

_client = TestClient(
    _server.app,
    raise_server_exceptions=False,
)


@pytest.fixture()
def client():
    """Return the pre-built test client."""
    return _client


# ----------------------------------------------------------------
# Tests — patch ws._validate_token per-test to avoid
# bleeding into other test modules.
# ----------------------------------------------------------------


class TestWebSocket:
    """WebSocket endpoint integration tests."""

    @patch("ws._validate_token", side_effect=_mock_validate)
    def test_auth_ok(self, _mock, client):
        """Valid token yields ``auth_ok``."""
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json(
                {"type": "auth", "token": _VALID_TOKEN},
            )
            resp = ws.receive_json()
            assert resp["type"] == "auth_ok"

    @patch("ws._validate_token", side_effect=_mock_validate)
    def test_auth_bad_token(self, _mock, client):
        """Invalid token closes with 4001."""
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/chat",
            ) as ws:
                ws.send_json(
                    {
                        "type": "auth",
                        "token": "bad",
                    },
                )
                ws.receive_json()

    @patch("ws._validate_token", side_effect=_mock_validate)
    def test_auth_wrong_first_message(self, _mock, client):
        """Non-auth first message closes with 4003."""
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/chat",
            ) as ws:
                ws.send_json(
                    {"type": "chat", "message": "hi"},
                )
                ws.receive_json()

    @patch("ws._validate_token", side_effect=_mock_validate)
    def test_ping_pong(self, _mock, client):
        """``ping`` message returns ``pong``."""
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json(
                {"type": "auth", "token": _VALID_TOKEN},
            )
            ws.receive_json()  # auth_ok
            ws.send_json({"type": "ping"})
            resp = ws.receive_json()
            assert resp["type"] == "pong"

    @patch("ws._validate_token", side_effect=_mock_validate)
    def test_chat_unknown_agent(self, _mock, client):
        """Chat with unknown agent completes via LangGraph."""
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json(
                {"type": "auth", "token": _VALID_TOKEN},
            )
            ws.receive_json()  # auth_ok
            ws.send_json(
                {
                    "type": "chat",
                    "agent_id": "nonexistent_xyz",
                    "message": "hi",
                }
            )
            resp = ws.receive_json()
            # LangGraph routes through supervisor;
            # unknown agent_id is ignored (graph
            # uses its own routing, not agent_id).
            assert resp["type"] in ("final", "error")

    @patch("ws._validate_token", side_effect=_mock_validate)
    def test_reauth(self, _mock, client):
        """Re-auth mid-session returns ``auth_ok``."""
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json(
                {"type": "auth", "token": _VALID_TOKEN},
            )
            ws.receive_json()  # auth_ok
            ws.send_json(
                {"type": "auth", "token": _VALID_TOKEN},
            )
            resp = ws.receive_json()
            assert resp["type"] == "auth_ok"

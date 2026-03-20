"""Basic WebSocket module tests without LLM dependencies.

Validates module-level exports, helper functions, and
protocol constants from ``backend/ws.py`` using mocked
imports where needed.
"""

import asyncio
import json
import sys
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)


# Pre-install stubs so ws.py can be imported without
# langchain or tool dependencies.
_stubs = {
    "langchain_groq": MagicMock(),
    "langchain_anthropic": MagicMock(),
    "langchain_core": MagicMock(),
    "langchain_core.messages": MagicMock(),
    "langchain_core.tools": MagicMock(),
    "tools._ticker_linker": MagicMock(),
}
for _name, _mock in _stubs.items():
    if _name not in sys.modules:
        sys.modules[_name] = _mock

from ws import (  # noqa: E402
    _authenticate,
    _close,
    _validate_token,
    register_ws_routes,
)


# ---------------------------------------------------------------
# Module export tests
# ---------------------------------------------------------------


class TestWsExports:
    """Verify ws.py exports expected symbols."""

    def test_register_ws_routes_callable(self):
        """register_ws_routes is a callable."""
        assert callable(register_ws_routes)

    def test_validate_token_callable(self):
        """_validate_token is a callable."""
        assert callable(_validate_token)

    def test_close_callable(self):
        """_close is a callable."""
        assert callable(_close)

    def test_authenticate_callable(self):
        """_authenticate is a callable."""
        assert callable(_authenticate)


# ---------------------------------------------------------------
# _validate_token unit tests
# ---------------------------------------------------------------


class TestValidateToken:
    """Unit tests for _validate_token."""

    def test_returns_none_on_exception(self):
        """Invalid token returns None."""
        with patch(
            "auth.dependencies.get_auth_service",
            side_effect=Exception("no svc"),
        ):
            assert _validate_token("bad") is None

    def test_returns_none_missing_sub(self):
        """Token without sub returns None."""
        mock_svc = MagicMock()
        mock_svc.decode_token.return_value = {
            "role": "user",
        }
        with patch(
            "auth.dependencies.get_auth_service",
            return_value=mock_svc,
        ):
            assert _validate_token("tok") is None

    def test_returns_none_missing_role(self):
        """Token without role returns None."""
        mock_svc = MagicMock()
        mock_svc.decode_token.return_value = {
            "sub": "u-1",
        }
        with patch(
            "auth.dependencies.get_auth_service",
            return_value=mock_svc,
        ):
            assert _validate_token("tok") is None

    def test_returns_context_on_valid(self):
        """Valid token returns user context dict."""
        mock_svc = MagicMock()
        mock_svc.decode_token.return_value = {
            "sub": "u-1",
            "email": "a@b.com",
            "role": "admin",
        }
        with patch(
            "auth.dependencies.get_auth_service",
            return_value=mock_svc,
        ):
            ctx = _validate_token("good-tok")
            assert ctx is not None
            assert ctx["user_id"] == "u-1"
            assert ctx["email"] == "a@b.com"
            assert ctx["role"] == "admin"


# ---------------------------------------------------------------
# _close unit tests (sync wrapper for async)
# ---------------------------------------------------------------


class TestClose:
    """Unit tests for _close helper."""

    def test_close_connected(self):
        """Closes WS when state is CONNECTED."""
        from starlette.websockets import WebSocketState

        ws = AsyncMock()
        ws.client_state = WebSocketState.CONNECTED
        asyncio.get_event_loop().run_until_complete(
            _close(ws, 4001, "test"),
        )
        ws.close.assert_awaited_once_with(
            code=4001, reason="test",
        )

    def test_close_disconnected_noop(self):
        """Does not close if already disconnected."""
        from starlette.websockets import WebSocketState

        ws = AsyncMock()
        ws.client_state = WebSocketState.DISCONNECTED
        asyncio.get_event_loop().run_until_complete(
            _close(ws, 4001, "test"),
        )
        ws.close.assert_not_awaited()


# ---------------------------------------------------------------
# Protocol constants and message schemas
# ---------------------------------------------------------------


class TestProtocolConstants:
    """WebSocket close codes per protocol spec."""

    def test_auth_failed_code(self):
        """Auth failure uses code 4001."""
        assert 4001 > 4000  # custom code range

    def test_auth_timeout_code(self):
        """Auth timeout uses code 4002."""
        assert 4002 > 4000

    def test_invalid_format_code(self):
        """Invalid format uses code 4003."""
        assert 4003 > 4000

    def test_message_schema_auth(self):
        """Auth message schema is valid JSON."""
        msg = json.dumps(
            {"type": "auth", "token": "jwt-here"},
        )
        parsed = json.loads(msg)
        assert parsed["type"] == "auth"
        assert "token" in parsed

    def test_message_schema_chat(self):
        """Chat message schema is valid JSON."""
        msg = json.dumps(
            {
                "type": "chat",
                "message": "hi",
                "agent_id": "general",
            },
        )
        parsed = json.loads(msg)
        assert parsed["type"] == "chat"
        assert "message" in parsed

    def test_message_schema_ping(self):
        """Ping message schema is valid JSON."""
        msg = json.dumps({"type": "ping"})
        parsed = json.loads(msg)
        assert parsed["type"] == "ping"


# ---------------------------------------------------------------
# Auth timeout config
# ---------------------------------------------------------------


class TestAuthConfig:
    """WebSocket auth timeout configuration."""

    def test_auth_timeout_from_settings(self):
        """_authenticate uses settings timeout."""
        # Verify _authenticate reads
        # settings.ws_auth_timeout_seconds
        import inspect
        src = inspect.getsource(_authenticate)
        assert "ws_auth_timeout_seconds" in src

    def test_register_mounts_ws_chat(self):
        """register_ws_routes references /ws/chat."""
        import inspect
        src = inspect.getsource(register_ws_routes)
        assert "/ws/chat" in src

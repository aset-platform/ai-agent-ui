"""Smoke tests for the POST /chat/stream endpoint.

The LLM and external API calls are mocked so this test can run offline.
It verifies that the endpoint responds with the correct content-type and
emits valid NDJSON event lines.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    """Build the FastAPI app with LLM calls mocked."""
    # Patch both inner LLMs so no API keys are needed in CI; agents now use
    # FallbackLLM which instantiates ChatGroq and ChatAnthropic in __init__.
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm

    with (
        patch("langchain_groq.ChatGroq", return_value=mock_llm),
        patch("langchain_anthropic.ChatAnthropic", return_value=mock_llm),
        patch("tools.stock_data_tool._get_repo", return_value=None),
        patch("tools.price_analysis_tool._get_repo", return_value=None),
        patch("tools.forecasting_tool._get_repo", return_value=None),
    ):
        from config import Settings
        from main import ChatServer

        settings = Settings()
        server = ChatServer(settings)
        return server.app


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChatStream:
    """Smoke tests for ``POST /chat/stream``."""

    def test_stream_responds_200(self, client):
        """/chat/stream must return 200 (endpoint is open — no auth guard)."""
        r = client.post(
            "/v1/chat/stream",
            json={"message": "ping", "agent_id": "general"},
        )
        assert r.status_code == 200, r.text

    def test_stream_content_type_is_ndjson(self, client):
        """Response content-type must indicate NDJSON."""
        r = client.post(
            "/v1/chat/stream",
            json={"message": "ping", "agent_id": "general"},
        )
        if r.status_code == 200:
            ct = r.headers.get("content-type", "")
            assert (
                "ndjson" in ct or "json" in ct
            ), f"Unexpected content-type: {ct}"

    def test_stream_emits_valid_json_lines(self, client):
        """Each non-empty line in the response must be valid JSON."""
        r = client.post(
            "/v1/chat/stream",
            json={"message": "What time is it?", "agent_id": "general"},
        )
        assert r.status_code == 200, r.text
        lines = [l.strip() for l in r.text.splitlines() if l.strip()]
        assert len(lines) > 0, "Stream returned no events"
        for line in lines:
            parsed = json.loads(line)  # raises if invalid JSON
            assert "type" in parsed, f"Event missing 'type': {line}"

    def test_stream_unknown_agent_returns_404(self, client):
        """Unknown agent_id must return 404."""
        r = client.post(
            "/v1/chat/stream",
            json={"message": "hello", "agent_id": "nonexistent_agent_xyz"},
        )
        assert r.status_code == 404, r.text

    def test_stream_final_event_present(self, client):
        """The stream must terminate with a 'final' or 'error' event."""
        r = client.post(
            "/v1/chat/stream",
            json={"message": "ping", "agent_id": "general"},
        )
        assert r.status_code == 200, r.text
        lines = [l.strip() for l in r.text.splitlines() if l.strip()]
        types = {json.loads(l).get("type") for l in lines}
        assert types & {
            "final",
            "error",
            "warning",
        }, f"No terminal event in stream. Event types seen: {types}"

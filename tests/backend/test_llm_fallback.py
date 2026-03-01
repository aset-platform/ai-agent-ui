"""Unit tests for the FallbackLLM Groq-first / Anthropic-fallback wrapper.

Tests cover:
- Primary (Groq) path succeeds → Anthropic is never called.
- Groq raises RateLimitError → falls back to Anthropic.
- Groq raises APIConnectionError → falls back to Anthropic.
- Both fail → re-raises the Anthropic error.
- bind_tools() stores bound LLMs and returns ``self``.
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fallback(groq_mock, anthropic_mock):
    """Construct a FallbackLLM with both inner LLMs already patched.

    Patches the *module-level* names ``llm_fallback.ChatGroq`` and
    ``llm_fallback.ChatAnthropic`` (the local bindings created by the
    ``from X import Y`` statements in llm_fallback.py) rather than the
    original source modules, ensuring each call gets the correct mock even
    when the llm_fallback module is already cached in sys.modules.
    """
    import llm_fallback  # noqa: PLC0415 — imported here to get the module object
    with (
        patch.object(llm_fallback, "ChatGroq", return_value=groq_mock),
        patch.object(llm_fallback, "ChatAnthropic", return_value=anthropic_mock),
    ):
        llm = llm_fallback.FallbackLLM(
            groq_model="openai/gpt-oss-120b",
            anthropic_model="claude-sonnet-4-6",
            temperature=0.0,
            agent_id="test",
        )
    return llm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFallbackLLMPrimaryPath:
    """Groq succeeds — Anthropic must not be invoked."""

    def test_groq_invoked_and_returns_response(self):
        """When Groq succeeds, the Groq response is returned."""
        groq_mock = MagicMock()
        anthropic_mock = MagicMock()
        groq_mock.bind_tools.return_value = groq_mock
        anthropic_mock.bind_tools.return_value = anthropic_mock
        groq_mock.invoke.return_value = "groq_response"
        anthropic_mock.invoke.return_value = "anthropic_response"

        llm = _make_fallback(groq_mock, anthropic_mock)
        llm.bind_tools([])

        result = llm.invoke("hello")

        assert result == "groq_response"
        groq_mock.invoke.assert_called_once()
        anthropic_mock.invoke.assert_not_called()


class TestFallbackLLMRateLimitFallback:
    """Groq raises RateLimitError → fallback to Anthropic."""

    def test_rate_limit_triggers_anthropic(self):
        """RateLimitError from Groq must cause Anthropic to be used."""
        from groq import RateLimitError

        groq_mock = MagicMock()
        anthropic_mock = MagicMock()
        groq_mock.bind_tools.return_value = groq_mock
        anthropic_mock.bind_tools.return_value = anthropic_mock
        groq_mock.invoke.side_effect = RateLimitError(
            "rate limit", response=MagicMock(), body={}
        )
        anthropic_mock.invoke.return_value = "anthropic_fallback"

        llm = _make_fallback(groq_mock, anthropic_mock)
        llm.bind_tools([])

        result = llm.invoke("hello")

        assert result == "anthropic_fallback"
        anthropic_mock.invoke.assert_called_once()


class TestFallbackLLMConnectionFallback:
    """Groq raises APIConnectionError → fallback to Anthropic."""

    def test_connection_error_triggers_anthropic(self):
        """APIConnectionError from Groq must cause Anthropic to be used."""
        from groq import APIConnectionError

        groq_mock = MagicMock()
        anthropic_mock = MagicMock()
        groq_mock.bind_tools.return_value = groq_mock
        anthropic_mock.bind_tools.return_value = anthropic_mock
        groq_mock.invoke.side_effect = APIConnectionError(request=MagicMock())
        anthropic_mock.invoke.return_value = "anthropic_fallback"

        llm = _make_fallback(groq_mock, anthropic_mock)
        llm.bind_tools([])

        result = llm.invoke("hello")

        assert result == "anthropic_fallback"
        anthropic_mock.invoke.assert_called_once()


class TestFallbackLLMBothFail:
    """Both Groq and Anthropic fail → re-raise the Anthropic error."""

    def test_reraises_when_both_fail(self):
        """When Groq and Anthropic both raise, the Anthropic exception propagates."""
        from groq import RateLimitError

        groq_mock = MagicMock()
        anthropic_mock = MagicMock()
        groq_mock.bind_tools.return_value = groq_mock
        anthropic_mock.bind_tools.return_value = anthropic_mock
        groq_mock.invoke.side_effect = RateLimitError(
            "rate limit", response=MagicMock(), body={}
        )
        anthropic_mock.invoke.side_effect = RuntimeError("Anthropic also failed")

        llm = _make_fallback(groq_mock, anthropic_mock)
        llm.bind_tools([])

        with pytest.raises(RuntimeError, match="Anthropic also failed"):
            llm.invoke("hello")


class TestFallbackLLMBindTools:
    """bind_tools() duck-types LangChain's interface."""

    def test_bind_tools_returns_self(self):
        """bind_tools() must return the FallbackLLM instance itself."""
        groq_mock = MagicMock()
        anthropic_mock = MagicMock()
        groq_mock.bind_tools.return_value = groq_mock
        anthropic_mock.bind_tools.return_value = anthropic_mock

        llm = _make_fallback(groq_mock, anthropic_mock)
        result = llm.bind_tools([MagicMock()])

        assert result is llm

    def test_bind_tools_stores_both_bound_llms(self):
        """bind_tools() must store bound versions of both inner LLMs."""
        groq_bound = MagicMock()
        anthropic_bound = MagicMock()
        groq_mock = MagicMock()
        anthropic_mock = MagicMock()
        groq_mock.bind_tools.return_value = groq_bound
        anthropic_mock.bind_tools.return_value = anthropic_bound

        llm = _make_fallback(groq_mock, anthropic_mock)
        tools = [MagicMock()]
        llm.bind_tools(tools)

        groq_mock.bind_tools.assert_called_once_with(tools)
        anthropic_mock.bind_tools.assert_called_once_with(tools)
        assert llm._groq_bound is groq_bound
        assert llm._anthropic_bound is anthropic_bound

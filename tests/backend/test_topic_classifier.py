"""Tests for topic classifier — follow-up detection."""

from unittest.mock import MagicMock, patch

import pytest

from agents.conversation_context import (
    ConversationContext,
)
from agents.nodes.topic_classifier import (
    classify_followup,
)


class TestClassifyFollowup:
    def test_no_context_returns_new_topic(self):
        result = classify_followup(
            "What is AAPL price?", None,
        )
        assert result == "new_topic"

    def test_first_turn_returns_new_topic(self):
        ctx = ConversationContext(session_id="s1")
        ctx.turn_count = 0
        result = classify_followup(
            "What is AAPL price?", ctx,
        )
        assert result == "new_topic"

    @patch(
        "agents.nodes.topic_classifier._classify_via_llm",
    )
    def test_follow_up_detected(self, mock_llm):
        mock_llm.return_value = "follow_up"
        ctx = ConversationContext(session_id="s1")
        ctx.turn_count = 2
        ctx.summary = "Discussed AAPL sentiment."
        ctx.current_topic = "AAPL sentiment"
        result = classify_followup(
            "And what about the forecast?", ctx,
        )
        assert result == "follow_up"

    @patch(
        "agents.nodes.topic_classifier._classify_via_llm",
    )
    def test_new_topic_detected(self, mock_llm):
        mock_llm.return_value = "new_topic"
        ctx = ConversationContext(session_id="s1")
        ctx.turn_count = 3
        ctx.summary = "Discussed AAPL sentiment."
        ctx.current_topic = "AAPL sentiment"
        result = classify_followup(
            "Show me my portfolio", ctx,
        )
        assert result == "new_topic"

    @patch(
        "agents.nodes.topic_classifier._classify_via_llm",
    )
    def test_llm_failure_defaults_new_topic(
        self, mock_llm,
    ):
        mock_llm.side_effect = Exception("LLM down")
        ctx = ConversationContext(session_id="s1")
        ctx.turn_count = 2
        ctx.summary = "Discussed AAPL."
        result = classify_followup(
            "And dividends?", ctx,
        )
        assert result == "new_topic"

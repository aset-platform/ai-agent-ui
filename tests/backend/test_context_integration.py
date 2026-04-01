"""Integration test for multi-turn context awareness."""

from unittest.mock import MagicMock, patch

from agents.conversation_context import (
    ConversationContext,
    ConversationContextStore,
    update_summary,
)
from agents.nodes.topic_classifier import (
    classify_followup,
)


class TestMultiTurnFlow:
    """Simulate a 3-turn conversation."""

    def test_full_flow(self):
        store = ConversationContextStore()

        # Turn 1: New topic.
        ctx = ConversationContext(session_id="s1")
        result = classify_followup("AAPL price?", ctx)
        assert result == "new_topic"

        ctx.current_topic = "AAPL price"
        ctx.last_agent = "stock_analyst"
        ctx.last_intent = "stock_analysis"
        ctx.tickers_mentioned = ["AAPL"]
        ctx.turn_count = 1
        ctx.summary = "User asked about AAPL price."
        store.upsert("s1", ctx)

        # Turn 2: Follow-up.
        ctx2 = store.get("s1")
        with patch(
            "agents.nodes.topic_classifier"
            "._classify_via_llm",
            return_value="follow_up",
        ):
            result2 = classify_followup(
                "And the forecast?", ctx2,
            )
        assert result2 == "follow_up"
        assert ctx2.last_agent == "stock_analyst"

        # Turn 3: New topic.
        ctx3 = store.get("s1")
        with patch(
            "agents.nodes.topic_classifier"
            "._classify_via_llm",
            return_value="new_topic",
        ):
            result3 = classify_followup(
                "Show my portfolio", ctx3,
            )
        assert result3 == "new_topic"

    def test_context_survives_store_roundtrip(self):
        """Context data persists through upsert/get."""
        store = ConversationContextStore()
        ctx = ConversationContext(session_id="s1")
        ctx.summary = "Discussed AAPL sentiment."
        ctx.current_topic = "AAPL sentiment"
        ctx.last_agent = "sentiment"
        ctx.tickers_mentioned = ["AAPL"]
        ctx.turn_count = 3
        store.upsert("s1", ctx)

        loaded = store.get("s1")
        assert loaded is not None
        assert loaded.summary == "Discussed AAPL sentiment."
        assert loaded.last_agent == "sentiment"
        assert loaded.tickers_mentioned == ["AAPL"]
        assert loaded.turn_count == 3

    @patch(
        "agents.conversation_context._get_summary_llm",
    )
    def test_summary_update_increments_turn(
        self, mock_get_llm,
    ):
        """update_summary increments turn_count."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content="Updated summary about AAPL.",
        )
        mock_get_llm.return_value = mock_llm

        ctx = ConversationContext(session_id="s1")
        assert ctx.turn_count == 0

        update_summary(ctx, "AAPL price?", "150.0")
        assert ctx.turn_count == 1
        assert "AAPL" in ctx.summary

        update_summary(ctx, "Forecast?", "Target 165")
        assert ctx.turn_count == 2

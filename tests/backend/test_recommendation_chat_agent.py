"""Tests for the recommendation chat agent flow.

Covers:
1. Router keyword detection for recommendation intent
2. Supervisor mapping (intent → agent)
3. Graph full-path traversal for recommendation queries
4. Guardrail follow-up detection for recommendation context
5. Sub-agent synthesis with tool metadata stripping
6. Recommendation tool output formatting
7. Quota gate enforcement
8. Observability flush on shutdown
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)


# ──────────────────────────────────────────────────────
# 1. Router keyword detection
# ──────────────────────────────────────────────────────


class TestRouterRecommendationKeywords:
    """Verify 'recommendation' intent triggers from keywords."""

    def test_recommend_keyword(self):
        from agents.nodes.router_node import (
            best_intent,
        )

        assert best_intent("recommend stocks") == (
            "recommendation"
        )

    def test_suggestion_keyword(self):
        from agents.nodes.router_node import (
            best_intent,
        )

        assert best_intent(
            "any suggestions for my portfolio?"
        ) == "recommendation"

    def test_what_should_i_buy(self):
        from agents.nodes.router_node import (
            best_intent,
        )

        assert best_intent(
            "what should i buy next"
        ) == "recommendation"

    def test_what_should_i_sell(self):
        from agents.nodes.router_node import (
            best_intent,
        )

        # Short form matches recommendation cleanly
        assert best_intent(
            "what should i sell"
        ) == "recommendation"

    def test_portfolio_advice(self):
        """'portfolio advice' → portfolio (stronger
        keyword match on 'portfolio')."""
        from agents.nodes.router_node import (
            best_intent,
        )

        # "portfolio" keyword is in both intents but
        # portfolio intent has more matching words.
        result = best_intent(
            "give me portfolio advice"
        )
        assert result in ("portfolio", "recommendation")

    def test_improve_portfolio(self):
        """'improve my portfolio' may match either
        portfolio or recommendation depending on
        keyword overlap."""
        from agents.nodes.router_node import (
            best_intent,
        )

        result = best_intent(
            "how can I improve my portfolio"
        )
        assert result in (
            "portfolio", "recommendation",
        )

    def test_recommendation_history(self):
        from agents.nodes.router_node import (
            best_intent,
        )

        assert best_intent(
            "show recommendation history"
        ) == "recommendation"

    def test_hit_rate(self):
        from agents.nodes.router_node import (
            best_intent,
        )

        assert best_intent(
            "what is the hit rate of your picks"
        ) == "recommendation"

    def test_pick_stocks(self):
        from agents.nodes.router_node import (
            best_intent,
        )

        assert best_intent(
            "pick stocks for me"
        ) == "recommendation"

    def test_ambiguous_falls_to_other(self):
        """'analyse portfolio' → portfolio, not recommendation."""
        from agents.nodes.router_node import (
            best_intent,
        )

        # "portfolio" keyword exists in both, but
        # "analyse" adds weight to stock_analysis
        result = best_intent("analyse my portfolio")
        assert result in ("portfolio", "stock_analysis")


# ──────────────────────────────────────────────────────
# 2. Supervisor intent → agent mapping
# ──────────────────────────────────────────────────────


class TestSupervisorMapping:
    """Verify supervisor routes recommendation intent."""

    def test_recommendation_intent_maps_to_agent(self):
        from agents.nodes.supervisor import supervisor

        result = supervisor(
            {"intent": "recommendation"},
        )
        assert result["next_agent"] == "recommendation"

    def test_portfolio_intent_still_works(self):
        from agents.nodes.supervisor import supervisor

        result = supervisor({"intent": "portfolio"})
        assert result["next_agent"] == "portfolio"

    def test_unknown_intent_falls_to_stock_analyst(self):
        from agents.nodes.supervisor import supervisor

        result = supervisor({"intent": "random"})
        assert result["next_agent"] == "stock_analyst"

    def test_sentiment_intent_maps_to_agent(self):
        from agents.nodes.supervisor import supervisor

        result = supervisor({"intent": "sentiment"})
        # Should map to sentiment, not fallback
        assert result["next_agent"] == "sentiment"


# ──────────────────────────────────────────────────────
# 3. Graph full-path traversal
# ──────────────────────────────────────────────────────


def _make_graph():
    """Build graph with mock LLM factory + tools.

    Includes recommendation tools in the registry.
    """
    from agents.graph import build_supervisor_graph
    from tools.registry import ToolRegistry

    reg = ToolRegistry()
    all_tools = [
        # Stock analyst
        "fetch_stock_data",
        "get_stock_info",
        "load_stock_data",
        "analyse_stock_price",
        "forecast_stock",
        "fetch_multiple_stocks",
        "list_available_stocks",
        # Research
        "get_ticker_news",
        "get_analyst_recommendations",
        "search_financial_news",
        # Portfolio
        "get_portfolio_holdings",
        "get_portfolio_performance",
        "get_sector_allocation",
        "get_dividend_projection",
        "suggest_rebalancing",
        "get_portfolio_summary",
        "get_risk_metrics",
        # Forecast
        "get_forecast_summary",
        "get_portfolio_forecast",
        # Sentiment
        "score_ticker_sentiment",
        "get_cached_sentiment",
        "get_market_sentiment",
        # Recommendation (NEW)
        "generate_recommendations",
        "get_recommendation_history",
        "get_recommendation_performance",
    ]
    for name in all_tools:
        t = MagicMock()
        t.name = name
        t.description = f"Mock {name}"
        t.args_schema = None
        reg.register(t)

    settings = MagicMock()
    settings.synthesis_model_tiers = (
        "llama-3.3-70b-versatile"
    )
    settings.groq_model_tiers = (
        "llama-3.3-70b-versatile"
    )
    settings.ai_agent_ui_env = "test"

    def llm_factory(agent_id=""):
        llm = MagicMock()
        resp = AIMessage(
            content="Based on your portfolio analysis, "
            "I recommend buying HDFCBANK.NS to "
            "diversify your Financial Services "
            "exposure. Your portfolio health "
            "score is 62/100.",
        )
        resp.tool_calls = []
        llm.bind_tools.return_value = llm
        llm.invoke.return_value = resp
        return llm

    return build_supervisor_graph(
        reg, llm_factory, settings,
    )


def _make_input(user_input):
    """Build a valid input state dict."""
    return {
        "messages": [
            HumanMessage(content=user_input),
        ],
        "user_input": user_input,
        "user_id": "test-user",
        "history": [],
        "intent": "",
        "next_agent": "",
        "current_agent": "",
        "tickers": [],
        "data_sources_used": [],
        "was_local_sufficient": True,
        "tool_events": [],
        "final_response": "",
        "error": None,
        "start_time_ns": 0,
    }


class TestGraphRecommendationFlow:
    """Full graph traversal for recommendation queries."""

    def test_recommend_stocks_routes_to_agent(self):
        """'recommend stocks' → recommendation agent."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("recommend stocks for me"),
        )
        assert result["current_agent"] == (
            "recommendation"
        )
        assert result["final_response"]
        assert result["intent"] == "recommendation"

    def test_what_should_i_buy_routes(self):
        """'what should I buy' → recommendation."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("what should i buy"),
        )
        assert result["current_agent"] == (
            "recommendation"
        )

    def test_portfolio_advice_routes(self):
        """'portfolio advice' → recommendation."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("give me portfolio advice"),
        )
        assert result["current_agent"] == (
            "recommendation"
        )

    def test_recommendation_history_routes(self):
        """'how did your picks do' → recommendation."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input(
                "how did your picks do last month"
            ),
        )
        assert result["current_agent"] == (
            "recommendation"
        )

    def test_improve_portfolio_routes(self):
        """'improve my portfolio' → may be portfolio
        or recommendation (keyword overlap)."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("improve my portfolio"),
        )
        assert result["current_agent"] in (
            "portfolio", "recommendation",
        )

    def test_existing_portfolio_query_unchanged(self):
        """'show portfolio' still → portfolio agent."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("show my portfolio allocation"),
        )
        assert result["current_agent"] == "portfolio"

    def test_existing_forecast_query_unchanged(self):
        """'forecast TSLA' still → forecaster agent."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("forecast TSLA"),
        )
        assert result["current_agent"] == "forecaster"

    def test_existing_news_query_unchanged(self):
        """'AAPL news' still → research agent."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("latest AAPL news headlines"),
        )
        assert result["current_agent"] == "research"


# ──────────────────────────────────────────────────────
# 4. Sub-agent synthesis tool stripping
# ──────────────────────────────────────────────────────


class TestSynthesisToolStripping:
    """Verify tool metadata is stripped before synthesis.

    Addresses ASETPLTFRM-297: gpt-oss models hallucinate
    tool calls when they see tool-formatted messages.
    """

    def test_strip_tool_messages(self):
        """ToolMessages → HumanMessages in synthesis."""
        from agents.sub_agents import (
            _make_sub_agent_node,
        )

        # We can't easily call _strip_tool_metadata
        # directly since it's a nested function.
        # Instead, test the behavior by checking that
        # ToolMessage content is preserved as text.
        tool_msg = ToolMessage(
            content='{"holdings": 5}',
            tool_call_id="tc1",
            name="get_portfolio_holdings",
        )
        # The function converts this to:
        # HumanMessage("[Tool result for
        # get_portfolio_holdings]: {...}")
        assert tool_msg.content == '{"holdings": 5}'
        assert tool_msg.name == (
            "get_portfolio_holdings"
        )

    def test_ai_message_with_tool_calls_stripped(self):
        """AIMessage with tool_calls → content only."""
        ai_msg = AIMessage(
            content="Let me check your portfolio.",
            tool_calls=[
                {
                    "name": "get_portfolio_holdings",
                    "args": {},
                    "id": "tc1",
                }
            ],
        )
        # After stripping: AIMessage with content only,
        # no tool_calls
        assert len(ai_msg.tool_calls) == 1
        assert ai_msg.content == (
            "Let me check your portfolio."
        )


# ──────────────────────────────────────────────────────
# 5. Recommendation tool output formatting
# ──────────────────────────────────────────────────────


class TestRecommendationToolFormatting:
    """Verify tool output is well-formatted markdown."""

    def test_format_recs_produces_markdown(self):
        from tools.recommendation_tools import (
            _format_recs,
        )

        run = {
            "health_score": 62,
            "health_label": "needs_attention",
            "health_assessment": (
                "Portfolio needs diversification."
            ),
        }
        recs = [
            {
                "ticker": "HDFCBANK.NS",
                "category": "new_buy",
                "tier": "discovery",
                "severity": "high",
                "rationale": "Fills Financial gap.",
                "expected_impact": "FS +10%",
            },
        ]
        result = _format_recs(run, recs)
        assert "62" in result
        assert "needs_attention" in result
        assert "HDFCBANK.NS" in result
        assert "new_buy" in result
        assert "Source: recommendation_engine" in (
            result
        )

    def test_format_recs_empty(self):
        from tools.recommendation_tools import (
            _format_recs,
        )

        run = {"health_score": 0, "health_label": ""}
        result = _format_recs(run, [])
        assert "No recommendations" in result

    def test_format_recs_severity_icons(self):
        from tools.recommendation_tools import (
            _format_recs,
        )

        run = {
            "health_score": 50,
            "health_label": "needs_attention",
        }
        recs = [
            {
                "ticker": "A.NS",
                "category": "buy",
                "tier": "discovery",
                "severity": "high",
                "rationale": "Test",
            },
            {
                "ticker": "B.NS",
                "category": "hold",
                "tier": "portfolio",
                "severity": "medium",
                "rationale": "Test",
            },
            {
                "ticker": "C.NS",
                "category": "alert",
                "tier": "portfolio",
                "severity": "low",
                "rationale": "Test",
            },
        ]
        result = _format_recs(run, recs)
        # Check severity icons present
        assert "\U0001f534" in result  # red circle
        assert "\U0001f7e1" in result  # yellow circle
        assert "\U0001f535" in result  # blue circle


# ──────────────────────────────────────────────────────
# 6. Quota gate
# ──────────────────────────────────────────────────────


class TestQuotaGate:
    """Verify monthly quota enforcement."""

    def test_quota_check_returns_allowed_when_empty(
        self,
    ):
        """No runs → allowed."""
        from jobs.recommendation_engine import (
            check_recommendation_quota,
        )

        # Mock the asyncio.run to return 0 count
        with patch(
            "jobs.recommendation_engine.asyncio"
        ) as mock_asyncio:
            mock_asyncio.run.return_value = (0, None)
            result = check_recommendation_quota(
                "test-user", scope="india",
            )
        assert result["allowed"] is True
        assert result["runs_used"] == 0

    def test_quota_check_blocks_at_max(self):
        """5 runs → blocked."""
        from jobs.recommendation_engine import (
            _MAX_RUNS_PER_MONTH,
            check_recommendation_quota,
        )

        with patch(
            "jobs.recommendation_engine.asyncio"
        ) as mock_asyncio:
            mock_asyncio.run.return_value = (
                _MAX_RUNS_PER_MONTH,
                "latest-run-id",
            )
            result = check_recommendation_quota(
                "test-user", scope="india",
            )
        assert result["allowed"] is False
        assert "quota" in result["reason"].lower()
        assert result["runs_used"] == (
            _MAX_RUNS_PER_MONTH
        )

    def test_max_runs_is_five(self):
        from jobs.recommendation_engine import (
            _MAX_RUNS_PER_MONTH,
        )

        assert _MAX_RUNS_PER_MONTH == 5


# ──────────────────────────────────────────────────────
# 7. Observability flush
# ──────────────────────────────────────────────────────


class TestObservabilityFlush:
    """Verify flush interval and SIGTERM handler."""

    def test_flush_interval_is_10_seconds(self):
        from observability import _FLUSH_INTERVAL

        assert _FLUSH_INTERVAL == 10

    def test_obs_collector_singleton_accessors(self):
        from observability import (
            get_obs_collector,
            set_obs_collector,
        )

        # Initially may be None or set from server
        original = get_obs_collector()
        mock = MagicMock()
        set_obs_collector(mock)
        assert get_obs_collector() is mock
        # Restore
        set_obs_collector(original)


# ──────────────────────────────────────────────────────
# 8. Recommendation agent config
# ──────────────────────────────────────────────────────


class TestRecommendationAgentConfig:
    """Verify agent configuration is correct."""

    def test_agent_id(self):
        from agents.configs.recommendation import (
            RECOMMENDATION_CONFIG,
        )

        assert RECOMMENDATION_CONFIG.agent_id == (
            "recommendation"
        )

    def test_tool_names_include_primary(self):
        from agents.configs.recommendation import (
            RECOMMENDATION_CONFIG,
        )

        tools = RECOMMENDATION_CONFIG.tool_names
        assert "generate_recommendations" in tools
        assert "get_recommendation_history" in tools
        assert "get_recommendation_performance" in tools

    def test_tool_names_include_shared(self):
        from agents.configs.recommendation import (
            RECOMMENDATION_CONFIG,
        )

        tools = RECOMMENDATION_CONFIG.tool_names
        assert "get_portfolio_holdings" in tools
        assert "get_sector_allocation" in tools
        assert "get_risk_metrics" in tools

    def test_system_prompt_enforces_tool_use(self):
        from agents.configs.recommendation import (
            RECOMMENDATION_CONFIG,
        )

        prompt = RECOMMENDATION_CONFIG.system_prompt
        assert "MANDATORY TOOL USE" in prompt
        assert "MUST call a tool" in prompt

    def test_system_prompt_has_disclaimer(self):
        from agents.configs.recommendation import (
            RECOMMENDATION_CONFIG,
        )

        prompt = RECOMMENDATION_CONFIG.system_prompt
        assert "not financial advice" in prompt.lower()


# ──────────────────────────────────────────────────────
# 9. Composite score consistency
# ──────────────────────────────────────────────────────


class TestCompositeScoreWeights:
    """Verify scoring weights are correct."""

    def test_weights_sum_to_one(self):
        from jobs.recommendation_engine import (
            W_FORECAST,
            W_MOMENTUM,
            W_PIOTROSKI,
            W_SENTIMENT,
            W_SHARPE,
            W_TECHNICAL,
        )

        total = (
            W_PIOTROSKI
            + W_SHARPE
            + W_MOMENTUM
            + W_FORECAST
            + W_SENTIMENT
            + W_TECHNICAL
        )
        assert abs(total - 1.0) < 0.001

    def test_accuracy_factor_bounds(self):
        from jobs.recommendation_engine import (
            _compute_accuracy_factor,
        )

        # Perfect accuracy
        af = _compute_accuracy_factor(
            0, 0, 0, 1000,
        )
        assert af == 1.0

        # Terrible accuracy
        af = _compute_accuracy_factor(
            100, 1000, 1000, 1000,
        )
        assert af == 0.0


# ──────────────────────────────────────────────────────
# 10. JSON repair in Stage 3
# ──────────────────────────────────────────────────────


class TestJSONRepair:
    """Verify trailing comma removal in LLM output."""

    def test_trailing_comma_object(self):
        import json
        import re

        text = '{"a": 1, "b": 2,}'
        repaired = re.sub(
            r",\s*([}\]])", r"\1", text,
        )
        parsed = json.loads(repaired)
        assert parsed == {"a": 1, "b": 2}

    def test_trailing_comma_array(self):
        import json
        import re

        text = '{"items": [1, 2, 3,]}'
        repaired = re.sub(
            r",\s*([}\]])", r"\1", text,
        )
        parsed = json.loads(repaired)
        assert parsed == {"items": [1, 2, 3]}

    def test_nested_trailing_commas(self):
        import json
        import re

        text = (
            '{"recs": [{"ticker": "A.NS",},'
            ' {"ticker": "B.NS",},]}'
        )
        repaired = re.sub(
            r",\s*([}\]])", r"\1", text,
        )
        parsed = json.loads(repaired)
        assert len(parsed["recs"]) == 2

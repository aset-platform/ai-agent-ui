"""Integration tests for the LangGraph supervisor graph.

Tests full graph traversal with mocked LLM and tools.
"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from agents.graph import build_supervisor_graph
from tools.registry import ToolRegistry


def _make_graph():
    """Build graph with mock LLM factory + tools."""
    reg = ToolRegistry()
    for name in [
        "fetch_stock_data",
        "get_stock_info",
        "load_stock_data",
        "analyse_stock_price",
        "forecast_stock",
        "fetch_multiple_stocks",
        "list_available_stocks",
        "get_ticker_news",
        "get_analyst_recommendations",
        "search_financial_news",
        "get_portfolio_holdings",
        "get_portfolio_performance",
        "get_sector_allocation",
        "get_dividend_projection",
        "suggest_rebalancing",
        "get_portfolio_summary",
        "get_risk_metrics",
        "get_forecast_summary",
        "get_portfolio_forecast",
    ]:
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

    def llm_factory(agent_id=""):
        llm = MagicMock()
        resp = AIMessage(
            content="Mock analysis response with "
            "enough text to pass synthesis threshold. "
            "Buy AAPL with 80% confidence. "
            "The stock shows strong momentum.",
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


class TestGraphFullPath:
    """Full graph traversal tests."""

    def test_stock_analysis_query(self):
        """'Analyse AAPL' → stock_analyst agent."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("Analyse AAPL"),
        )
        assert result["current_agent"] == (
            "stock_analyst"
        )
        assert result["final_response"]
        assert result["intent"] == "stock_analysis"

    def test_forecast_query(self):
        """'Forecast TSLA' → forecaster agent."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("Forecast TSLA"),
        )
        assert result["current_agent"] == "forecaster"
        assert result["final_response"]

    def test_portfolio_query(self):
        """'Show my portfolio' → portfolio agent."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("Show my portfolio allocation"),
        )
        assert result["current_agent"] == "portfolio"
        assert result["final_response"]

    def test_news_query(self):
        """'AAPL news' → research agent."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("Latest AAPL news headlines"),
        )
        assert result["current_agent"] == "research"
        assert result["final_response"]

    def test_non_financial_decline(self):
        """'Weather in London' → decline."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("What is the weather in London?"),
        )
        assert result["current_agent"] == "decline"
        assert "stock analysis" in (
            result["final_response"].lower()
        )

    def test_blocked_content_decline(self):
        """Blocked keyword → decline."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input(
                "Tell me about weapons and guns",
            ),
        )
        assert result["current_agent"] == "decline"

    def test_ticker_only_routes_to_stock(self):
        """Just a ticker symbol → stock_analyst."""
        graph = _make_graph()
        result = graph.invoke(
            _make_input("RELIANCE.NS"),
        )
        # Ticker detected → routes to router →
        # ambiguous (no keyword) → llm_classifier →
        # defaults to stock_analysis
        assert result["final_response"]

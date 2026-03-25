"""Unit tests for LangGraph supervisor graph nodes."""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------
# Guardrail
# ---------------------------------------------------------------


class TestGuardrailNode:
    """Tests for agents.nodes.guardrail."""

    def _call(self, user_input):
        from agents.nodes.guardrail import guardrail

        return guardrail({"user_input": user_input})

    def test_financial_query_routes_to_router(self):
        r = self._call("Analyse AAPL stock")
        assert r["next_agent"] == "router"

    def test_ticker_only_routes_to_router(self):
        r = self._call("AAPL")
        assert r["next_agent"] == "router"
        assert "AAPL" in r["tickers"]

    def test_portfolio_query_routes_to_router(self):
        r = self._call("Show my portfolio allocation")
        assert r["next_agent"] == "router"

    def test_non_financial_declines(self):
        r = self._call("What is the capital of France?")
        assert r["next_agent"] == "decline"

    def test_joke_declines(self):
        r = self._call("Tell me a joke")
        assert r["next_agent"] == "decline"

    def test_weather_declines(self):
        r = self._call("What is the weather today?")
        assert r["next_agent"] == "decline"

    def test_blocked_content_declines(self):
        r = self._call("Tell me about weapons")
        assert r["next_agent"] == "decline"
        assert r.get("error") == "blocked"

    def test_extracts_tickers(self):
        r = self._call("Compare AAPL and TSLA")
        assert r["next_agent"] == "router"
        assert "AAPL" in r["tickers"]
        assert "TSLA" in r["tickers"]

    def test_forecast_keyword_routes(self):
        """Forecast keyword routes to router even
        without a short ticker."""
        r = self._call("Forecast RELIANCE.NS")
        assert r["next_agent"] == "router"

    def test_sets_start_time(self):
        r = self._call("Analyse AAPL")
        assert r["start_time_ns"] > 0


# ---------------------------------------------------------------
# Router (Tier 1)
# ---------------------------------------------------------------


class TestRouterNode:
    """Tests for agents.nodes.router_node."""

    def _call(self, user_input):
        from agents.nodes.router_node import (
            router_node,
        )

        return router_node({"user_input": user_input})

    def test_portfolio_intent(self):
        r = self._call("Show my portfolio allocation")
        assert r["intent"] == "portfolio"
        assert r["next_agent"] == "supervisor"

    def test_stock_analysis_intent(self):
        r = self._call("Analyse AAPL technical indicators")
        assert r["intent"] == "stock_analysis"

    def test_forecast_intent(self):
        r = self._call("Forecast TSLA for 6 months")
        assert r["intent"] == "forecast"

    def test_research_intent(self):
        r = self._call("Latest AAPL news headlines")
        assert r["intent"] == "research"

    def test_ambiguous_to_llm_classifier(self):
        r = self._call("Should I increase my position?")
        assert r["next_agent"] == "llm_classifier"


# ---------------------------------------------------------------
# LLM Classifier (Tier 2)
# ---------------------------------------------------------------


class TestLLMClassifier:
    """Tests for agents.nodes.llm_classifier."""

    @patch("agents.nodes.llm_classifier.ChatGroq")
    def test_stock_analysis_intent(self, mock_groq):
        from agents.nodes.llm_classifier import (
            llm_classifier,
        )

        mock_resp = MagicMock()
        mock_resp.content = "stock_analysis"
        mock_groq.return_value.invoke.return_value = (
            mock_resp
        )

        r = llm_classifier(
            {"user_input": "How is AAPL doing?"}
        )
        assert r["intent"] == "stock_analysis"
        assert r["next_agent"] == "supervisor"

    @patch("agents.nodes.llm_classifier.ChatGroq")
    def test_decline_intent(self, mock_groq):
        from agents.nodes.llm_classifier import (
            llm_classifier,
        )

        mock_resp = MagicMock()
        mock_resp.content = "decline"
        mock_groq.return_value.invoke.return_value = (
            mock_resp
        )

        r = llm_classifier(
            {"user_input": "Tell me about cats"}
        )
        assert r["next_agent"] == "decline"

    @patch("agents.nodes.llm_classifier.ChatGroq")
    def test_invalid_output_declines(self, mock_groq):
        from agents.nodes.llm_classifier import (
            llm_classifier,
        )

        mock_resp = MagicMock()
        mock_resp.content = "gibberish_output_xyz"
        mock_groq.return_value.invoke.return_value = (
            mock_resp
        )

        r = llm_classifier(
            {"user_input": "something weird"}
        )
        assert r["next_agent"] == "decline"

    @patch("agents.nodes.llm_classifier.ChatGroq")
    def test_llm_error_defaults_to_stock(
        self, mock_groq,
    ):
        from agents.nodes.llm_classifier import (
            llm_classifier,
        )

        mock_groq.side_effect = Exception("API down")
        r = llm_classifier(
            {"user_input": "something"}
        )
        assert r["intent"] == "stock_analysis"
        assert r["next_agent"] == "supervisor"


# ---------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------


class TestSupervisor:
    """Tests for agents.nodes.supervisor."""

    def _call(self, intent):
        from agents.nodes.supervisor import supervisor

        return supervisor({"intent": intent})

    def test_portfolio_maps(self):
        r = self._call("portfolio")
        assert r["next_agent"] == "portfolio"

    def test_stock_analysis_maps(self):
        r = self._call("stock_analysis")
        assert r["next_agent"] == "stock_analyst"

    def test_forecast_maps(self):
        r = self._call("forecast")
        assert r["next_agent"] == "forecaster"

    def test_research_maps(self):
        r = self._call("research")
        assert r["next_agent"] == "research"

    def test_unknown_defaults_to_stock(self):
        r = self._call("something_unknown")
        assert r["next_agent"] == "stock_analyst"


# ---------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------


class TestSynthesis:
    """Tests for agents.nodes.synthesis."""

    def test_long_response_passthrough(self):
        from agents.nodes.synthesis import synthesis

        long_text = "A" * 200
        r = synthesis({"final_response": long_text})
        assert r["final_response"] == long_text

    def test_short_response_passthrough(self):
        """Short response still passes through if
        synthesis LLM is unavailable."""
        from agents.nodes.synthesis import synthesis

        r = synthesis({
            "final_response": "Short",
            "messages": [],
        })
        # Should at least return something
        assert r["final_response"]

    def test_empty_response_handled(self):
        from agents.nodes.synthesis import synthesis

        r = synthesis({
            "final_response": "",
            "messages": [],
        })
        assert r["final_response"]


# ---------------------------------------------------------------
# Decline
# ---------------------------------------------------------------


class TestDecline:
    """Tests for agents.nodes.decline."""

    def test_returns_decline_message(self):
        from agents.nodes.decline import decline_node

        r = decline_node({"intent": ""})
        assert "stock analysis" in r["final_response"]
        assert "portfolio" in r["final_response"]
        assert r["current_agent"] == "decline"
        assert r["tool_events"] == []

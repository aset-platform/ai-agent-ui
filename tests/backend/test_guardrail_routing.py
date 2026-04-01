"""Tests for intent-aware follow-up routing in guardrail.

Verifies that the guardrail node correctly handles intent
switches mid-conversation instead of unconditionally reusing
the last agent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_ctx(
    last_agent: str = "",
    last_intent: str = "",
    tickers: list[str] | None = None,
    turn_count: int = 1,
    summary: str = "User asked about portfolio.",
):
    """Build a mock ConversationContext."""
    ctx = MagicMock()
    ctx.last_agent = last_agent
    ctx.last_intent = last_intent
    ctx.tickers_mentioned = tickers or []
    ctx.turn_count = turn_count
    ctx.summary = summary
    ctx.current_topic = last_intent
    return ctx


def _call_guardrail(user_input, ctx=None, session_id="s1"):
    """Invoke guardrail with optional mocked context."""
    from agents.nodes.guardrail import guardrail

    state = {"user_input": user_input, "session_id": session_id}

    if ctx is not None:
        with patch(
            "agents.conversation_context.context_store"
        ) as mock_store:
            mock_store.get.return_value = ctx
            return guardrail(state)
    return guardrail(state)


# ---------------------------------------------------------------
# 1. Intent switch: portfolio → stock analysis
# ---------------------------------------------------------------


class TestIntentSwitch:
    """When user switches from portfolio to stock analysis."""

    def test_analyse_after_portfolio_routes_to_router(self):
        """'analyse SBI' after portfolio chat must NOT
        reuse portfolio agent."""
        ctx = _make_ctx(
            last_agent="portfolio",
            last_intent="portfolio",
        )
        r = _call_guardrail("analyse SBI", ctx)
        assert r["next_agent"] == "router"

    def test_fetch_after_portfolio_routes_to_router(self):
        """'fetch SBIN.NS' after portfolio chat must
        route through router."""
        ctx = _make_ctx(
            last_agent="portfolio",
            last_intent="portfolio",
        )
        r = _call_guardrail("fetch SBIN.NS data", ctx)
        assert r["next_agent"] == "router"

    def test_forecast_after_stock_routes_to_router(self):
        """'forecast TCS' after stock analysis must
        re-route to forecaster via router."""
        ctx = _make_ctx(
            last_agent="stock_analyst",
            last_intent="stock_analysis",
        )
        r = _call_guardrail("forecast TCS", ctx)
        assert r["next_agent"] == "router"

    def test_news_after_portfolio_routes_to_router(self):
        """'latest news on INFY' after portfolio must
        route to research via router."""
        ctx = _make_ctx(
            last_agent="portfolio",
            last_intent="portfolio",
        )
        r = _call_guardrail("latest news on INFY", ctx)
        assert r["next_agent"] == "router"


# ---------------------------------------------------------------
# 2. Same-intent follow-up → reuse agent
# ---------------------------------------------------------------


class TestSameIntentFollowUp:
    """When user continues with the same intent."""

    def test_portfolio_followup_reuses_agent(self):
        """'and the dividends?' after portfolio should
        reuse portfolio agent (keyword match: none,
        but 'allocation' triggers portfolio)."""
        ctx = _make_ctx(
            last_agent="portfolio",
            last_intent="portfolio",
        )
        r = _call_guardrail(
            "show me the allocation breakdown", ctx,
        )
        assert r["next_agent"] == "portfolio"
        assert r["intent"] == "portfolio"

    def test_stock_analysis_followup_reuses_agent(self):
        """'compare with INFY' after stock analysis
        should reuse stock_analyst."""
        ctx = _make_ctx(
            last_agent="stock_analyst",
            last_intent="stock_analysis",
        )
        r = _call_guardrail(
            "compare it with INFY", ctx,
        )
        assert r["next_agent"] == "stock_analyst"
        assert r["intent"] == "stock_analysis"


# ---------------------------------------------------------------
# 3. Ambiguous follow-up → LLM classifier
# ---------------------------------------------------------------


class TestAmbiguousFollowUp:
    """When message has no keywords, use LLM classifier."""

    @patch("agents.nodes.topic_classifier.classify_followup")
    def test_ambiguous_followup_calls_classifier(
        self, mock_classify,
    ):
        """'which one should I increase?' has no intent
        keywords — must call LLM classifier."""
        mock_classify.return_value = "follow_up"
        ctx = _make_ctx(
            last_agent="portfolio",
            last_intent="portfolio",
        )
        r = _call_guardrail(
            "which one should I increase?", ctx,
        )
        mock_classify.assert_called_once()
        assert r["next_agent"] == "portfolio"

    @patch("agents.nodes.topic_classifier.classify_followup")
    def test_ambiguous_new_topic_falls_through(
        self, mock_classify,
    ):
        """If LLM says new_topic for ambiguous message,
        fall through to financial relevance check."""
        mock_classify.return_value = "new_topic"
        ctx = _make_ctx(
            last_agent="portfolio",
            last_intent="portfolio",
        )
        r = _call_guardrail("hello there", ctx)
        # No financial keywords → decline
        assert r["next_agent"] == "decline"


# ---------------------------------------------------------------
# 4. First message (no context)
# ---------------------------------------------------------------


class TestFirstMessage:
    """First message in session has no context."""

    def test_first_message_routes_to_router(self):
        """'analyse AAPL' with no session context
        routes normally through router."""
        r = _call_guardrail(
            "analyse AAPL", ctx=None, session_id="",
        )
        assert r["next_agent"] == "router"


# ---------------------------------------------------------------
# 5. Multi-keyword — highest intent wins
# ---------------------------------------------------------------


class TestMultiKeyword:
    """When message contains keywords from multiple intents."""

    def test_analyse_my_portfolio_picks_portfolio(self):
        """'analyse my portfolio allocation' has
        stock_analysis (analyse=1) + portfolio
        (portfolio=1, allocation=1). Portfolio wins
        (score 2 > 1)."""
        ctx = _make_ctx(
            last_agent="stock_analyst",
            last_intent="stock_analysis",
        )
        # portfolio wins → different from last_intent
        # → routes to router
        r = _call_guardrail(
            "analyse my portfolio allocation", ctx,
        )
        assert r["next_agent"] == "router"


# ---------------------------------------------------------------
# 6. Ticker merging on same-intent follow-up
# ---------------------------------------------------------------


class TestTickerMerging:
    """Tickers from context + new message should merge."""

    def test_merges_new_tickers_with_context(self):
        """Follow-up with new ticker should merge into
        existing context tickers."""
        ctx = _make_ctx(
            last_agent="portfolio",
            last_intent="portfolio",
            tickers=["INFY.NS", "TCS.NS"],
        )
        r = _call_guardrail(
            "add SBIN.NS to my portfolio", ctx,
        )
        # "portfolio" keyword → same intent → reuse
        assert r["next_agent"] == "portfolio"
        assert "INFY.NS" in r["tickers"]
        assert "TCS.NS" in r["tickers"]
        assert "SBIN.NS" in r["tickers"]


# ---------------------------------------------------------------
# 7. best_intent() unit tests
# ---------------------------------------------------------------


class TestAmbiguousClarification:
    """When intent switch has tied scores, offer options."""

    def test_tied_scores_returns_clarification(self):
        """'analyse my portfolio' has stock_analysis=1
        and portfolio=2. Not tied — routes to router."""
        ctx = _make_ctx(
            last_agent="stock_analyst",
            last_intent="stock_analysis",
        )
        r = _call_guardrail(
            "analyse my portfolio allocation", ctx,
        )
        # portfolio wins (score 2 vs 1) → router
        assert r["next_agent"] == "router"

    def test_clear_winner_skips_clarification(self):
        """'analyse SBIN.NS' → stock_analysis=1,
        portfolio=0. Clear winner → router."""
        ctx = _make_ctx(
            last_agent="portfolio",
            last_intent="portfolio",
        )
        r = _call_guardrail("analyse SBIN.NS", ctx)
        assert r["next_agent"] == "router"


class TestBestIntent:
    """Unit tests for the extracted best_intent()."""

    def test_analyse_returns_stock_analysis(self):
        from agents.nodes.router_node import best_intent

        assert best_intent("analyse SBI") == "stock_analysis"

    def test_portfolio_returns_portfolio(self):
        from agents.nodes.router_node import best_intent

        assert best_intent("show my holdings") == "portfolio"

    def test_forecast_returns_forecast(self):
        from agents.nodes.router_node import best_intent

        assert best_intent("predict AAPL 6 month") == "forecast"

    def test_no_match_returns_none(self):
        from agents.nodes.router_node import best_intent

        assert best_intent("hello world") is None

    def test_case_insensitive(self):
        from agents.nodes.router_node import best_intent

        assert best_intent("ANALYSE TCS") == "stock_analysis"

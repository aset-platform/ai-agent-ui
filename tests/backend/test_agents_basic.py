"""Basic agent tests without LLM dependencies.

Validates config constants, registry behaviour, and router
logic.  Uses ``sys.modules`` stubs so langchain is not
required at runtime.
"""

import sys
from unittest.mock import MagicMock

# ---------------------------------------------------------------
# Install stubs before any agent imports.
# ---------------------------------------------------------------

_STUBS = {
    "langchain_groq": MagicMock(),
    "langchain_anthropic": MagicMock(),
    "langchain_core": MagicMock(),
    "langchain_core.messages": MagicMock(),
    "langchain_core.tools": MagicMock(),
    "tools._ticker_linker": MagicMock(),
    "tools.registry": MagicMock(),
}
for _n, _m in _STUBS.items():
    if _n not in sys.modules:
        sys.modules[_n] = _m

from agents.config import (  # noqa: E402
    MAX_ITERATIONS,
    AgentConfig,
)
from agents.registry import AgentRegistry  # noqa: E402
from agents.router import (  # noqa: E402
    BLOCKED_RESPONSE,
    is_blocked,
    route,
)


# ---------------------------------------------------------------
# AgentConfig / MAX_ITERATIONS
# ---------------------------------------------------------------


class TestAgentConfig:
    """Tests for agents.config constants."""

    def test_max_iterations_is_15(self):
        """MAX_ITERATIONS defaults to 15."""
        assert MAX_ITERATIONS == 15

    def test_max_iterations_is_int(self):
        """MAX_ITERATIONS is an integer."""
        assert isinstance(MAX_ITERATIONS, int)

    def test_agent_config_defaults(self):
        """AgentConfig has sensible defaults."""
        cfg = AgentConfig(
            agent_id="test",
            name="Test Agent",
            description="A test agent",
        )
        assert cfg.temperature == 0.0
        assert cfg.system_prompt == ""
        assert cfg.tool_names == []
        assert cfg.groq_model_tiers == []

    def test_agent_config_custom_values(self):
        """AgentConfig stores custom values."""
        cfg = AgentConfig(
            agent_id="stock",
            name="Stock Agent",
            description="Analyzes stocks",
            groq_model_tiers=["llama-3.3-70b"],
            temperature=0.7,
            system_prompt="You analyze stocks.",
            tool_names=["get_price", "analyze"],
        )
        assert cfg.agent_id == "stock"
        assert cfg.temperature == 0.7
        assert len(cfg.groq_model_tiers) == 1
        assert len(cfg.tool_names) == 2


# ---------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------


class TestAgentRegistry:
    """Tests for AgentRegistry register/get/list."""

    def _make_agent(self, agent_id="test"):
        """Create a mock agent with config."""
        agent = MagicMock()
        agent.config = AgentConfig(
            agent_id=agent_id,
            name=f"{agent_id} Agent",
            description=f"The {agent_id} agent",
        )
        return agent

    def test_register_and_get(self):
        """Registered agent can be retrieved."""
        reg = AgentRegistry()
        agent = self._make_agent("general")
        reg.register(agent)
        assert reg.get("general") is agent

    def test_get_missing_returns_none(self):
        """Missing agent returns None."""
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_list_agents_empty(self):
        """Empty registry lists nothing."""
        reg = AgentRegistry()
        assert reg.list_agents() == []

    def test_list_agents_populated(self):
        """Populated registry lists all agents."""
        reg = AgentRegistry()
        reg.register(self._make_agent("general"))
        reg.register(self._make_agent("stock"))
        result = reg.list_agents()
        assert len(result) == 2
        ids = {a["id"] for a in result}
        assert ids == {"general", "stock"}

    def test_register_overwrites_duplicate(self):
        """Duplicate ID overwrites previous agent."""
        reg = AgentRegistry()
        a1 = self._make_agent("dup")
        a2 = self._make_agent("dup")
        reg.register(a1)
        reg.register(a2)
        assert reg.get("dup") is a2
        assert len(reg.list_agents()) == 1


# ---------------------------------------------------------------
# Router
# ---------------------------------------------------------------


class TestRouter:
    """Tests for agents.router route/is_blocked."""

    def test_route_stock_keyword(self):
        """Stock keyword routes to stock agent."""
        assert route("Analyze AAPL stock") == "stock"

    def test_route_general_fallback(self):
        """Non-stock input routes to general."""
        assert route("What is the weather?") == "general"

    def test_route_ticker_pattern(self):
        """Ticker-like pattern routes to stock."""
        assert route("How is TSLA doing?") == "stock"

    def test_is_blocked_true(self):
        """Blocked keywords are detected."""
        assert is_blocked("Tell me about weapons")

    def test_is_blocked_false(self):
        """Clean input is not blocked."""
        assert not is_blocked("Analyze AAPL")

    def test_blocked_response_not_empty(self):
        """BLOCKED_RESPONSE is a non-empty string."""
        assert isinstance(BLOCKED_RESPONSE, str)
        assert len(BLOCKED_RESPONSE) > 10

    def test_route_with_history_param(self):
        """Route accepts optional history param."""
        result = route(
            "forecast AAPL",
            history=[
                {"role": "user", "content": "hi"},
            ],
        )
        assert result == "stock"

    def test_route_case_insensitive(self):
        """Keywords matched case-insensitively."""
        assert route("STOCK analysis please") == "stock"

    def test_route_multiple_keywords(self):
        """Multiple stock keywords still route ok."""
        assert route("buy stock forecast") == "stock"


# ---------------------------------------------------------------
# BaseAgent (source introspection — avoids import)
# ---------------------------------------------------------------


class TestBaseAgentExport:
    """Check BaseAgent source without full import."""

    def test_base_agent_defines_build_llm(self):
        """BaseAgent source contains _build_llm."""
        from pathlib import Path
        src = (
            Path(__file__).resolve().parent.parent.parent
            / "backend" / "agents" / "base.py"
        ).read_text()
        assert "_build_llm" in src

    def test_base_agent_extends_abc(self):
        """BaseAgent source imports ABC."""
        from pathlib import Path
        src = (
            Path(__file__).resolve().parent.parent.parent
            / "backend" / "agents" / "base.py"
        ).read_text()
        assert "from abc import ABC" in src

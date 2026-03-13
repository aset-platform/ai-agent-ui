"""Stock Analysis Agent with N-tier Groq/Anthropic LLM cascade.

:class:`StockAgent` extends :class:`~agents.base.BaseAgent` and is wired
with eight financial analysis tools:

- :func:`~tools.stock_data_tool.fetch_stock_data` — delta-fetch
- :func:`~tools.stock_data_tool.get_stock_info` — company metadata
- :func:`~tools.stock_data_tool.load_stock_data` — inspect data
- :func:`~tools.stock_data_tool.fetch_multiple_stocks` — batch fetch
- :func:`~tools.stock_data_tool.get_dividend_history` — dividends
- :func:`~tools.stock_data_tool.list_available_stocks` — registry
- :func:`~tools.price_analysis_tool.analyse_stock_price` — analysis
- :func:`~tools.forecasting_tool.forecast_stock` — forecast

The agentic loop (inherited from BaseAgent) drives the LLM through
the **fetch -> analyse -> forecast** pipeline automatically.
The agent uses :class:`~llm_fallback.FallbackLLM` which cascades
through an ordered list of Groq models before falling back to
Anthropic Claude.

Typical usage::

    from tools.registry import ToolRegistry
    from agents.stock_agent import create_stock_agent

    registry = ToolRegistry()
    # (register stock tools first)
    agent = create_stock_agent(registry)
    reply = agent.run("Analyse AAPL")
"""

from agents.base import AgentConfig, BaseAgent
from config import get_settings
from llm_fallback import FallbackLLM
from message_compressor import MessageCompressor
from token_budget import TokenBudget
from tools.registry import ToolRegistry

# -------------------------------------------------------------------
# System prompt
# -------------------------------------------------------------------

_STOCK_SYSTEM_PROMPT = (
    "You are a professional stock market analyst with "
    "deep expertise in technical analysis and "
    "time-series price forecasting.\n\n"
    "STANDARD PIPELINE — follow this order for every "
    "single-stock request:\n"
    "STEP 1 (data): Call fetch_stock_data and "
    "get_stock_info. Wait for results before "
    "proceeding.\n"
    "STEP 2 (analysis): Only AFTER step 1 completes, "
    "call analyse_stock_price, forecast_stock, and "
    "search_market_news.\n"
    "STEP 3 (report): Synthesise all results into a "
    "clear, structured report.\n\n"
    "CRITICAL: Never call analyse_stock_price or "
    "forecast_stock in the same step as "
    "fetch_stock_data. Data must be written to the "
    "database before it can be read.\n\n"
    "COMPARISON PIPELINE — for multi-stock requests:\n"
    "1. Call fetch_multiple_stocks with a "
    "comma-separated list of tickers.\n"
    "2. For each ticker: call analyse_stock_price "
    "then forecast_stock.\n"
    "3. Present a side-by-side comparison table "
    "sorted by 6-month upside potential.\n\n"
    "RULES:\n"
    "- Use exact ticker symbols "
    "(e.g. AAPL, TSLA, RELIANCE.NS, MSFT).\n"
    "- Always include chart file paths in your "
    "response so the user can open them.\n"
    "- Present price targets at 3, 6, and 9 month "
    "marks with percentage change.\n"
    "- State sentiment clearly: Bullish (>+10%), "
    "Neutral, or Bearish (<-10%).\n"
    "- If a ticker returns an error, explain it "
    "and suggest the correct format.\n"
    "- If data is already up to date, skip "
    "re-fetching and proceed to analysis.\n"
    "- Never fabricate prices or statistics "
    "— only report what the tools return."
)


def _parse_tiers(csv: str) -> list[str]:
    """Split a comma-separated model list into a clean list."""
    return [m.strip() for m in csv.split(",") if m.strip()]


# -------------------------------------------------------------------
# Agent class and factory
# -------------------------------------------------------------------


class StockAgent(BaseAgent):
    """Stock analysis agent with N-tier LLM cascade.

    Inherits the agentic loop from :class:`~agents.base.BaseAgent`
    and overrides :meth:`_build_llm` to supply FallbackLLM with
    budget-aware cascading.
    """

    def _build_llm(self) -> FallbackLLM:
        """Instantiate an N-tier :class:`~llm_fallback.FallbackLLM`.

        Returns:
            A :class:`~llm_fallback.FallbackLLM` with Groq tiers
            and Anthropic fallback.
        """
        return FallbackLLM(
            groq_models=self.config.groq_model_tiers,
            anthropic_model="claude-sonnet-4-6",
            temperature=self.config.temperature,
            agent_id=self.config.agent_id,
            token_budget=self.token_budget,
            compressor=self.compressor,
            obs_collector=self.obs_collector,
        )


def create_stock_agent(
    tool_registry: ToolRegistry,
    token_budget: TokenBudget | None = None,
    compressor: MessageCompressor | None = None,
    obs_collector=None,
) -> StockAgent:
    """Factory function that builds a :class:`StockAgent`.

    Args:
        tool_registry: The shared :class:`~tools.registry.ToolRegistry`.
        token_budget: Shared :class:`TokenBudget` instance.
            Created with defaults if ``None``.
        compressor: Shared :class:`MessageCompressor` instance.
            Created with defaults if ``None``.
        obs_collector: Optional
            :class:`~observability.ObservabilityCollector`.

    Returns:
        A ready-to-use :class:`StockAgent` instance.
    """
    settings = get_settings()
    config = AgentConfig(
        agent_id="stock",
        name="Stock Analysis Agent",
        description=(
            "Analyses stocks with 10-year OHLCV data, "
            "technical indicators (SMA, RSI, MACD, "
            "Bollinger Bands), Prophet price forecasting, "
            "and interactive Plotly charts."
        ),
        groq_model_tiers=_parse_tiers(
            settings.groq_model_tiers,
        ),
        temperature=0.0,
        system_prompt=_STOCK_SYSTEM_PROMPT,
        tool_names=[
            "fetch_stock_data",
            "get_stock_info",
            "load_stock_data",
            "fetch_multiple_stocks",
            "get_dividend_history",
            "list_available_stocks",
            "analyse_stock_price",
            "forecast_stock",
            "search_market_news",
        ],
    )
    return StockAgent(
        config=config,
        tool_registry=tool_registry,
        token_budget=token_budget or TokenBudget(),
        compressor=compressor
        or MessageCompressor(
            max_history_turns=settings.max_history_turns,
            max_tool_result_chars=(settings.max_tool_result_chars),
        ),
        obs_collector=obs_collector,
    )

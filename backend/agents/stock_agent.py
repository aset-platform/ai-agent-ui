"""Stock Analysis Agent with Groq-first / Anthropic-fallback LLM.

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
The agent uses FallbackLLM (Groq-first, Anthropic fallback).

Typical usage::

    from tools.registry import ToolRegistry
    from agents.stock_agent import create_stock_agent

    registry = ToolRegistry()
    # (register stock tools first)
    agent = create_stock_agent(registry)
    reply = agent.run("Analyse AAPL")
"""

from agents.base import AgentConfig, BaseAgent
from llm_fallback import FallbackLLM
from tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_STOCK_SYSTEM_PROMPT = (
    "You are a professional stock market analyst with "
    "deep expertise in technical analysis and "
    "time-series price forecasting.\n\n"
    "STANDARD PIPELINE — follow this order for every "
    "single-stock request:\n"
    "1. Call fetch_stock_data to download/update local "
    "OHLCV data (handles delta automatically).\n"
    "2. Call get_stock_info to retrieve company metadata "
    "(name, sector, market cap, PE ratio).\n"
    "3. Call analyse_stock_price for full technical "
    "analysis and chart generation.\n"
    "4. Call forecast_stock for Prophet price targets "
    "and forecast chart.\n"
    "5. Call search_market_news with a query like "
    '"{TICKER} latest news earnings analyst 2026" '
    "to include recent developments in the report.\n"
    "6. Synthesise all results into a clear, "
    "structured report.\n\n"
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

# ---------------------------------------------------------------------------
# Agent class and factory
# ---------------------------------------------------------------------------


class StockAgent(BaseAgent):
    """Stock analysis agent with Groq/Anthropic fallback.

    Inherits the agentic loop from BaseAgent and overrides
    :meth:`_build_llm` to supply FallbackLLM.  The system
    prompt guides the LLM through the fetch -> analyse ->
    forecast pipeline automatically.
    """

    def _build_llm(self) -> FallbackLLM:
        """Instantiate and return a :class:`~llm_fallback.FallbackLLM`.

        Groq is tried first; Anthropic is used as fallback on rate-limit or
        connection errors.  Uses the ``model`` and ``temperature`` values from
        :attr:`~agents.base.BaseAgent.config`.

        Returns:
            A :class:`~llm_fallback.FallbackLLM` instance configured with
            the agent's Groq model name, Anthropic model, and temperature.
        """
        return FallbackLLM(
            groq_model=self.config.model,
            anthropic_model="claude-sonnet-4-6",
            temperature=self.config.temperature,
            agent_id=self.config.agent_id,
        )


def create_stock_agent(tool_registry: ToolRegistry) -> StockAgent:
    """Factory function that builds a :class:`StockAgent` with all stock tools.

    Constructs an :class:`~agents.base.AgentConfig` with
    ``agent_id="stock"``, binds the eight stock analysis tools, and
    returns a fully initialised :class:`StockAgent`.

    Args:
        tool_registry: The shared :class:`~tools.registry.ToolRegistry`
            instance from which all stock tools will be fetched and bound
            to the LLM.

    Returns:
        A ready-to-use :class:`StockAgent` instance.

    Example:
        >>> from tools.registry import ToolRegistry
        >>> registry = ToolRegistry()
        >>> agent = create_stock_agent(registry)
        >>> agent.config.agent_id
        'stock'
    """
    config = AgentConfig(
        agent_id="stock",
        name="Stock Analysis Agent",
        description=(
            "Analyses stocks with 10-year OHLCV data, technical indicators "
            "(SMA, RSI, MACD, Bollinger Bands), Prophet price forecasting, "
            "and interactive Plotly charts."
        ),
        model="openai/gpt-oss-120b",
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
    return StockAgent(config=config, tool_registry=tool_registry)

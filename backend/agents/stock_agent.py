"""Stock Analysis Agent backed by the Groq LLM provider.

:class:`StockAgent` extends :class:`~agents.base.BaseAgent` and is wired
with eight financial analysis tools:

- :func:`~tools.stock_data_tool.fetch_stock_data` — smart delta-fetch OHLCV data
- :func:`~tools.stock_data_tool.get_stock_info` — company metadata
- :func:`~tools.stock_data_tool.load_stock_data` — inspect locally stored data
- :func:`~tools.stock_data_tool.fetch_multiple_stocks` — batch fetch multiple tickers
- :func:`~tools.stock_data_tool.get_dividend_history` — dividend payment history
- :func:`~tools.stock_data_tool.list_available_stocks` — list the local registry
- :func:`~tools.price_analysis_tool.analyse_stock_price` — technical indicators + chart
- :func:`~tools.forecasting_tool.forecast_stock` — Prophet forecast + chart

The agentic loop (inherited from :class:`~agents.base.BaseAgent`) drives
the LLM through the **fetch → analyse → forecast** pipeline automatically.
The system prompt ensures consistent behaviour: always fetch before
analysing, always produce charts, always state price targets and sentiment.

**Switching to Claude Sonnet 4.6** (same 2-line change as GeneralAgent)::

    # Line 1 — change import
    from langchain_anthropic import ChatAnthropic

    # Line 2 — change return in StockAgent._build_llm()
    return ChatAnthropic(model=self.config.model, temperature=self.config.temperature)

Typical usage::

    from tools.registry import ToolRegistry
    from agents.stock_agent import create_stock_agent

    registry = ToolRegistry()
    # (register stock tools first)
    agent = create_stock_agent(registry)
    reply = agent.run("Analyse AAPL")
"""

# To switch to Claude Sonnet 4.6:
#   1. Change import: from langchain_anthropic import ChatAnthropic
#   2. Change return: return ChatAnthropic(model=self.config.model, temperature=self.config.temperature)
#   3. Update model field in create_stock_agent() to "claude-sonnet-4-6"
#   4. Set ANTHROPIC_API_KEY instead of GROQ_API_KEY

from langchain_groq import ChatGroq

from agents.base import AgentConfig, BaseAgent
from tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_STOCK_SYSTEM_PROMPT = """You are a professional stock market analyst with deep expertise \
in technical analysis and time-series price forecasting.

STANDARD PIPELINE — follow this order for every single-stock request:
1. Call fetch_stock_data to download/update local OHLCV data (handles delta automatically).
2. Call get_stock_info to retrieve company metadata (name, sector, market cap, PE ratio).
3. Call analyse_stock_price for full technical analysis and chart generation.
4. Call forecast_stock for Prophet price targets and forecast chart.
5. Synthesise all results into a clear, structured report.

COMPARISON PIPELINE — for multi-stock requests:
1. Call fetch_multiple_stocks with a comma-separated list of tickers.
2. For each ticker: call analyse_stock_price then forecast_stock.
3. Present a side-by-side comparison table sorted by 6-month upside potential.

RULES:
- Use exact ticker symbols (e.g. AAPL, TSLA, RELIANCE.NS, MSFT).
- Always include chart file paths in your response so the user can open them.
- Present price targets at 3, 6, and 9 month marks with percentage change.
- State sentiment clearly: Bullish (>+10%), Neutral, or Bearish (<-10%).
- If a ticker returns an error, explain it and suggest the correct format.
- If data is already up to date, skip re-fetching and proceed to analysis.
- Never fabricate prices or statistics — only report what the tools return."""

# ---------------------------------------------------------------------------
# Agent class and factory
# ---------------------------------------------------------------------------


class StockAgent(BaseAgent):
    """Stock analysis agent powered by ChatGroq and a suite of financial tools.

    Inherits the complete agentic loop from :class:`~agents.base.BaseAgent`
    and only overrides :meth:`_build_llm` to supply the Groq provider.
    The system prompt in :data:`_STOCK_SYSTEM_PROMPT` guides the LLM through
    the fetch → analyse → forecast pipeline automatically.

    Note:
        **Temporary** — using Groq until Anthropic API access is resolved.
        See the module-level comment for the two-line swap to
        :class:`~langchain_anthropic.ChatAnthropic`.
    """

    def _build_llm(self) -> ChatGroq:
        """Instantiate and return a :class:`~langchain_groq.ChatGroq` model.

        Uses the ``model`` and ``temperature`` values from
        :attr:`~agents.base.BaseAgent.config`.

        Returns:
            A :class:`~langchain_groq.ChatGroq` instance configured with
            the agent's model name and sampling temperature.
        """
        return ChatGroq(model=self.config.model, temperature=self.config.temperature)


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
            "and interactive Plotly charts. Supports single stocks and comparisons."
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
        ],
    )
    return StockAgent(config=config, tool_registry=tool_registry)

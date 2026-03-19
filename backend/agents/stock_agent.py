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

import re

from agents.base import AgentConfig, BaseAgent
from agents.report_builder import build_report
from config import get_settings
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
    "STEP 1 (data): YOUR FIRST RESPONSE MUST ONLY "
    "call fetch_stock_data and get_stock_info. "
    "Do NOT call any other tools in step 1. "
    "Wait for results before proceeding.\n"
    "STEP 2 (analysis): Only AFTER step 1 results "
    "are returned, call analyse_stock_price, "
    "forecast_stock, and search_market_news.\n"
    "STEP 3 (verdict): The data tables are rendered "
    "automatically by the system. You ONLY need to "
    "provide:\n"
    "  (a) A Buy/Hold/Sell recommendation with "
    "confidence percentage\n"
    "  (b) 2-3 key risks as bullet points\n"
    "  (c) A 3-4 sentence investment thesis\n"
    "Do NOT repeat prices, indicators, or forecast "
    "numbers — they are already in the report.\n\n"
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


_REPORT_TOOLS = frozenset({
    "get_stock_info",
    "analyse_stock_price",
    "forecast_stock",
})


class StockAgent(BaseAgent):
    """Stock analysis agent with N-tier LLM cascade.

    Inherits the agentic loop from :class:`~agents.base.BaseAgent`
    and overrides :meth:`_build_llm` to supply FallbackLLM with
    budget-aware cascading.  In test mode, Anthropic is disabled.

    The final response is post-processed by
    :func:`~agents.report_builder.build_report` to prepend
    deterministic data tables before the LLM's verdict.
    """

    def format_response(
        self,
        llm_text: str,
        messages: list,
    ) -> str:
        """Prepend deterministic report template to verdict.

        Scans the message history for tool call results from
        ``get_stock_info``, ``analyse_stock_price``, and
        ``forecast_stock``.  Builds the data template and
        prepends it to the LLM's verdict text.

        Args:
            llm_text: The LLM's final response (verdict only).
            messages: Full message history from the loop.

        Returns:
            Combined markdown: data sections + verdict.
        """
        from langchain_core.messages import (
            AIMessage,
            ToolMessage,
        )

        # Collect tool results from message history.
        tool_results: dict[str, str] = {}
        # Map tool_call_id → tool_name from AIMessages.
        call_id_to_name: dict[str, str] = {}
        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    call_id_to_name[tc["id"]] = tc["name"]
            elif isinstance(msg, ToolMessage):
                name = call_id_to_name.get(
                    msg.tool_call_id, ""
                )
                if name in _REPORT_TOOLS:
                    tool_results[name] = msg.content

        if not tool_results:
            return llm_text

        # Detect ticker from analysis output.
        ticker = ""
        for txt in tool_results.values():
            m = re.search(
                r"(?:ANALYSIS|FORECAST):\s*(\S+)",
                txt,
            )
            if m:
                ticker = m.group(1)
                break

        template = build_report(tool_results, ticker)
        if not template.strip():
            return llm_text

        # Combine: data sections + verdict.
        return (
            f"{template}\n"
            f"### Verdict\n\n"
            f"{llm_text}"
        )

    # _build_llm and _build_synthesis_llm inherited
    # from BaseAgent — no override needed.


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

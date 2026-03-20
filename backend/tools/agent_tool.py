"""Factory for creating the search_market_news agent-to-agent tool.

Wraps the :class:`~agents.base.BaseAgent` agentic loop as a LangChain
``@tool`` so that the Stock Agent can delegate web searches to the General
Agent (which already has ``search_web`` bound) without requiring a separate
SerpAPI call in the stock tool layer.

The tool is constructed at server startup time (after the General
Agent has been created) and registered into the shared ToolRegistry
before the Stock Agent is instantiated.

Typical usage (in ``main.py``)::

    general_agent = create_general_agent(tool_registry)
    news_tool = create_search_market_news_tool(general_agent)
    tool_registry.register(news_tool)
    stock_agent = create_stock_agent(tool_registry)
"""

import logging

from agents.base import BaseAgent
from langchain_core.tools import BaseTool, tool
from validation import validate_search_query

logger = logging.getLogger(__name__)


def create_search_market_news_tool(general_agent: BaseAgent) -> BaseTool:
    """Build a LangChain tool that delegates searches to the General Agent.

    The returned tool calls :meth:`~agents.base.BaseAgent.run` on
    ``general_agent`` with an empty history so it always runs a fresh
    agentic loop.  The General Agent uses ``search_web`` internally and
    returns a synthesised string suitable for the Stock Agent's context.

    Args:
        general_agent: A fully initialised General Agent instance that has
            ``search_web`` bound to its LLM.

    Returns:
        A LangChain :class:`~langchain_core.tools.BaseTool` named
        ``search_market_news``, ready to register in a
        :class:`~tools.registry.ToolRegistry`.

    Example:
        >>> from tools.registry import ToolRegistry
        >>> from agents.general_agent import create_general_agent
        >>> registry = ToolRegistry()
        >>> agent = create_general_agent(registry)
        >>> news_tool = create_search_market_news_tool(agent)
        >>> news_tool.name
        'search_market_news'
    """

    @tool
    def search_market_news(query: str) -> str:
        """Search the web for recent news, earnings, analyst reports, or macro
        developments relevant to a stock or market topic.

        Call this tool before completing any stock analysis report to enrich
        it with current context.  Use a targeted query such as
        ``"AAPL earnings Q1 2026 analyst outlook"`` or
        ``"TSLA latest news 2026"``.

        Args:
            query: A specific search query, e.g. ``"AAPL earnings Q1 2026
                analyst outlook"``.

        Returns:
            Web search results as a plain-text string, or an error message
            if the search fails.

        Example:
            >>> result = search_market_news.invoke(
            ...     {"query": "AAPL latest news 2026"}
            ... )
            >>> isinstance(result, str)
            True
        """
        err = validate_search_query(query)
        if err:
            return f"News search failed: {err}"
        logger.info(
            "search_market_news | query=%r",
            query,
        )
        try:
            result = general_agent.run(
                user_input=query,
                history=[],
                max_iterations=2,
            )
            logger.debug(
                "search_market_news result length:"
                " %d chars",
                len(result),
            )
            return result
        except Exception as exc:
            logger.error("search_market_news failed: %s", exc, exc_info=True)
            return f"News search failed: {exc}"

    return search_market_news

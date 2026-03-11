"""Tool and agent registration bootstrap.

Extracted from :class:`~main.ChatServer` to reduce module size.
Called once during server startup.

Functions
---------
- :func:`setup_tools` — register all LangChain tools
- :func:`setup_agents` — instantiate and register all agents
"""

import logging

from agents.general_agent import create_general_agent
from agents.stock_agent import create_stock_agent
from tools.agent_tool import create_search_market_news_tool
from tools.forecasting_tool import forecast_stock
from tools.price_analysis_tool import analyse_stock_price
from tools.search_tool import search_web
from tools.stock_data_tool import (
    fetch_multiple_stocks,
    fetch_quarterly_results,
    fetch_stock_data,
    get_dividend_history,
    get_stock_info,
    list_available_stocks,
    load_stock_data,
)
from tools.time_tool import get_current_time

_logger = logging.getLogger(__name__)


def setup_tools(registry):
    """Register all available LangChain tools.

    Args:
        registry: A :class:`~tools.registry.ToolRegistry`
            instance to populate.
    """
    registry.register(get_current_time)
    registry.register(search_web)
    registry.register(fetch_stock_data)
    registry.register(get_stock_info)
    registry.register(load_stock_data)
    registry.register(fetch_multiple_stocks)
    registry.register(get_dividend_history)
    registry.register(list_available_stocks)
    registry.register(fetch_quarterly_results)
    registry.register(analyse_stock_price)
    registry.register(forecast_stock)
    _logger.info("Tools registered: %s", registry.list_names())


def setup_agents(
    tool_registry,
    agent_registry,
    token_budget,
    compressor,
):
    """Instantiate and register all agents.

    The general agent must be created first because the
    stock agent's ``search_market_news`` tool depends on it.

    Args:
        tool_registry: Populated
            :class:`~tools.registry.ToolRegistry`.
        agent_registry: Empty
            :class:`~agents.registry.AgentRegistry`.
        token_budget: Shared
            :class:`~token_budget.TokenBudget`.
        compressor: Shared
            :class:`~message_compressor.MessageCompressor`.
    """
    general = create_general_agent(
        tool_registry,
        token_budget=token_budget,
        compressor=compressor,
    )
    agent_registry.register(general)

    # News tool depends on the general agent instance.
    news_tool = create_search_market_news_tool(general)
    tool_registry.register(news_tool)

    stock = create_stock_agent(
        tool_registry,
        token_budget=token_budget,
        compressor=compressor,
    )
    agent_registry.register(stock)

    _logger.info(
        "Agents registered: %s",
        [a["id"] for a in agent_registry.list_agents()],
    )

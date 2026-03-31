"""Tool and agent registration bootstrap.

Extracted from :class:`~main.ChatServer` to reduce module size.
Called once during server startup.

Functions
---------
- :func:`setup_tools` — register all LangChain tools
- :func:`setup_agents` — instantiate and register all agents
- :func:`setup_graph` — build the LangGraph supervisor graph
"""

import logging

from agents.general_agent import create_general_agent
from agents.stock_agent import create_stock_agent
from tools.agent_tool import (
    create_search_market_news_tool,
)
from tools.forecasting_tool import forecast_stock
from tools.price_analysis_tool import (
    analyse_stock_price,
)
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

    # Register tiered news tools for Research Agent
    try:
        from tools.news_tools import (
            get_analyst_recommendations,
            get_ticker_news,
            search_financial_news,
        )

        registry.register(get_ticker_news)
        registry.register(get_analyst_recommendations)
        registry.register(search_financial_news)
    except Exception:
        _logger.warning(
            "News tools registration failed",
            exc_info=True,
        )

    # Register portfolio tools for Portfolio Agent
    try:
        from tools.portfolio_tools import (
            get_dividend_projection,
            get_portfolio_holdings,
            get_portfolio_performance,
            get_portfolio_summary,
            get_risk_metrics,
            get_sector_allocation,
            suggest_rebalancing,
        )

        registry.register(get_portfolio_holdings)
        registry.register(get_portfolio_performance)
        registry.register(get_sector_allocation)
        registry.register(get_dividend_projection)
        registry.register(suggest_rebalancing)
        registry.register(get_portfolio_summary)
        registry.register(get_risk_metrics)
    except Exception:
        _logger.warning(
            "Portfolio tools registration failed",
            exc_info=True,
        )

    # Register forecast tools for Forecaster Agent
    try:
        from tools.forecast_tools import (
            get_forecast_summary,
            get_portfolio_forecast,
        )

        registry.register(get_forecast_summary)
        registry.register(get_portfolio_forecast)
    except Exception:
        _logger.warning(
            "Forecast tools registration failed",
            exc_info=True,
        )

    # Register sentiment tools for Sentiment Agent
    try:
        from tools.sentiment_agent import (
            get_cached_sentiment,
            get_market_sentiment,
            score_ticker_sentiment,
        )

        registry.register(score_ticker_sentiment)
        registry.register(get_cached_sentiment)
        registry.register(get_market_sentiment)
    except Exception:
        _logger.warning(
            "Sentiment tools registration failed",
            exc_info=True,
        )

    # Sector discovery
    try:
        from tools.sector_discovery_tool import (
            suggest_sector_stocks,
        )

        registry.register(suggest_sector_stocks)
    except Exception:
        _logger.warning(
            "Sector discovery tool registration failed",
            exc_info=True,
        )

    _logger.info(
        "Tools registered: %s",
        registry.list_names(),
    )


def setup_agents(
    tool_registry,
    agent_registry,
    token_budget,
    compressor,
    obs_collector=None,
):
    """Instantiate and register legacy agents.

    Kept for feature-flag fallback when
    ``use_langgraph=False``.

    Args:
        tool_registry: Populated ToolRegistry.
        agent_registry: Empty AgentRegistry.
        token_budget: Shared TokenBudget.
        compressor: Shared MessageCompressor.
        obs_collector: Optional ObservabilityCollector.
    """
    general = create_general_agent(
        tool_registry,
        token_budget=token_budget,
        compressor=compressor,
        obs_collector=obs_collector,
    )
    agent_registry.register(general)

    # News tool depends on the general agent instance.
    news_tool = create_search_market_news_tool(general)
    tool_registry.register(news_tool)

    stock = create_stock_agent(
        tool_registry,
        token_budget=token_budget,
        compressor=compressor,
        obs_collector=obs_collector,
    )
    agent_registry.register(stock)

    _logger.info(
        "Legacy agents registered: %s",
        [a["id"] for a in agent_registry.list_agents()],
    )


def setup_graph(
    tool_registry,
    token_budget,
    compressor,
    obs_collector=None,
):
    """Build the LangGraph supervisor graph.

    Args:
        tool_registry: Populated ToolRegistry.
        token_budget: Shared TokenBudget.
        compressor: Shared MessageCompressor.
        obs_collector: Optional ObservabilityCollector.

    Returns:
        Compiled LangGraph ``CompiledStateGraph``.
    """
    from agents.graph import build_supervisor_graph
    from config import get_settings
    from llm_fallback import FallbackLLM

    settings = get_settings()

    def _parse_tiers(csv: str) -> list[str]:
        return [t.strip() for t in csv.split(",") if t.strip()]

    def llm_factory(agent_id: str = "graph"):
        """Create a FallbackLLM for a sub-agent."""
        env = settings.ai_agent_ui_env
        if env == "test":
            tiers = _parse_tiers(
                settings.test_model_tiers,
            )
            anthropic = None
        else:
            tiers = _parse_tiers(
                settings.groq_model_tiers,
            )
            anthropic = "claude-sonnet-4-6"

        ollama = (
            settings.ollama_model
            if settings.ollama_enabled
            else None
        )

        return FallbackLLM(
            groq_models=tiers,
            anthropic_model=anthropic,
            temperature=0,
            agent_id=agent_id,
            token_budget=token_budget,
            compressor=compressor,
            obs_collector=obs_collector,
            cascade_profile="tool",
            ollama_model=ollama,
            ollama_first=False,
        )

    graph = build_supervisor_graph(
        tool_registry,
        llm_factory,
        settings,
    )
    _logger.info("LangGraph supervisor graph built")

    # Start daily gap filler background job
    try:
        from jobs.gap_filler import start_gap_filler

        start_gap_filler()
    except Exception:
        _logger.warning(
            "Gap filler start failed",
            exc_info=True,
        )

    return graph

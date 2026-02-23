"""FastAPI application entry point for the AI Agent backend.

Architecture overview
---------------------
All server-level state is encapsulated in :class:`ChatServer`, which owns:

- A :class:`~tools.registry.ToolRegistry` — holds all callable LangChain tools.
- An :class:`~agents.registry.AgentRegistry` — holds all agent instances.
- The :class:`~fastapi.FastAPI` application with CORS and route registration.

Module-level code at the bottom of this file creates the single
:class:`ChatServer` instance and exposes ``app`` for uvicorn::

    uvicorn main:app --port 8181 --reload

HTTP API
--------
``POST /chat``
    Accepts a :class:`ChatRequest` (``message``, optional ``history``, optional
    ``agent_id``).  Dispatches to the named agent's
    :meth:`~agents.base.BaseAgent.run` method and returns a
    :class:`ChatResponse`.

``GET /agents``
    Returns a JSON list of all registered agents (id, name, description).

Error handling
--------------
- ``404`` — the requested ``agent_id`` is not registered.
- ``500`` — an unhandled exception occurred inside the agent loop.

Both cases return proper :class:`~fastapi.HTTPException` responses rather
than embedding error strings in a ``200`` body.
"""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import Settings, get_settings
from logging_config import setup_logging
from tools.registry import ToolRegistry
from tools.time_tool import get_current_time
from tools.search_tool import search_web
from agents.registry import AgentRegistry
from agents.general_agent import create_general_agent
from tools.agent_tool import create_search_market_news_tool

# === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 ===
# Does not modify existing chat agent functionality.
# Stock agent is dispatched via agent_id="stock" on POST /chat.
from tools.stock_data_tool import (
    fetch_stock_data,
    get_stock_info,
    load_stock_data,
    fetch_multiple_stocks,
    get_dividend_history,
    list_available_stocks,
)
from tools.price_analysis_tool import analyse_stock_price
from tools.forecasting_tool import forecast_stock
from agents.stock_agent import create_stock_agent
# === END STOCK AGENT ROUTING ===


class ChatRequest(BaseModel):
    """Request body for the ``POST /chat`` endpoint.

    Attributes:
        message: The user's latest message text.
        history: Previous conversation turns, oldest first.  Each element
            must be a dict with ``"role"`` (``"user"`` or ``"assistant"``)
            and ``"content"`` keys.  Defaults to an empty list.
        agent_id: ID of the agent that should handle the request.
            Must match an ID registered in :class:`~agents.registry.AgentRegistry`.
            Defaults to ``"general"``.
    """

    message: str
    history: list[dict] = []
    agent_id: str = "general"


class ChatResponse(BaseModel):
    """Response body for the ``POST /chat`` endpoint.

    Attributes:
        response: The agent's natural-language reply.
        agent_id: The ID of the agent that produced the response, echoed
            from the request.
    """

    response: str
    agent_id: str


class ChatServer:
    """Encapsulates all server-level state and wires the FastAPI application.

    Instantiating this class registers all tools and agents, then builds
    the ASGI application.  The order of operations in :meth:`__init__` is
    significant: tools must be registered before agents because agents
    fetch their tools from the registry during :meth:`~agents.base.BaseAgent._setup`.

    Attributes:
        logger: Logger instance named after this module.
        settings: Application configuration from :func:`~config.get_settings`.
        tool_registry: Registry holding all available LangChain tools.
        agent_registry: Registry holding all available agent instances.
        app: The configured :class:`~fastapi.FastAPI` ASGI application.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialise the server, register tools and agents, and build the app.

        Args:
            settings: Validated application settings, typically obtained
                from :func:`~config.get_settings`.
        """
        self.logger = logging.getLogger(__name__)
        self.settings = settings
        self.tool_registry = ToolRegistry()
        self.agent_registry = AgentRegistry()
        # Tools must be registered before agents so agents can bind them.
        self._register_tools()
        self._register_agents()
        self.app = self._create_app()

    def _register_tools(self) -> None:
        """Populate the tool registry with all available tools.

        Add new tools here as the system grows.  Each registered tool
        becomes available to any agent that lists its name in
        :attr:`~agents.base.AgentConfig.tool_names`.
        """
        self.tool_registry.register(get_current_time)
        self.tool_registry.register(search_web)

        # === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 ===
        self.tool_registry.register(fetch_stock_data)
        self.tool_registry.register(get_stock_info)
        self.tool_registry.register(load_stock_data)
        self.tool_registry.register(fetch_multiple_stocks)
        self.tool_registry.register(get_dividend_history)
        self.tool_registry.register(list_available_stocks)
        self.tool_registry.register(analyse_stock_price)
        self.tool_registry.register(forecast_stock)
        # === END STOCK AGENT ROUTING ===

        self.logger.info("Tools registered: %s", self.tool_registry.list_names())

    def _register_agents(self) -> None:
        """Instantiate and register all agents with the agent registry.

        Add new agent factory calls here to make them available for
        routing via the ``agent_id`` field on ``POST /chat``.
        """
        general = create_general_agent(self.tool_registry)
        self.agent_registry.register(general)

        # Register news tool now that general agent exists (stock agent depends on it)
        search_market_news = create_search_market_news_tool(general)
        self.tool_registry.register(search_market_news)

        # === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 ===
        stock = create_stock_agent(self.tool_registry)
        self.agent_registry.register(stock)
        # === END STOCK AGENT ROUTING ===

        self.logger.info(
            "Agents registered: %s",
            [a["id"] for a in self.agent_registry.list_agents()],
        )

    def _create_app(self) -> FastAPI:
        """Build and return the configured FastAPI ASGI application.

        Attaches CORS middleware (open to all origins — tighten before
        production deployment) and registers all HTTP route handlers.

        Returns:
            A fully configured :class:`~fastapi.FastAPI` instance.
        """
        app = FastAPI(title="AI Agent API")
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        # Register handlers by passing bound methods to the route decorators.
        app.post("/chat", response_model=ChatResponse)(self._chat_handler)
        app.get("/agents")(self._list_agents_handler)
        return app

    async def _chat_handler(self, req: ChatRequest) -> ChatResponse:
        """Handle ``POST /chat`` requests.

        Looks up the requested agent, delegates to its
        :meth:`~agents.base.BaseAgent.run` method, and returns the result.

        Args:
            req: Validated :class:`ChatRequest` from the HTTP body.

        Returns:
            A :class:`ChatResponse` containing the agent's reply and the
            echoed ``agent_id``.

        Raises:
            HTTPException: ``404`` if ``req.agent_id`` is not registered.
            HTTPException: ``500`` if the agent raises an unhandled exception.
        """
        agent = self.agent_registry.get(req.agent_id)
        if agent is None:
            raise HTTPException(
                status_code=404, detail=f"Agent '{req.agent_id}' not found"
            )
        try:
            result = agent.run(req.message, req.history)
        except Exception as e:
            self.logger.error("Chat handler error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Agent execution failed")
        return ChatResponse(response=result, agent_id=req.agent_id)

    async def _list_agents_handler(self) -> dict:
        """Handle ``GET /agents`` requests.

        Returns:
            A dict with a single ``"agents"`` key whose value is a list
            of agent summary dicts (``id``, ``name``, ``description``).

        Example response::

            {
                "agents": [
                    {
                        "id": "general",
                        "name": "General Agent",
                        "description": "A general-purpose agent ..."
                    }
                ]
            }
        """
        return {"agents": self.agent_registry.list_agents()}


# ---------------------------------------------------------------------------
# Module-level startup — executed once when uvicorn imports this module.
# ---------------------------------------------------------------------------

settings = get_settings()
setup_logging(level=settings.log_level, log_to_file=settings.log_to_file)
server = ChatServer(settings)
app = server.app  # uvicorn entry point: uvicorn main:app --port 8181 --reload

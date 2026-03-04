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
    :class:`ChatResponse`.  Bounded by
    :attr:`~config.Settings.agent_timeout_seconds` (HTTP 504 on timeout).

``POST /chat/stream``
    Same request body as ``POST /chat``.  Returns an
    ``application/x-ndjson`` :class:`~fastapi.responses.StreamingResponse`
    that emits one JSON event per line as the agentic loop progresses.
    Event types: ``thinking``, ``tool_start``, ``tool_done``, ``warning``,
    ``final``, ``error``, ``timeout``.

``GET /agents``
    Returns a JSON list of all registered agents (id, name, description).

Error handling
--------------
- ``404`` — the requested ``agent_id`` is not registered.
- ``504`` — the agent did not respond within the configured timeout.
- ``500`` — an unhandled exception occurred inside the agent loop.

All cases return proper :class:`~fastapi.HTTPException` responses rather
than embedding error strings in a ``200`` body.
"""

import asyncio
import json
import logging
import os
import queue
import sys
import threading
import time

# Make the project root importable so that the auth/ package (which lives
# alongside backend/ rather than inside it) can be found by Python.
# _project_root is module-level because sys.path manipulation must happen
# before any project imports are resolved.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agents.general_agent import create_general_agent
from agents.registry import AgentRegistry
from agents.stock_agent import create_stock_agent
from config import Settings, get_settings
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from logging_config import setup_logging
from models import ChatRequest, ChatResponse
from tools.agent_tool import create_search_market_news_tool
from tools.forecasting_tool import forecast_stock
from tools.price_analysis_tool import analyse_stock_price
from tools.registry import ToolRegistry
from tools.search_tool import search_web

# === STOCK AGENT ROUTING — ADDED BY PLAN PROMPT 8 ===
from tools.stock_data_tool import (
    fetch_multiple_stocks,
    fetch_stock_data,
    get_dividend_history,
    get_stock_info,
    list_available_stocks,
    load_stock_data,
)
from tools.time_tool import get_current_time

from auth.api import create_auth_router

# === END STOCK AGENT ROUTING ===


logger = logging.getLogger(__name__)


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

        self.logger.info(
            "Tools registered: %s", self.tool_registry.list_names()
        )

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
        app.post("/chat/stream")(self._chat_stream_handler)
        app.get("/agents")(self._list_agents_handler)

        # Auth + user management router (mounts /auth/*, /users/*, /admin/*)
        app.include_router(create_auth_router())

        # Serve uploaded avatars as static files at /avatars/{filename}
        _avatars_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "data",
            "avatars",
        )
        os.makedirs(_avatars_dir, exist_ok=True)
        app.mount(
            "/avatars", StaticFiles(directory=_avatars_dir), name="avatars"
        )

        return app

    async def _chat_handler(self, req: ChatRequest) -> ChatResponse:
        """Handle ``POST /chat`` requests.

        Looks up the requested agent, delegates to its
        :meth:`~agents.base.BaseAgent.run` method, and returns the result.
        The call is bounded by :attr:`~config.Settings.agent_timeout_seconds`;
        if the loop does not complete in time a ``504`` is returned.

        Args:
            req: Validated :class:`ChatRequest` from the HTTP body.

        Returns:
            A :class:`ChatResponse` containing the agent's reply and the
            echoed ``agent_id``.

        Raises:
            HTTPException: ``404`` if ``req.agent_id`` is not registered.
            HTTPException: ``504`` if the agent does not respond within the
                configured timeout.
            HTTPException: ``500`` if the agent raises an unhandled exception.
        """
        agent = self.agent_registry.get(req.agent_id)
        if agent is None:
            raise HTTPException(
                status_code=404, detail=f"Agent '{req.agent_id}' not found"
            )
        try:
            loop = asyncio.get_event_loop()
            future = loop.run_in_executor(
                None, agent.run, req.message, req.history
            )
            result = await asyncio.wait_for(
                future, timeout=self.settings.agent_timeout_seconds
            )
        except asyncio.TimeoutError:
            self.logger.warning(
                "Agent '%s' timed out after %ds",
                req.agent_id,
                self.settings.agent_timeout_seconds,
            )
            raise HTTPException(status_code=504, detail="Agent timed out")
        except Exception as e:
            self.logger.error("Chat handler error: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500, detail="Agent execution failed"
            )
        return ChatResponse(response=result, agent_id=req.agent_id)

    async def _chat_stream_handler(
        self, req: ChatRequest
    ) -> StreamingResponse:
        """Handle ``POST /chat/stream`` requests via NDJSON streaming.

        Streams one JSON event per line as the agentic loop progresses.
        The generator runs in a daemon thread; events are passed via a
        :class:`queue.Queue`.  If no event arrives within
        :attr:`~config.Settings.agent_timeout_seconds` seconds a
        ``timeout`` event is emitted and the stream is closed.

        Event types emitted:

        - ``thinking`` — LLM invocation starting.
        - ``tool_start`` — A tool call is about to execute.
        - ``tool_done`` — A tool call completed.
        - ``warning`` — ``MAX_ITERATIONS`` was reached.
        - ``final`` — Loop complete with full response.
        - ``error`` — An exception occurred inside the agent.
        - ``timeout`` — The overall deadline was exceeded.

        Args:
            req: Validated :class:`ChatRequest` from the HTTP body.

        Returns:
            A :class:`~fastapi.responses.StreamingResponse` with
            ``application/x-ndjson`` content type.

        Raises:
            HTTPException: ``404`` if ``req.agent_id`` is not registered.
        """
        agent = self.agent_registry.get(req.agent_id)
        if agent is None:
            raise HTTPException(
                status_code=404, detail=f"Agent '{req.agent_id}' not found"
            )

        timeout = self.settings.agent_timeout_seconds

        def generate():
            """Run the agent stream generator in a thread and yield events."""
            event_queue: queue.Queue = queue.Queue()

            def run() -> None:
                """Execute ``agent.stream()`` and push events to the queue."""
                try:
                    for event in agent.stream(req.message, req.history):
                        event_queue.put(event)
                except Exception:
                    pass  # error event already yielded by stream()
                finally:
                    event_queue.put(None)  # sentinel — signals end of stream

            worker = threading.Thread(target=run, daemon=True)
            worker.start()

            start = time.time()
            while True:
                elapsed = time.time() - start
                if elapsed >= timeout:
                    yield json.dumps(
                        {
                            "type": "timeout",
                            "message": f"Agent timed out after {timeout}s",
                        }
                    ) + "\n"
                    break
                remaining = timeout - elapsed
                try:
                    item = event_queue.get(timeout=min(remaining, 1.0))
                    if item is None:
                        break
                    yield item
                except queue.Empty:
                    if time.time() - start >= timeout:
                        yield json.dumps(
                            {
                                "type": "timeout",
                                "message": f"Agent timed out after {timeout}s",
                            }
                        ) + "\n"
                        break

            worker.join(timeout=2)

        return StreamingResponse(generate(), media_type="application/x-ndjson")

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

# _settings is module-level because it must be initialised before the
# ChatServer instance is created and before logging is configured.
_settings = get_settings()
setup_logging(level=_settings.log_level, log_to_file=_settings.log_to_file)

# Export all settings loaded by Pydantic into os.environ so that third-party
# libraries (LangChain/Groq/SerpAPI) and auth/dependencies.py, which read
# os.environ directly, can find them even when values are only in backend/.env.
# _env_exports is module-level because it drives one-time environment
# initialisation that must complete before any imports consume os.environ.
_env_exports = {
    "GROQ_API_KEY": _settings.groq_api_key,
    "ANTHROPIC_API_KEY": _settings.anthropic_api_key,
    "SERPAPI_API_KEY": _settings.serpapi_api_key,
    "JWT_SECRET_KEY": _settings.jwt_secret_key,
    "ACCESS_TOKEN_EXPIRE_MINUTES": str(_settings.access_token_expire_minutes),
    "REFRESH_TOKEN_EXPIRE_DAYS": str(_settings.refresh_token_expire_days),
}
for _key, _val in _env_exports.items():
    if _val and _key not in os.environ:
        os.environ[_key] = _val

# _server is module-level because uvicorn requires ``app`` to be a
# module-level name; _server owns the app instance.
_server = ChatServer(_settings)
app = _server.app  # uvicorn entry point: uvicorn main:app --port 8181 --reload

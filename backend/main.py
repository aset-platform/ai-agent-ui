"""FastAPI application entry point for the AI Agent backend.

Thin orchestrator that delegates to:

- :mod:`bootstrap` — tool and agent registration
- :mod:`routes` — HTTP route handlers and middleware

Start with::

    uvicorn main:app --port 8181 --reload
"""

import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor

# Make the project root importable so that the auth/ package
# (which lives alongside backend/) can be found by Python.
# _project_root is module-level because sys.path manipulation
# must happen before any project imports are resolved.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agents.registry import AgentRegistry  # noqa: E402
from bootstrap import setup_agents, setup_tools  # noqa: E402
from config import Settings, get_settings  # noqa: E402
from logging_config import setup_logging  # noqa: E402
from message_compressor import MessageCompressor  # noqa: E402
from routes import create_app  # noqa: E402
from token_budget import TokenBudget  # noqa: E402
from tools.registry import ToolRegistry  # noqa: E402

logger = logging.getLogger(__name__)


class ChatServer:
    """Thin orchestrator that wires registries and the ASGI app.

    Attributes:
        settings: Application configuration.
        tool_registry: All available LangChain tools.
        agent_registry: All available agent instances.
        app: The configured FastAPI ASGI application.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialise the server.

        Args:
            settings: Validated application settings from
                :func:`~config.get_settings`.
        """
        self.settings = settings
        self.tool_registry = ToolRegistry()
        self.agent_registry = AgentRegistry()

        self.executor = ThreadPoolExecutor(max_workers=10)

        self.token_budget = TokenBudget()
        self.compressor = MessageCompressor(
            max_history_turns=settings.max_history_turns,
            max_tool_result_chars=(settings.max_tool_result_chars),
        )

        setup_tools(self.tool_registry)
        setup_agents(
            self.tool_registry,
            self.agent_registry,
            self.token_budget,
            self.compressor,
        )
        self.app = create_app(
            self.agent_registry,
            self.executor,
            self.settings,
        )


# -------------------------------------------------------------------
# Module-level startup — executed once when uvicorn imports this
# module.
# -------------------------------------------------------------------

_settings = get_settings()
setup_logging(
    level=_settings.log_level,
    log_to_file=_settings.log_to_file,
)

# Export settings to os.environ for third-party libraries.
_env_exports = {
    "GROQ_API_KEY": _settings.groq_api_key,
    "ANTHROPIC_API_KEY": _settings.anthropic_api_key,
    "SERPAPI_API_KEY": _settings.serpapi_api_key,
    "JWT_SECRET_KEY": _settings.jwt_secret_key,
    "ACCESS_TOKEN_EXPIRE_MINUTES": str(
        _settings.access_token_expire_minutes,
    ),
    "REFRESH_TOKEN_EXPIRE_DAYS": str(
        _settings.refresh_token_expire_days,
    ),
    "REDIS_URL": _settings.redis_url,
}
for _key, _val in _env_exports.items():
    if _val and _key not in os.environ:
        os.environ[_key] = _val

_server = ChatServer(_settings)
app = _server.app

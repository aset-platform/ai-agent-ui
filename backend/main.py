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
from bootstrap import (  # noqa: E402
    setup_agents,
    setup_graph,
    setup_tools,
)
from config import Settings, get_settings  # noqa: E402
from logging_config import setup_logging  # noqa: E402
from message_compressor import MessageCompressor  # noqa: E402
from observability import ObservabilityCollector  # noqa: E402
from routes import create_app  # noqa: E402
from token_budget import TokenBudget  # noqa: E402,F401
from tools.registry import ToolRegistry  # noqa: E402

logger = logging.getLogger(__name__)


def _ensure_iceberg_tables() -> None:
    """Create missing Iceberg tables (idempotent)."""
    try:
        from auth.create_tables import (
            create_tables as create_auth_tables,
        )
        from stocks.create_tables import (
            create_tables as create_stock_tables,
        )
        from backend.algo.iceberg_init import create_algo_tables
        create_auth_tables()
        create_stock_tables()
        create_algo_tables()
        logger.info("Iceberg tables ensured.")
    except Exception:
        logger.warning(
            "Iceberg table init failed — "
            "tables may need manual creation",
            exc_info=True,
        )


async def _run_startup_hooks() -> None:
    """Run async startup tasks after Iceberg tables are ready.

    Currently:
    - paper replay rebuilder: restores risk_state from today's
      algo.events after an unexpected restart.

    Failures are caught and logged as warnings so they never
    prevent the backend from booting.
    """
    from backend.algo.paper.replay_rebuilder import (
        rebuild_all as _rebuild_paper,
    )
    try:
        result = await _rebuild_paper()
        logger.info(
            "Paper replay rebuilder: %s",
            result,
        )
    except Exception as exc:
        logger.warning(
            "Paper replay rebuilder failed at startup: %s", exc,
        )

    # ASETPLTFRM-375 — replay the daily caps reset if the scheduled
    # 09:00 IST trigger was missed (e.g. backend restarted mid-day).
    # No-op if already reset today or pre-09:00 IST. Failure is
    # warned-and-swallowed; the runtime guards already enforce caps
    # so a missed catch-up just means stale counters until next day.
    from backend.algo.jobs.live_caps_reset import run_if_missed_today
    try:
        caps_result = await run_if_missed_today()
        logger.info(
            "Live caps reset catch-up: %s", caps_result,
        )
    except Exception as exc:
        logger.warning(
            "Live caps reset catch-up failed at startup: %s", exc,
        )

    # ASETPLTFRM-379 — sweep zombie 'running' algo.runs rows whose
    # backing asyncio task was killed by the previous restart.
    # Without this, postback reconciliation walks stale runs every
    # request and the dashboard "currently running" panel shows
    # ghost entries.
    try:
        import os
        from backend.algo.backtest.runs_repo import BacktestRunsRepo
        from backend.db.engine import get_session_factory
        thr = int(os.environ.get(
            "ALGO_RUN_STALE_THRESHOLD_SECONDS", "3600",
        ) or "3600")
        runs_repo = BacktestRunsRepo()
        factory = get_session_factory()
        async with factory() as session:
            crashed_ids = (
                await runs_repo.mark_stale_running_as_crashed(
                    session, threshold_seconds=thr,
                )
            )
        logger.info(
            "Zombie runs sweep: marked %d run(s) crashed "
            "(threshold %ds)", len(crashed_ids), thr,
        )
    except Exception as exc:
        logger.warning(
            "Zombie runs sweep failed at startup: %s", exc,
        )


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
        _ensure_iceberg_tables()

        from db.engine import get_session_factory

        self._pg_session_factory = get_session_factory()
        logger.info("PostgreSQL async engine ready")

        self.tool_registry = ToolRegistry()
        self.agent_registry = AgentRegistry()

        self.executor = ThreadPoolExecutor(max_workers=10)

        from token_budget import get_token_budget

        self.token_budget = get_token_budget()
        self.compressor = MessageCompressor(
            max_history_turns=settings.max_history_turns,
            max_tool_result_chars=(settings.max_tool_result_chars),
        )
        # Observability with Iceberg persistence.
        from tools._stock_shared import _get_repo

        _obs_repo = _get_repo()
        self.obs_collector = ObservabilityCollector(
            repo=_obs_repo,
        )
        from observability import set_obs_collector

        set_obs_collector(self.obs_collector)

        # PII anonymizer for LangSmith + LangFuse.
        from tracing import setup_anonymizer

        setup_anonymizer()

        setup_tools(self.tool_registry)
        setup_agents(
            self.tool_registry,
            self.agent_registry,
            self.token_budget,
            self.compressor,
            self.obs_collector,
        )

        # Build LangGraph supervisor graph
        self.graph = None
        if self.settings.use_langgraph:
            try:
                self.graph = setup_graph(
                    self.tool_registry,
                    self.token_budget,
                    self.compressor,
                    self.obs_collector,
                )
            except Exception:
                logger.warning(
                    "LangGraph setup failed, " "using legacy agents",
                    exc_info=True,
                )

        self.app = create_app(
            self.agent_registry,
            self.executor,
            self.settings,
            self.token_budget,
            self.obs_collector,
            graph=self.graph,
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

# SIGTERM handler: flush observability before Docker
# sends SIGKILL (default 10s grace period).
import signal


def _sigterm_handler(signum, frame):
    """Flush pending events on SIGTERM."""
    if _server.obs_collector is not None:
        logger.info("SIGTERM: flushing observability")
        _server.obs_collector.flush_sync()


signal.signal(signal.SIGTERM, _sigterm_handler)

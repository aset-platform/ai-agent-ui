"""App config from environment variables and an optional ``.env`` file.

:class:`Settings` is a Pydantic Settings model that reads every field
from the process environment.  An optional
``.env`` file in the backend working directory is also consulted (lower
priority than real environment variables).

:func:`get_settings` returns a module-level singleton backed by
:func:`functools.lru_cache` so the environment is parsed exactly once per
process lifetime.

Typical usage::

    from config import get_settings

    settings = get_settings()
    assert settings.log_level == "DEBUG"
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application settings sourced from environment variables.

    All fields have sensible defaults so the server starts without any
    environment configuration, though API-dependent features (Groq, SerpAPI)
    will fail at runtime if the corresponding keys are empty.

    Attributes:
        groq_api_key: API key for the Groq LLM provider.  Maps to the
            ``GROQ_API_KEY`` environment variable.
        anthropic_api_key: API key for Anthropic (Claude).  Maps to
            ``ANTHROPIC_API_KEY``.  Unused while the backend targets Groq.
        serpapi_api_key: API key for SerpAPI web search.  Maps to
            ``SERPAPI_API_KEY``.  Required by the ``search_web`` tool.
        log_level: Minimum log severity forwarded to all handlers.  Must
            be a valid :mod:`logging` level name.  Defaults to ``"DEBUG"``.
        log_to_file: When ``True``, :func:`~logging_config.setup_logging`
            creates a rotating log file under ``logs/``.  Defaults to
            ``True``.
        agent_timeout_seconds: Maximum wall-clock seconds the agentic loop
            may run before the request is abandoned.  Applied to both the
            synchronous ``POST /chat`` endpoint and the streaming
            ``POST /chat/stream`` endpoint.  Defaults to ``900``.
        jwt_secret_key: Secret used to sign and verify JWT tokens.
            Must be at least 32 random characters.
            Maps to ``JWT_SECRET_KEY``.
        access_token_expire_minutes: Lifetime of an access
            token in minutes.  Defaults to ``60``.
        refresh_token_expire_days: Lifetime of a refresh token in days.
            Defaults to ``7``.
        google_client_id: OAuth 2.0 client ID from Google Cloud Console.
            Maps to ``GOOGLE_CLIENT_ID``.  Leave empty to disable Google SSO.
        google_client_secret: OAuth 2.0 client secret from Google
            Cloud Console.  Maps to ``GOOGLE_CLIENT_SECRET``.
        facebook_app_id: App ID from Facebook Developers portal.
            Maps to ``FACEBOOK_APP_ID``.  Leave empty to disable Facebook SSO.
        facebook_app_secret: App secret from Facebook Developers portal.
            Maps to ``FACEBOOK_APP_SECRET``.
        oauth_redirect_uri: The redirect URI registered with each
            OAuth provider.  Must match exactly.  Defaults to
            ``"http://localhost:3000/auth/oauth/callback"``.
    """

    groq_api_key: str = ""
    anthropic_api_key: str = ""
    serpapi_api_key: str = ""
    log_level: str = "DEBUG"
    log_to_file: bool = True
    agent_timeout_seconds: int = 900

    # PostgreSQL connection string (async driver).
    # Override via DATABASE_URL env var.
    database_url: str = (
        "postgresql+asyncpg://app:devpass123"
        "@localhost:5432/aiagent"
    )

    # Environment profile: "dev" (default), "test" (free-only).
    # "test" skips gpt-oss-120b and Anthropic entirely.
    ai_agent_ui_env: str = "dev"

    # ── Ollama local LLM (Tier 0) ────────────────
    # When enabled and reachable, the local Ollama model
    # is tried FIRST (zero cost).  Falls back to Groq on
    # failure.  Set OLLAMA_ENABLED=false in prod / CI.
    ollama_enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss:20b"
    ollama_num_ctx: int = 8192
    ollama_timeout: int = 120
    ollama_health_cache_ttl: int = 30

    # ── Memory / embedding settings ──────────────
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    memory_enabled: bool = True
    memory_top_k: int = 5
    memory_token_budget: int = 200

    # Groq model tiers — tried in order; cascade on budget
    # exhaustion or API error.  Comma-separated in env var.
    groq_model_tiers: str = (
        "llama-3.3-70b-versatile,"
        "moonshotai/kimi-k2-instruct,"
        "qwen/qwen3-32b,"
        "openai/gpt-oss-120b,"
        "openai/gpt-oss-20b,"
        "meta-llama/llama-4-scout-17b-16e-instruct"
    )

    # Synthesis tiers — used for final response (no tool calls).
    # Reserves gpt-oss-120b for quality output.
    synthesis_model_tiers: str = (
        "openai/gpt-oss-120b,"
        "openai/gpt-oss-20b,"
        "moonshotai/kimi-k2-instruct"
    )

    # Test tiers — free models only, zero paid exposure.
    test_model_tiers: str = (
        "llama-3.3-70b-versatile,"
        "moonshotai/kimi-k2-instruct,"
        "meta-llama/llama-4-scout-17b-16e-instruct"
    )

    # ── Round-robin model pools ──────────────────
    # When enabled, models are grouped into pools
    # and rotated within each pool to spread load
    # across daily token budgets.
    round_robin_enabled: bool = True

    tool_pool_primary: str = (
        "llama-3.3-70b-versatile,"
        "moonshotai/kimi-k2-instruct,"
        "qwen/qwen3-32b"
    )
    tool_pool_secondary: str = (
        "openai/gpt-oss-120b,"
        "openai/gpt-oss-20b"
    )
    tool_pool_tertiary: str = (
        "meta-llama/"
        "llama-4-scout-17b-16e-instruct"
    )
    synthesis_pool_primary: str = (
        "openai/gpt-oss-120b,"
        "openai/gpt-oss-20b,"
        "moonshotai/kimi-k2-instruct"
    )
    synthesis_pool_secondary: str = (
        "meta-llama/"
        "llama-4-scout-17b-16e-instruct"
    )

    # Message compression settings.
    max_history_turns: int = 3
    max_tool_result_chars: int = 800

    # Auth / JWT settings — required for the authentication module.
    # JWT_SECRET_KEY must be at least 32 random characters.  Generate with:
    #   python -c "import secrets; print(secrets.token_hex(32))"
    jwt_secret_key: str = ""
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # SSO / OAuth2 settings.
    # Google: obtain from console.cloud.google.com
    #   — create an OAuth 2.0 Web Application client.
    #   Add redirect URI below as an authorised URI.
    # Facebook: obtain from https://developers.facebook.com — create a Consumer
    #   app with the Facebook Login product.
    google_client_id: str = ""
    google_client_secret: str = ""
    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:3000/auth/oauth/callback"

    # WebSocket settings.
    ws_auth_timeout_seconds: int = 10
    ws_ping_interval_seconds: int = 30

    # Rate limiting (slowapi format: "N/period").
    rate_limit_login: str = "30/15minutes"
    rate_limit_register: str = "10/hour"
    rate_limit_oauth: str = "30/minute"

    # Google JWKS verification cache TTL in seconds.
    google_jwks_cache_ttl: int = 3600

    # Redis URL for token deny-list and OAuth state.
    # Empty = in-memory fallback (single-instance dev).
    redis_url: str = ""

    # Razorpay payment gateway (test mode).
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # Stripe payment gateway (test mode).
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""

    # Data retention policies (days to keep; 0 = keep forever).
    # Applies to append-only tables that grow unboundedly.
    retention_llm_usage_days: int = 90
    retention_analysis_summary_days: int = 365
    retention_forecast_runs_days: int = 180
    retention_company_info_days: int = 365
    retention_enabled: bool = False
    retention_dry_run: bool = True

    # Job scheduler
    scheduler_enabled: bool = True
    scheduler_max_workers: int = 3
    scheduler_catchup_enabled: bool = True

    # Smart cache warming: pre-warm Redis for the top
    # N most active users at startup.
    cache_warm_top_users: int = 5

    # LangGraph supervisor graph (set False to revert
    # to legacy BaseAgent dispatch).
    use_langgraph: bool = True

    # ── Observability: LangSmith / LangFuse ──────
    # LangSmith auto-traces LangChain/LangGraph calls
    # when LANGCHAIN_TRACING_V2=true is set in env.
    langsmith_enabled: bool = True
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    trace_sample_rate: float = 1.0  # 1.0 = 100% (dev)
    hide_trace_io: bool = False  # True in prod only

    # ── Forecast: Phase 3 ──────────────────────────
    ensemble_enabled: bool = False  # XGBoost ensemble

    # Read from .env in the working directory; silently skip if absent.
    # Real environment variables always take precedence over .env values.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Return the application :class:`Settings` singleton.

    The instance is constructed once and cached for the lifetime of the
    process.  Use ``get_settings.cache_clear()`` in tests to force
    re-parsing of environment variables.

    Returns:
        The cached :class:`Settings` instance populated from the
        environment and any ``.env`` file present.

    Example:
        >>> from config import get_settings
        >>> s = get_settings()
        >>> isinstance(s.log_level, str)
        True
    """
    return Settings()


def _parse_csv(val: str) -> list[str]:
    """Split comma-separated string, strip blanks."""
    return [t.strip() for t in val.split(",") if t.strip()]


def get_pool_groups(
    profile: str,
    settings: Settings | None = None,
) -> list[list[str]] | None:
    """Return pool groups for a cascade profile.

    Returns ``None`` when round-robin is disabled
    (legacy sequential mode).

    Args:
        profile: ``"tool"`` or ``"synthesis"``.
        settings: Override settings (uses singleton
            if ``None``).

    Returns:
        Ordered list of model-name lists, or ``None``.
    """
    s = settings or get_settings()
    if not s.round_robin_enabled:
        return None
    if profile == "synthesis":
        return [
            _parse_csv(s.synthesis_pool_primary),
            _parse_csv(s.synthesis_pool_secondary),
        ]
    # Default: tool profile
    return [
        _parse_csv(s.tool_pool_primary),
        _parse_csv(s.tool_pool_secondary),
        _parse_csv(s.tool_pool_tertiary),
    ]

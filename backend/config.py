"""Application configuration loaded from environment variables and an optional ``.env`` file.

:class:`Settings` is a `Pydantic Settings <https://docs.pydantic.dev/latest/concepts/pydantic_settings/>`_
model that reads every field from the process environment.  An optional
``.env`` file in the backend working directory is also consulted (lower
priority than real environment variables).

:func:`get_settings` returns a module-level singleton backed by
:func:`functools.lru_cache` so the environment is parsed exactly once per
process lifetime.

Typical usage::

    from config import get_settings

    settings = get_settings()
    print(settings.log_level)   # "DEBUG"
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
        jwt_secret_key: Secret used to sign and verify JWT tokens.  Must be
            at least 32 random characters.  Maps to ``JWT_SECRET_KEY``.
            Generate with ``python -c "import secrets; print(secrets.token_hex(32))"``.
        access_token_expire_minutes: Lifetime of an access token in minutes.
            Defaults to ``60``.
        refresh_token_expire_days: Lifetime of a refresh token in days.
            Defaults to ``7``.
        google_client_id: OAuth 2.0 client ID from Google Cloud Console.
            Maps to ``GOOGLE_CLIENT_ID``.  Leave empty to disable Google SSO.
        google_client_secret: OAuth 2.0 client secret from Google Cloud Console.
            Maps to ``GOOGLE_CLIENT_SECRET``.
        facebook_app_id: App ID from Facebook Developers portal.
            Maps to ``FACEBOOK_APP_ID``.  Leave empty to disable Facebook SSO.
        facebook_app_secret: App secret from Facebook Developers portal.
            Maps to ``FACEBOOK_APP_SECRET``.
        oauth_redirect_uri: The redirect URI registered with each OAuth provider.
            Must match exactly.  Defaults to
            ``"http://localhost:3000/auth/oauth/callback"``.
    """

    groq_api_key: str = ""
    anthropic_api_key: str = ""
    serpapi_api_key: str = ""
    log_level: str = "DEBUG"
    log_to_file: bool = True
    agent_timeout_seconds: int = 900

    # Auth / JWT settings — required for the authentication module.
    # JWT_SECRET_KEY must be at least 32 random characters.  Generate with:
    #   python -c "import secrets; print(secrets.token_hex(32))"
    jwt_secret_key: str = ""
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # SSO / OAuth2 settings.
    # Google: obtain from https://console.cloud.google.com — create an OAuth 2.0
    #   Web Application client.  Add the redirect URI below as an authorised URI.
    # Facebook: obtain from https://developers.facebook.com — create a Consumer
    #   app with the Facebook Login product.
    google_client_id: str = ""
    google_client_secret: str = ""
    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:3000/auth/oauth/callback"

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

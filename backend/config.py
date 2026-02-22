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
    """

    groq_api_key: str = ""
    anthropic_api_key: str = ""
    serpapi_api_key: str = ""
    log_level: str = "DEBUG"
    log_to_file: bool = True

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

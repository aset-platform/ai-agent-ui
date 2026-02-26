# Configuration

Application configuration is managed in `backend/config.py` using [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). All settings are read from environment variables at startup. An optional `.env` file in the `backend/` directory is also supported.

---

## Settings Model

```python
class Settings(BaseSettings):
    groq_api_key: str = ""
    anthropic_api_key: str = ""
    serpapi_api_key: str = ""
    log_level: str = "DEBUG"
    log_to_file: bool = True
    agent_timeout_seconds: int = 900
    # OAuth / SSO settings
    google_client_id: str = ""
    google_client_secret: str = ""
    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:3000/auth/oauth/callback"
```

| Field | Env Var | Default | Description |
|-------|---------|---------|-------------|
| `groq_api_key` | `GROQ_API_KEY` | `""` | API key for the Groq LLM provider |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | `""` | API key for Claude (unused while on Groq) |
| `serpapi_api_key` | `SERPAPI_API_KEY` | `""` | API key for SerpAPI web search |
| `log_level` | `LOG_LEVEL` | `"DEBUG"` | Minimum log severity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `log_to_file` | `LOG_TO_FILE` | `True` | Write logs to a rotating file under `backend/logs/` |
| `agent_timeout_seconds` | `AGENT_TIMEOUT_SECONDS` | `900` | Maximum seconds the agentic loop may run; HTTP 504 / stream timeout event on expiry |
| `google_client_id` | `GOOGLE_CLIENT_ID` | `""` | Google OAuth client identifier |
| `google_client_secret` | `GOOGLE_CLIENT_SECRET` | `""` | Google OAuth client secret |
| `facebook_app_id` | `FACEBOOK_APP_ID` | `""` | Facebook App ID for OAuth |
| `facebook_app_secret` | `FACEBOOK_APP_SECRET` | `""` | Facebook App secret for OAuth |
| `oauth_redirect_uri` | `OAUTH_REDIRECT_URI` | `"http://localhost:3000/auth/oauth/callback"` | Redirect URI registered with each OAuth provider |

All fields have defaults, so the server starts without any environment configuration. API‑dependent features (LLM inference, web search, SSO) will fail at runtime if the corresponding keys are missing.

---

## Priority Order

When the same variable is defined in multiple places, later sources override earlier ones:

```
.env file  <  environment variable
```

Real environment variables (exported in the shell) always take precedence over `.env` file values.

---

## Using a .env File

Create `backend/.env` (never commit this file):

```dotenv
GROQ_API_KEY=gsk_...
SERPAPI_API_KEY=abc123...
LOG_LEVEL=INFO
LOG_TO_FILE=true
AGENT_TIMEOUT_SECONDS=900
# OAuth / SSO
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
FACEBOOK_APP_ID=your-fb-app-id
FACEBOOK_APP_SECRET=your-fb-app-secret
OAUTH_REDIRECT_URI=http://localhost:3000/auth/oauth/callback
```

The `.env` file is read automatically by Pydantic Settings when the server starts. It uses `extra="ignore"` so unknown keys in the file are silently skipped rather than raising a validation error.

---

## get_settings()

```python
from functools import lru_cache

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`get_settings()` is cached with `@lru_cache`. The environment is parsed exactly once per process lifetime. This means:

- Changing an environment variable after startup has no effect.
- In tests, call `get_settings.cache_clear()` before each test that needs fresh settings.

---

## Usage in main.py

```python
from config import get_settings

settings = get_settings()
setup_logging(level=settings.log_level, log_to_file=settings.log_to_file)
server = ChatServer(settings)
```

`ChatServer` receives the `Settings` object and stores it as `self.settings`. Currently the server itself does not read settings fields directly (logging and LLM credentials are handled by the logging module and the LLM provider library respectively), but having it on `self` makes it available for future use.
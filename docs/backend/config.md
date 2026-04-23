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
    # N-tier Groq model cascade (comma-separated)
    groq_model_tiers: str = (
        "llama-3.3-70b-versatile,"
        "qwen/qwen3-32b,"
        "openai/gpt-oss-120b,"
        "meta-llama/llama-4-scout-17b-16e-instruct"
    )
    # Message compression settings
    max_history_turns: int = 3
    max_tool_result_chars: int = 2000
    # OAuth / SSO settings
    google_client_id: str = ""
    google_client_secret: str = ""
    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:3000/auth/oauth/callback"
    # WebSocket settings
    ws_auth_timeout_seconds: int = 10
    ws_ping_interval_seconds: int = 30
    # Redis (optional — enables RedisTokenStore)
    redis_url: str = ""
```

| Field | Env Var | Default | Description |
|-------|---------|---------|-------------|
| `groq_api_key` | `GROQ_API_KEY` | `""` | API key for the Groq LLM provider (optional — enables Groq tiers) |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | `""` | API key for Anthropic Claude (required — final fallback) |
| `serpapi_api_key` | `SERPAPI_API_KEY` | `""` | API key for SerpAPI web search |
| `groq_model_tiers` | `GROQ_MODEL_TIERS` | *(4 models, see above)* | Comma-separated ordered list of Groq model names tried first→last before Anthropic fallback |
| `max_history_turns` | `MAX_HISTORY_TURNS` | `3` | Max conversation turns kept after compression |
| `max_tool_result_chars` | `MAX_TOOL_RESULT_CHARS` | `2000` | Max characters per tool result after compression |
| `log_level` | `LOG_LEVEL` | `"DEBUG"` | Minimum log severity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `log_to_file` | `LOG_TO_FILE` | `True` | Write logs to a rotating file under `~/.ai-agent-ui/logs/` |
| `agent_timeout_seconds` | `AGENT_TIMEOUT_SECONDS` | `900` | Maximum seconds the agentic loop may run; HTTP 504 / stream timeout event on expiry |
| `google_client_id` | `GOOGLE_CLIENT_ID` | `""` | Google OAuth client identifier |
| `google_client_secret` | `GOOGLE_CLIENT_SECRET` | `""` | Google OAuth client secret |
| `facebook_app_id` | `FACEBOOK_APP_ID` | `""` | Facebook App ID for OAuth |
| `facebook_app_secret` | `FACEBOOK_APP_SECRET` | `""` | Facebook App secret for OAuth |
| `oauth_redirect_uri` | `OAUTH_REDIRECT_URI` | `"http://localhost:3000/auth/oauth/callback"` | Redirect URI registered with each OAuth provider |
| `ws_auth_timeout_seconds` | `WS_AUTH_TIMEOUT_SECONDS` | `10` | Seconds to wait for WebSocket auth message before closing |
| `ws_ping_interval_seconds` | `WS_PING_INTERVAL_SECONDS` | `30` | Seconds between WebSocket keepalive pings |
| `redis_url` | `REDIS_URL` | `""` | Redis connection URL (empty = use InMemoryTokenStore) |

### Memory / Embedding Settings

| Field | Env Var | Default | Description |
|-------|---------|---------|-------------|
| `embedding_model` | `EMBEDDING_MODEL` | `nomic-embed-text` | Ollama embedding model name |
| `embedding_dim` | `EMBEDDING_DIM` | `768` | Vector dimension (must match model) |
| `memory_enabled` | `MEMORY_ENABLED` | `true` | Enable/disable pgvector memory pipeline |
| `memory_top_k` | `MEMORY_TOP_K` | `5` | Memories retrieved per query |
| `memory_token_budget` | `MEMORY_TOKEN_BUDGET` | `200` | Max tokens for `[Memory context]` injection |

### Round-Robin Pool Settings

| Field | Env Var | Default |
|-------|---------|---------|
| `round_robin_enabled` | `ROUND_ROBIN_ENABLED` | `true` |
| `tool_pool_primary` | `TOOL_POOL_PRIMARY` | `llama-3.3-70b-versatile,qwen/qwen3-32b` |
| `tool_pool_secondary` | `TOOL_POOL_SECONDARY` | `openai/gpt-oss-120b,openai/gpt-oss-20b` |
| `tool_pool_tertiary` | `TOOL_POOL_TERTIARY` | `meta-llama/llama-4-scout-17b-16e-instruct` |
| `synthesis_pool_primary` | `SYNTHESIS_POOL_PRIMARY` | `openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3-32b` |
| `synthesis_pool_secondary` | `SYNTHESIS_POOL_SECONDARY` | `meta-llama/llama-4-scout-17b-16e-instruct` |
| `synthesis_model_tiers` | `SYNTHESIS_MODEL_TIERS` | `openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3-32b` |

Set `ROUND_ROBIN_ENABLED=false` to revert to legacy sequential cascade.

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
ANTHROPIC_API_KEY=sk-ant-...
GROQ_API_KEY=gsk_...
SERPAPI_API_KEY=abc123...
LOG_LEVEL=INFO
LOG_TO_FILE=true
AGENT_TIMEOUT_SECONDS=900
# Groq model tiers (comma-separated, tried in order)
GROQ_MODEL_TIERS=llama-3.3-70b-versatile,qwen/qwen3-32b,openai/gpt-oss-120b,meta-llama/llama-4-scout-17b-16e-instruct
# Message compression
MAX_HISTORY_TURNS=3
MAX_TOOL_RESULT_CHARS=2000
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
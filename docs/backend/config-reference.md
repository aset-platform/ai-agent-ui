# Configuration Reference (Auto-Generated)

!!! info
    This page is auto-generated from the `Settings` class in `backend/config.py` on every `mkdocs build`. Do not edit manually.

All settings are read from environment variables (or `backend/.env`). The env var name is the field name in **UPPER_CASE**.

## API Keys

| Variable | Type | Default |
|----------|------|---------|
| `GROQ_API_KEY` | str | *(empty — required)* |
| `ANTHROPIC_API_KEY` | str | *(empty — required)* |
| `SERPAPI_API_KEY` | str | *(empty — required)* |
| `GROQ_MODEL_TIERS` | str | `llama-3.3-70b-versatile,qwen/qwen3-32b,…` |

## LLM Cascade

| Variable | Type | Default |
|----------|------|---------|
| `AI_AGENT_UI_ENV` | str | `dev` |
| `SYNTHESIS_MODEL_TIERS` | str | `openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3-32b` |
| `TEST_MODEL_TIERS` | str | `llama-3.3-70b-versatile,qwen/qwen3-32b,…` |

## JWT & Auth

| Variable | Type | Default |
|----------|------|---------|
| `JWT_SECRET_KEY` | str | *(empty — required)* |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | int | `60` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | int | `7` |

## OAuth / SSO

| Variable | Type | Default |
|----------|------|---------|
| `GOOGLE_CLIENT_ID` | str | `` |
| `GOOGLE_CLIENT_SECRET` | str | *(empty — required)* |
| `FACEBOOK_APP_ID` | str | `` |
| `FACEBOOK_APP_SECRET` | str | *(empty — required)* |
| `OAUTH_REDIRECT_URI` | str | `http://localhost:3000/auth/oauth/callback` |
| `RATE_LIMIT_OAUTH` | str | `30/minute` |
| `GOOGLE_JWKS_CACHE_TTL` | int | `3600` |

## WebSocket

| Variable | Type | Default |
|----------|------|---------|
| `WS_AUTH_TIMEOUT_SECONDS` | int | `10` |
| `WS_PING_INTERVAL_SECONDS` | int | `30` |

## Rate Limiting

| Variable | Type | Default |
|----------|------|---------|
| `RATE_LIMIT_LOGIN` | str | `30/15minutes` |
| `RATE_LIMIT_REGISTER` | str | `10/hour` |

## Redis

| Variable | Type | Default |
|----------|------|---------|
| `REDIS_URL` | str | `` |

## Logging

| Variable | Type | Default |
|----------|------|---------|
| `LOG_LEVEL` | str | `DEBUG` |
| `LOG_TO_FILE` | bool | `True` |

## Agent Execution

| Variable | Type | Default |
|----------|------|---------|
| `AGENT_TIMEOUT_SECONDS` | int | `900` |

## Message Compression

| Variable | Type | Default |
|----------|------|---------|
| `MAX_HISTORY_TURNS` | int | `3` |
| `MAX_TOOL_RESULT_CHARS` | int | `2000` |

## Data Retention

| Variable | Type | Default |
|----------|------|---------|
| `RETENTION_LLM_USAGE_DAYS` | int | `90` |
| `RETENTION_ANALYSIS_SUMMARY_DAYS` | int | `365` |
| `RETENTION_FORECAST_RUNS_DAYS` | int | `180` |
| `RETENTION_COMPANY_INFO_DAYS` | int | `365` |
| `RETENTION_ENABLED` | bool | `False` |
| `RETENTION_DRY_RUN` | bool | `True` |

## Other

| Variable | Type | Default |
|----------|------|---------|
| `DATABASE_URL` | str | `postgresql+asyncpg://app:devpass123@loca…` |
| `OLLAMA_ENABLED` | bool | `True` |
| `OLLAMA_BASE_URL` | str | `http://localhost:11434` |
| `OLLAMA_MODEL` | str | `gpt-oss:20b` |
| `OLLAMA_NUM_CTX` | int | `8192` |
| `OLLAMA_TIMEOUT` | int | `120` |
| `OLLAMA_HEALTH_CACHE_TTL` | int | `30` |
| `RAZORPAY_KEY_ID` | str | *(empty — required)* |
| `RAZORPAY_KEY_SECRET` | str | *(empty — required)* |
| `RAZORPAY_WEBHOOK_SECRET` | str | *(empty — required)* |
| `STRIPE_SECRET_KEY` | str | *(empty — required)* |
| `STRIPE_PUBLISHABLE_KEY` | str | *(empty — required)* |
| `STRIPE_WEBHOOK_SECRET` | str | *(empty — required)* |
| `SCHEDULER_ENABLED` | bool | `True` |
| `SCHEDULER_MAX_WORKERS` | int | `3` |
| `SCHEDULER_CATCHUP_ENABLED` | bool | `True` |
| `CACHE_WARM_TOP_USERS` | int | `5` |
| `USE_LANGGRAPH` | bool | `True` |
| `LANGSMITH_ENABLED` | bool | `True` |
| `LANGFUSE_ENABLED` | bool | `False` |
| `LANGFUSE_PUBLIC_KEY` | str | *(empty — required)* |
| `LANGFUSE_SECRET_KEY` | str | *(empty — required)* |
| `LANGFUSE_HOST` | str | `https://cloud.langfuse.com` |
| `TRACE_SAMPLE_RATE` | float | `1.0` |
| `HIDE_TRACE_IO` | bool | `False` |
| `ENSEMBLE_ENABLED` | bool | `False` |

---

*59 configuration fields total.*

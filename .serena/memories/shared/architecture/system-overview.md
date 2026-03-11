# System Overview

## Services

| Service | Port | Entry point | Stack |
|---------|------|-------------|-------|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19, TypeScript |
| Dashboard | 8050 | `dashboard/app.py` | Plotly Dash (FLATLY theme) |
| Docs | 8000 | `mkdocs serve` | MkDocs Material |

## Core Patterns

- **`ChatServer`** (`backend/main.py`) — owns `ToolRegistry`,
  `AgentRegistry`, FastAPI app, bounded `ThreadPoolExecutor(10)`.
  All state in this class, no module-level mutable globals.
  `GET /health` endpoint returns `{"status": "ok"}`.
- **API versioning**: Routes dual-mounted at `/` (legacy) and `/v1/`
  via `_register_core_routes(router)` in `backend/routes.py`.
- **Token store**: `auth/token_store.py` — `TokenStore` protocol
  with `InMemoryTokenStore` / `RedisTokenStore`. Factory:
  `create_token_store(redis_url)`. Used for JWT deny-list + OAuth state.
- **`BaseAgent`** (`backend/agents/base.py`) — ABC with agentic loop
  (`MAX_ITERATIONS=15`) + streaming. Subclasses only override
  `_build_llm()`.
- **LLM**: N-tier Groq cascade + Anthropic Claude Sonnet 4.6 fallback
  via `FallbackLLM` in `backend/llm_fallback.py`. Config: `AgentConfig.
  groq_model_tiers` list, parsed from `GROQ_MODEL_TIERS` CSV env var.
  Default: llama-3.3-70b → kimi-k2 → gpt-oss-120b → scout-17b →
  claude-sonnet-4-6.
- **Budget tracking**: `backend/token_budget.py` — sliding-window
  TPM/RPM per Groq model. `backend/message_compressor.py` — 3-stage
  compression (system prompt, history, tool results).
- **Streaming**: `POST /chat/stream` returns NDJSON events:
  `thinking`, `tool_start`, `tool_done`, `warning`, `final`, `error`.
- **Same-day cache**:
  `~/.ai-agent-ui/data/cache/{TICKER}_{key}_{YYYY-MM-DD}.txt`.
- **Centralised paths**: `backend/paths.py` — single source of truth
  for all filesystem locations. Override root with `AI_AGENT_UI_HOME`.
- **Tool registration order**: `search_market_news` registered after
  GeneralAgent, before StockAgent.
- **Ticker auto-linking**: `tools/_ticker_linker.py` uses
  `threading.local()` to pass `user_id` from HTTP handler into
  `@tool` functions. Frontend sends `user_id` via `getUserIdFromToken()`.
- **Freshness gates**: Analysis skips if done today (Iceberg check);
  forecast skips if run within 7 days. Both non-blocking.

## Filesystem Layout

All runtime data under `~/.ai-agent-ui/` (override: `AI_AGENT_UI_HOME`).
Paths centralised in `backend/paths.py`.

```
~/.ai-agent-ui/
├── data/iceberg/{catalog.db,warehouse/}   # Iceberg tables
├── data/{cache,raw,forecasts,avatars}/     # runtime data
├── charts/{analysis,forecasts}/            # HTML charts
├── logs/                                   # rotating agent.log
├── backend.env                             # secrets (symlinked)
└── frontend.env.local                      # service URLs (symlinked)
```

## Key Directories

- `backend/` — agents, tools, config, llm_fallback, token_budget
- `auth/` — JWT + RBAC + OAuth PKCE + user-ticker linking
- `stocks/` — Iceberg persistence (9 tables, single source of truth)
- `frontend/` — SPA (Next.js)
- `dashboard/` — Dash + services, incl. Marketplace page
- `hooks/` — pre-commit, pre-push

# AI Agent UI

A fullstack agentic chat application with stock analysis, Prophet forecasting, and portfolio management. LangGraph supervisor with 6 sub-agents, memory-augmented multi-turn conversations with PG-persisted context. Hybrid PostgreSQL + Apache Iceberg data layer with DuckDB read acceleration.

---

## Feature Highlights

- **6 specialized AI agents** — Portfolio, Stock Analyst, Forecaster, Research, Sentiment, and Recommendation agents, each with purpose-built tool sets, routed by a LangGraph supervisor with 2-tier intent classification
- **Context-aware multi-turn conversations** — PG-persisted conversation context with cross-session resume, rolling summary window, intent-aware follow-up detection
- **Smart Funnel recommendations** — 3-stage pipeline (DuckDB pre-filter → gap analysis → LLM reasoning), market-scoped (India/US), unified quota system
- **Prophet forecasting with ensemble correction** — 3/6/9-month price targets with 80% confidence bands, XGBoost ensemble, accuracy-adjusted scoring
- **Historical portfolio tools** — daily value series, period comparison, time-travel queries with flexible date range support
- **752-stock pipeline** — automated daily refresh, analytics, sentiment, Piotroski F-Score across India and US markets
- **Memory-augmented chat** — pgvector semantic memory retrieval (768-dim); facts + summaries persist and auto-inject into sub-agent prompts
- **Round-robin model pools** — load-balanced Groq daily token budgets across 6 models (~2.3M TPD combined)
- **Real-time WebSocket streaming** — live `tool_start` / `tool_done` events give users visibility into the agentic loop as it runs
- **Dual payment gateways** — Razorpay (INR modal) and Stripe (USD hosted checkout) with pro-rata billing
- **Docker Compose 5-service orchestration** — `docker compose up -d` starts backend (8181), frontend (3000), PostgreSQL (5432), Redis (6379), and docs (8000)
- **LLM Observability dashboard** — real-time token tracking, per-model TPD/RPD bars, cascade event log
- **Piotroski F-Score screening** — fundamental scoring (747 stocks), market filter (India/US), index tags (Nifty 50/100/500)
- **Data Health dashboard** — 5 health cards with fix buttons, NaN cleanup, backfill from yfinance
- **Live market ticker** — Nifty 50 + Sensex in header, dual-source (NSE India + Yahoo Finance), 30s refresh, PG-persisted for restart resilience

---

## Stack

| Layer | Technology |
|-------|------------|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4 |
| Backend | Python 3.12, FastAPI, LangChain |
| LLM | Round-robin Groq cascade (6 models) + Anthropic Claude Sonnet 4.6 fallback |
| Vector DB | pgvector (PostgreSQL extension, 768-dim embeddings) |
| Embeddings | Ollama nomic-embed-text (local, zero API cost) |
| Web search tool | SerpAPI via `langchain-community` |
| Package management | npm (frontend), pip + virtualenv (backend) |

---

## How It Works

```
User types a message
       │
       ▼
Frontend (Next.js) sends via WebSocket
       │
       ▼
Guardrail → content safety, financial relevance, ticker extraction
       │
       ▼
Router → 2-tier intent classification (keyword → LLM fallback)
       │
       ▼
Supervisor → routes to 1 of 6 sub-agents
       │
       ▼
Sub-agent tool loop:
  1. Invoke LLM with bound tools (Groq cascade)
  2. Execute tool calls → feed results back → repeat
  3. After max_tool_rounds → synthesis pass (gpt-oss-120b)
       │
       ▼
Response streamed via NDJSON → rendered as chat bubble
Context persisted to PG for cross-session resume
```

---

## Quick Start

```bash
# 1. Start all services
./run.sh start

# Or manually:
cd backend
source ~/.ai-agent-ui/venv/bin/activate
export GROQ_API_KEY=...          # optional — enables Groq tiers
export ANTHROPIC_API_KEY=...     # required — Claude fallback
export SERPAPI_API_KEY=...       # optional — web search
uvicorn main:app --port 8181 --reload

# 2. Start the frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## Project Layout

```
ai-agent-ui/
├── backend/                    # FastAPI (:8181)
│   ├── main.py                 # Entry point
│   ├── agents/                 # LangGraph framework
│   │   ├── configs/            # 7 sub-agent configs
│   │   ├── nodes/              # 10 graph nodes
│   │   ├── graph.py            # State graph
│   │   ├── sub_agents.py       # Tool-calling loop
│   │   └── conversation_context.py  # PG-persisted context
│   ├── tools/                  # 32 LLM-callable tools
│   ├── jobs/                   # Scheduler executors
│   ├── pipeline/               # CLI pipeline (19 commands)
│   ├── db/models/              # 18 ORM models
│   └── llm_fallback.py         # N-tier cascade
├── auth/                       # JWT + RBAC + OAuth
├── stocks/repository.py        # Iceberg CRUD (DuckDB-first)
├── frontend/                   # Next.js 16 (:3000)
│   ├── app/                    # 12 pages
│   ├── components/             # 30+ components
│   └── hooks/                  # 19 SWR data hooks
├── tests/                      # 88 pytest files
├── e2e/                        # 65 Playwright specs
├── scripts/                    # 37 utilities
└── docker-compose.yml          # 5 services
```

---

## Navigation

- [Backend Overview](backend/overview.md) — architecture and startup sequence
- [API Reference](backend/api.md) — HTTP endpoints, request/response shapes
- [Agents](backend/agents.md) — BaseAgent, AgentRegistry, GeneralAgent
- [Tools](backend/tools.md) — ToolRegistry, get_current_time, search_web
- [Configuration](backend/config.md) — environment variables, Settings model
- [Logging](backend/logging.md) — structured logging, rotating file handler
- [Frontend Overview](frontend/overview.md) — UI, state, API calls
- [OAuth & SSO](backend/oauth.md) — PKCE flow, Google + Facebook
- [How to Run](dev/how-to-run.md) — full setup instructions
- [Decisions](dev/decisions.md) — why things are built the way they are
- [Changelog](dev/changelog.md) — session-by-session history

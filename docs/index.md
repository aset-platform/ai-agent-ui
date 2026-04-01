# AI Agent UI

A fullstack agentic chat application built with Next.js and FastAPI. The LLM runs inside an agentic loop — it can call tools, receive their results, and keep iterating until it has a final answer before responding to the user.

---

## Feature Highlights

- **5 specialized AI agents** — Portfolio, Stock Analyst, Forecaster, Research, and Sentiment agents, each with purpose-built tool sets, routed by a LangGraph supervisor
- **Context-aware multi-turn conversations** — rolling summary window maintains coherent long conversations without overflowing the context budget
- **Recency-aware news** — 7-day default window with time-decay scoring surfaces the most relevant recent headlines first
- **Dual payment gateways** — Razorpay (INR modal) and Stripe (USD hosted checkout) with pro-rata billing and webhook-verified transaction ledger
- **Prophet forecasting with ensemble correction** — 3/6/9-month price targets with 80% confidence bands, cached same-day
- **Real-time WebSocket streaming** — live `tool_start` / `tool_done` events give users visibility into the agentic loop as it runs
- **Ollama local LLM support** — zero-cost inference as Tier 0 in the cascade; gracefully skipped when unavailable
- **Docker Compose 5-service orchestration** — `docker compose up -d` starts backend (8181), frontend (3000), PostgreSQL (5432), Redis (6379), and docs (8000)
- **Memory-augmented chat** — pgvector semantic memory retrieval across sessions; facts + summaries persist and auto-inject into sub-agent prompts
- **Round-robin model pools** — load-balanced Groq daily token budgets across 6 models (~2.3M TPD combined)
- **LLM Observability dashboard** — real-time token tracking, daily budget monitoring, per-model TPD/RPD bars
- **Lighthouse performance monitoring** — 94/100 score; LHCI gate enforced pre-PR via `npm run perf:check`

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
Frontend (Next.js) POSTs { message, history } to the backend
       │
       ▼
ChatServer routes the request to the "general" agent
       │
       ▼
BaseAgent.run() starts the agentic loop:
  1. Invoke LLM with tools bound
  2. If LLM calls a tool → execute it, feed result back, repeat
  3. If no tool calls → return final response
       │
       ▼
Response text returned to the frontend and rendered as a chat bubble
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
├── backend/
│   ├── main.py              # ChatServer class + uvicorn entry point
│   ├── config.py            # Pydantic Settings (env vars / .env)
│   ├── logging_config.py    # Centralised logging setup
│   ├── llm_fallback.py      # FallbackLLM — N-tier Groq + Anthropic cascade
│   ├── token_budget.py      # Sliding-window TPM/RPM budget tracker
│   ├── message_compressor.py # 3-stage message compression
│   ├── agents/              # Agent framework
│   │   ├── base.py          # BaseAgent ABC (agentic loop)
│   │   ├── config.py        # AgentConfig dataclass
│   │   ├── loop.py          # Agentic loop logic
│   │   ├── stream.py        # NDJSON streaming
│   │   ├── registry.py      # AgentRegistry
│   │   ├── general_agent.py # GeneralAgent + factory
│   │   └── stock_agent.py   # StockAgent + factory
│   ├── tools/               # Tool framework
│   │   ├── registry.py      # ToolRegistry
│   │   ├── time_tool.py     # get_current_time
│   │   ├── search_tool.py   # search_web
│   │   └── stock_data_tool.py # 7 Yahoo Finance tools
│   └── requirements.txt
├── frontend/
│   └── app/
│       ├── page.tsx         # Main chat UI
│       ├── layout.tsx       # Root layout
│       └── globals.css      # Tailwind globals
└── mkdocs.yml
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

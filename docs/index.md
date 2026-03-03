# AI Agent UI

A fullstack agentic chat application built with Next.js and FastAPI. The LLM runs inside an agentic loop — it can call tools, receive their results, and keep iterating until it has a final answer before responding to the user.

---

## Stack

| Layer | Technology |
|-------|------------|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4 |
| Backend | Python 3.12, FastAPI, LangChain |
| LLM | Groq `openai/gpt-oss-120b` *(temporary — Claude Sonnet 4.6 intended)* |
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
# 1. Start the backend
cd backend
source demoenv/bin/activate
export GROQ_API_KEY=...
export SERPAPI_API_KEY=...
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
│   ├── agents/              # Agent framework
│   │   ├── base.py          # AgentConfig + BaseAgent ABC (agentic loop)
│   │   ├── registry.py      # AgentRegistry
│   │   └── general_agent.py # GeneralAgent + factory
│   ├── tools/               # Tool framework
│   │   ├── registry.py      # ToolRegistry
│   │   ├── time_tool.py     # get_current_time
│   │   └── search_tool.py   # search_web
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
- [How to Run](dev/how-to-run.md) — full setup instructions
- [Decisions](dev/decisions.md) — why things are built the way they are
- [Changelog](dev/changelog.md) — session-by-session history

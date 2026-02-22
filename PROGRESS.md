# PROGRESS.md — Session Log

---

# Session: Feb 22, 2026

## What We Did Today

### 1. OOP Backend Refactor — agents/ and tools/ packages

Deleted `backend/agent.py` and split its responsibilities across a proper package structure:

**`backend/agents/`**
- `base.py` — `AgentConfig` dataclass + `BaseAgent` ABC; the full agentic loop lives here (`run()` method); subclasses only implement `_build_llm()`
- `registry.py` — `AgentRegistry` maps `agent_id` strings to `BaseAgent` instances; used by the HTTP layer for routing
- `general_agent.py` — `GeneralAgent(BaseAgent)` backed by Groq; `create_general_agent(tool_registry)` factory function

**`backend/tools/`**
- `registry.py` — `ToolRegistry` maps tool name strings to LangChain `BaseTool` instances; provides `register`, `get_tools`, `invoke`, `list_names`
- `time_tool.py` — `get_current_time` `@tool`
- `search_tool.py` — `search_web` `@tool` (now wrapped in try/except — see item 3)

### 2. Rewrote backend/main.py with ChatServer class

- All server state (registries, app) encapsulated in `ChatServer`; no more module-level globals
- `POST /chat` now accepts optional `agent_id` (default `"general"`) and echoes it in the response
- Added `GET /agents` endpoint — returns list of registered agents with id, name, description
- Replaced bare `except` returning error strings in `200` body with proper `HTTPException` `404`/`500`

### 3. Fixed search_web error handling (was a listed TODO)

- Wrapped `SerpAPIWrapper().run(query)` in try/except
- On failure returns `"Search failed: <reason>"` as a `ToolMessage` so the LLM can recover gracefully instead of raising an unhandled exception

### 4. Added backend/config.py

- `Settings(BaseSettings)` with fields: `groq_api_key`, `anthropic_api_key`, `serpapi_api_key`, `log_level`, `log_to_file`
- Reads from env vars; also reads `backend/.env` if present
- `get_settings()` cached with `@lru_cache`

### 5. Added backend/logging_config.py

- `setup_logging(level, log_to_file, log_dir)` configures the root logger
- Always adds a stdout console handler
- Optionally adds a `TimedRotatingFileHandler` → `backend/logs/agent.log` (daily rotation, 7-day retention)
- `logs/` added to `.gitignore`

### 6. Added Google-style Sphinx docstrings

- All backend Python files have module-level, class-level, and method-level docstrings

### 7. Python 3.9 annotation compatibility fix

- `X | Y` union syntax (PEP 604, Python 3.10+) replaced with `Optional[X]` from `typing` throughout — `demoenv` runs Python 3.9.13

### 8. Updated CLAUDE.md

- Replaced old flat backend tree with new package layout
- Rewrote Backend Details section (removed `agent.py`, rewrote `main.py`, added subsections for all new modules)
- Updated "Switching back to Claude" to reference `agents/general_agent.py` → `_build_llm()`
- Added 7 new Decisions Made entries
- Removed fixed TODO (search_web error handling)

### 9. Committed and pushed (commit fa20966)

```
refactor: OOP backend restructure with agents/, tools/ packages and structured logging
```
14 files changed, 1,191 insertions, 127 deletions

### 10. MkDocs documentation site

- Installed `mkdocs==1.6.1` and `mkdocs-material==9.7.2` into `demoenv`
- Created `mkdocs.yml` — material theme (indigo, light/dark toggle), nav tabs, code copy, pymdownx extensions
- Populated all 11 docs pages from scratch based on a full codebase read-and-analyse pass:

| Page | Content |
|------|---------|
| `docs/index.md` | Stack, end-to-end flow, quick start, layout |
| `docs/backend/overview.md` | Module map, startup sequence, agentic loop, extension guide |
| `docs/backend/api.md` | Both endpoints, JSON shapes, error codes, curl examples |
| `docs/backend/agents.md` | AgentConfig, BaseAgent loop, AgentRegistry, GeneralAgent, Claude switch |
| `docs/backend/tools.md` | ToolRegistry, both tools, invoke() comparison, how to add tools |
| `docs/backend/config.md` | Settings fields, priority order, .env usage, lru_cache |
| `docs/backend/logging.md` | Format, handlers, hot-reload safety, what-gets-logged table |
| `docs/frontend/overview.md` | Component, state, send flow, UI layout, limitations |
| `docs/dev/how-to-run.md` | Prerequisites, backend + frontend setup, verification, Claude switch |
| `docs/dev/decisions.md` | Full rationale for every architectural and tooling decision |
| `docs/dev/changelog.md` | Session log with commit hashes, pending-issues table |

- Committed and pushed (commit f7f1cbc)
```
docs: add MkDocs site with full project documentation
```
13 files changed, 1,951 insertions

### 11. Pre-push quality gate (hooks/pre-push)

Added a git pre-push hook that enforces 5 mandatory steps before every push to `main`:

| Check | Mechanism | On failure |
|-------|-----------|------------|
| No bare `print()` in backend Python | AST walk via `demoenv/bin/python` | Hard block (exit 1) |
| Module-level docstrings on non-`__init__.py` files | AST walk | Warning only (exit 0) |
| `mkdocs build` passes | Runs in `demoenv` | Hard block (exit 1) |
| OOP architecture / standard practices | CLAUDE.md checklist (manual) | — |
| CLAUDE.md + PROGRESS.md + docs updated | CLAUDE.md checklist (manual) | — |

- Hook saved to `hooks/pre-push` (tracked by git — source of truth)
- Install: `cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`
- Uses AST parsing (not `grep`) so `print()` inside docstring examples is not flagged
- Only activates on pushes targeting `refs/heads/main`; other branches pass through
- Added "Pre-Push Checklist" section to CLAUDE.md with the full 5-step gate

---

## Current State of the Codebase

| Component | Status |
|-----------|--------|
| FastAPI backend (`main.py`) | ✅ `ChatServer` class — `/chat` + `/agents` endpoints |
| Agentic loop (`agents/base.py`) | ✅ `BaseAgent.run()` — proper multi-turn tool loop |
| Agent routing | ✅ `AgentRegistry` — dispatch by `agent_id` |
| Tool registry | ✅ `ToolRegistry` — decoupled from agent code |
| LLM | ⚠️ Groq `openai/gpt-oss-120b` (temporary — Claude Sonnet 4.6 intended) |
| `get_current_time` tool | ✅ Working |
| `search_web` tool | ✅ SerpAPI with try/except error handling |
| Structured logging | ✅ Console + rotating file (`logs/agent.log`) |
| Config / env vars | ✅ Pydantic Settings with `.env` support |
| Frontend chat UI | ✅ Unchanged — working |
| Multi-turn history | ✅ Working |
| Documentation | ✅ MkDocs site — 11 pages, material theme (`mkdocs serve`) |
| Pre-push hook | ✅ `hooks/pre-push` — print() + docstring + mkdocs checks |
| Git + GitHub | ✅ Clean — 7 commits pushed |

---

## What's Pending

### High Priority
- **Fix Anthropic API access** — once resolved, swap back to Claude (2-line change in `agents/general_agent.py` → `_build_llm()`):
  - Change import to `from langchain_anthropic import ChatAnthropic`
  - Change return to `return ChatAnthropic(model=self.config.model, temperature=self.config.temperature)`
  - Update `model` field in `create_general_agent()` to `"claude-sonnet-4-6"`
  - Set `ANTHROPIC_API_KEY` instead of `GROQ_API_KEY`
- **Set `SERPAPI_API_KEY`** — sign up at serpapi.com, get key, export before running backend

### Nice to Have
- **Streaming responses** — backend waits for full agentic loop; SSE or WebSockets would improve perceived speed
- **Move backend URL to env var** — `http://127.0.0.1:8181` hardcoded in `frontend/app/page.tsx`; move to `.env.local`
- **Session persistence** — history lost on page refresh (React state only)
- **Add more agents** — registry supports multiple agents; register additional `BaseAgent` subclasses in `ChatServer._register_agents()`
- **Add more tools** — register additional `@tool` functions in `ChatServer._register_tools()`
- **Agentic loop iteration cap** — no `max_iterations` guard; a misbehaving LLM could loop forever

---

## Environment Variables Required to Run

```bash
# Backend
export GROQ_API_KEY=...          # current (Groq)
export SERPAPI_API_KEY=...       # for search_web tool

# Optional
export LOG_LEVEL=DEBUG           # default: DEBUG
export LOG_TO_FILE=true          # default: true  (writes to backend/logs/agent.log)

# When switching back to Claude:
export ANTHROPIC_API_KEY=...     # replaces GROQ_API_KEY
```

## How to Run

```bash
# Backend
cd backend
source demoenv/bin/activate
uvicorn main:app --port 8181 --reload

# Frontend (separate terminal)
cd frontend
npm run dev
# → http://localhost:3000
```

## Git Log

| Commit | Message |
|--------|---------|
| `6604b74` | Initial commit: agentic chat app with Claude Sonnet 4.6 |
| `ee7967f` | chore: swap LLM back to Groq (openai/gpt-oss-120b) for testing |
| `ef643f7` | feat: implement search_web tool with SerpAPI (real Google results) |
| `89d7eb4` | docs: update CLAUDE.md and add PROGRESS.md session log |
| `fa20966` | refactor: OOP backend restructure with agents/, tools/ packages and structured logging |
| `f7f1cbc` | docs: add MkDocs site with full project documentation |
| `58bdd6a` | docs: update CLAUDE.md and PROGRESS.md with MkDocs session |

---

# Session: Feb 21, 2026

---

## What We Did Today

### 1. Migrated LLM from Groq → Claude Sonnet 4.6
- Replaced `langchain_groq.ChatGroq` with `langchain_anthropic.ChatAnthropic`
- Model set to `claude-sonnet-4-6`

### 2. Fixed the Agentic Loop (Critical Bug)
- The original loop called one tool and returned immediately — it never sent the tool result back to the model
- Rewrote `run_agent()` as a proper while loop: invokes model → executes all tool calls → feeds `ToolMessage` results back → repeats until no more tool calls → returns final response

### 3. Added Multi-Turn Conversation Support
- `main.py`: added `history: list[dict] = []` field to `ChatRequest`
- `agent.py`: converts history dicts to `HumanMessage` / `AIMessage` objects before the loop
- Frontend now sends full conversation history with every request

### 4. Redesigned the Frontend UI
- Header with "✦ AI Agent / Claude Sonnet 4.6" badge
- Clear chat button (trash icon, only shown when messages exist)
- Message bubbles: indigo for user (right), white card for Claude (left)
- Avatars: gradient "✦" for Claude, "You" circle for user
- Timestamps below each bubble
- Three-dot bouncing typing indicator while loading
- Auto-growing textarea (max 160px), resets after send
- Empty state with centered prompt when no messages
- Removed Next.js footer promo

### 5. Created CLAUDE.md
- Full project documentation for future Claude Code sessions
- Covers stack, how to run, backend/frontend internals, migration history, known TODOs

### 6. Fixed .gitignore (Was Completely Broken)
- File had markdown code fences (` ``` `) wrapping it — none of the rules were active
- Rewrote as a proper gitignore
- Added `demoenv/` and `*env/` to cover the Python virtualenv

### 7. Fixed Nested Git Repo
- `frontend/.git` existed, making it a broken git submodule from the root repo's perspective
- Removed `frontend/.git` so frontend is tracked as regular files

### 8. Populated requirements.txt
- Was empty — froze all deps from `demoenv` with `pip freeze`

### 9. Made First Git Commit & Pushed to GitHub
- Remote: `git@github.com:asequitytrading-design/ai-agent-ui.git`
- Initial commit: `6604b74` — 22 files

### 10. Swapped Back to Groq (Temporary)
- Anthropic API not working during testing
- Reverted to `ChatGroq(model="openai/gpt-oss-120b")` — agentic loop and all other logic unchanged
- Commit: `ee7967f`

### 11. Implemented Real search_web Tool with SerpAPI
- Replaced dummy stub with `SerpAPIWrapper().run(query)` from `langchain_community`
- Installed `google-search-results==2.4.2` and updated `requirements.txt`
- Commit: `ef643f7`

---

## Current State of the Codebase

| Component | Status |
|-----------|--------|
| FastAPI backend (`main.py`) | ✅ Working — `/chat` endpoint with history support |
| Agentic loop (`agent.py`) | ✅ Fixed — proper multi-turn tool loop |
| LLM | ⚠️ Groq `openai/gpt-oss-120b` (temporary — Claude Sonnet 4.6 intended) |
| `get_current_time` tool | ✅ Working |
| `search_web` tool | ✅ Implemented with SerpAPI — needs `SERPAPI_API_KEY` env var |
| Frontend chat UI | ✅ Redesigned and working |
| Multi-turn history | ✅ Working |
| Git + GitHub | ✅ Clean — 3 commits pushed |

---

## What's Pending

### High Priority
- **Fix Anthropic API access** — once resolved, swap back to Claude:
  - `agent.py` line 1: `from langchain_anthropic import ChatAnthropic`
  - `agent.py` line 29: `llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)`
  - Update comment on line 28 to reflect Claude again
- **Set `SERPAPI_API_KEY`** — sign up at serpapi.com, get key, export before running backend

### Nice to Have
- **Streaming responses** — currently the backend waits for the full agentic loop before responding; SSE or WebSockets would make the UI feel faster
- **Move backend URL to env var** — `http://127.0.0.1:8181` is hardcoded in `frontend/app/page.tsx`; move to `.env.local` for easier deployment
- **Session persistence** — conversation history is lost on page refresh (stored only in React state)
- **Real search_web error handling** — SerpAPI calls can fail; wrap in try/except and return a graceful error message
- **Replace placeholder tools** — `search_web` now uses SerpAPI; could add more tools (calculator, weather, etc.)

---

## Environment Variables Required to Run

```bash
# Backend
export GROQ_API_KEY=...          # current (Groq)
export SERPAPI_API_KEY=...       # for search_web tool

# When switching back to Claude:
export ANTHROPIC_API_KEY=...     # replaces GROQ_API_KEY
```

## How to Run

```bash
# Backend
cd backend
source demoenv/bin/activate
uvicorn main:app --port 8181 --reload

# Frontend (separate terminal)
cd frontend
npm run dev
# → http://localhost:3000
```

## Git Log

| Commit | Message |
|--------|---------|
| `6604b74` | Initial commit: agentic chat app with Claude Sonnet 4.6 |
| `ee7967f` | chore: swap LLM back to Groq (openai/gpt-oss-120b) for testing |
| `ef643f7` | feat: implement search_web tool with SerpAPI (real Google results) |

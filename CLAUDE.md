# CLAUDE.md — AI Agent UI

Project context for Claude Code. Read this before making any changes.

---

## What This Project Is

A fullstack agentic chat application:
- **Frontend**: Next.js 16 + React 19 + TypeScript + Tailwind CSS 4
- **Backend**: Python FastAPI + LangChain + Groq `openai/gpt-oss-120b` *(temporary — Claude Sonnet 4.6 is the intended LLM once Anthropic API access is fixed)*

The UI is a chat interface. The backend runs an agentic loop — the LLM can call tools (`get_current_time`, `search_web`) and keeps looping until it has a final answer before responding to the user.

---

## Project Structure

```
ai-agent-ui/
├── .gitignore             # Root gitignore (covers both frontend + backend)
├── CLAUDE.md              # This file — project context for Claude Code
├── PROGRESS.md            # Session log: what was done, what's pending
├── mkdocs.yml             # MkDocs config (material theme)
├── docs/                  # MkDocs source pages
│   ├── index.md
│   ├── backend/           # overview, api, agents, tools, config, logging
│   ├── frontend/          # overview
│   └── dev/               # how-to-run, decisions, changelog
├── frontend/              # Next.js app
│   ├── .gitignore         # Next.js-specific ignores (.next/, node_modules/, etc.)
│   ├── app/
│   │   ├── page.tsx       # Main chat UI (the only page)
│   │   ├── layout.tsx     # Root layout
│   │   └── globals.css    # Tailwind global styles
│   ├── public/            # Static SVG assets
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── next.config.ts
│   ├── eslint.config.mjs
│   └── postcss.config.mjs
│
└── backend/               # FastAPI server
    ├── main.py              # ChatServer class + uvicorn entry point
    ├── logging_config.py    # Centralised logging (console + rotating file)
    ├── config.py            # Pydantic Settings (env vars / .env file)
    ├── agents/
    │   ├── __init__.py
    │   ├── base.py          # AgentConfig dataclass + BaseAgent ABC
    │   ├── registry.py      # AgentRegistry
    │   └── general_agent.py # GeneralAgent (Groq) + factory function
    ├── tools/
    │   ├── __init__.py
    │   ├── registry.py      # ToolRegistry
    │   ├── time_tool.py     # get_current_time @tool
    │   └── search_tool.py   # search_web @tool
    ├── requirements.txt     # Frozen pip deps (from demoenv)
    ├── logs/                # Created at runtime — gitignored
    └── demoenv/             # Python virtualenv — NOT committed
```

---

## How to Run

### Backend
```bash
cd backend
source demoenv/bin/activate

export GROQ_API_KEY=...          # current LLM
export SERPAPI_API_KEY=...       # required for search_web tool

uvicorn main:app --port 8181 --reload
```

Optionally set `LOG_LEVEL` (default `DEBUG`) and `LOG_TO_FILE` (default `true`) as env vars, or put them in a `backend/.env` file.

### Frontend
```bash
cd frontend
npm install
npm run dev
# Runs on http://localhost:3000
```

The frontend hardcodes the backend URL as `http://127.0.0.1:8181` (move to `.env.local` before deploying).

### MkDocs (documentation)
```bash
cd ai-agent-ui
source backend/demoenv/bin/activate   # mkdocs installed in demoenv
mkdocs serve                           # → http://127.0.0.1:8000
mkdocs build --site-dir site/          # static build
```

### Install the pre-push hook (one-time setup)
```bash
cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

The hook blocks pushes to `main` if bare `print()` calls exist in backend Python or `mkdocs build` fails. See [Pre-Push Checklist](#pre-push-checklist) below.

To test the hook without pushing:
```bash
printf 'refs/heads/main 0000000000000000000000000000000000000000 refs/heads/main 0000000000000000000000000000000000000000\n' \
  | bash hooks/pre-push
```

---

## Pre-Push Checklist

**Every push to `main` must pass all five steps.** The git hook (`hooks/pre-push`, installed to `.git/hooks/pre-push`) enforces steps 1, 3, and 5 automatically. Steps 2 and 4 are manual.

---

### Step 1 — Google-style docstrings on all backend Python files

Every non-`__init__.py` file under `backend/` must have:
- A **module-level docstring** as the first statement (before any imports)
- **Class-level docstrings** on every class
- **Method/function docstrings** on every public method and `@tool` function

Format (Google style, Sphinx-compatible):
```python
"""One-sentence summary.

Longer description if needed.

Args:
    param_name: Description.

Returns:
    Description.

Raises:
    ExceptionType: When raised.

Example:
    >>> result = my_function(arg)
    >>> isinstance(result, str)
    True
"""
```

The hook **warns** (does not block) on missing module docstrings. Missing class/method docstrings are a manual check.

---

### Step 2 — OOP architecture and standard practices

Before pushing, verify:

- [ ] No new module-level mutable globals — all state in class instances
- [ ] New agents extend `BaseAgent`; only `_build_llm()` is overridden
- [ ] New tools registered via `ToolRegistry.register()` in `ChatServer._register_tools()`
- [ ] No direct cross-module imports of tool/agent internals — use registries
- [ ] Type annotations on all public function signatures
- [ ] `Optional[X]` used (not `X | Y` union syntax) — Python 3.9 compat
- [ ] New HTTP request/response bodies modelled as Pydantic classes in `main.py`
- [ ] No bare `except:` — always `except Exception` or a specific type

---

### Step 3 — Appropriate logging (no bare print() in backend)

The hook **hard blocks** pushes to `main` containing `print()` calls in backend Python.

Rules:
- Use `logging.getLogger(__name__)` per module (not a shared global)
- `DEBUG` for internal state, `INFO` for lifecycle events, `WARNING` for recoverable issues, `ERROR` for failures
- `print()` inside docstring examples is ignored (AST-based check, skips string literals)

Fix pattern:
```python
logger = logging.getLogger(__name__)
logger.debug("value: %s", x)   # NOT: print(x)
```

---

### Step 4 — Code review checklist

Self-review `git diff --staged` before committing:

- [ ] No secrets, API keys, or `.env` files staged
- [ ] No debug leftovers (`breakpoint()`, `# TODO`, temp `print()`)
- [ ] Error paths raise `HTTPException` with correct status codes — no errors in `200` bodies
- [ ] Tool failures return error strings (not exceptions) so LLM gets a `ToolMessage`
- [ ] `requirements.txt` updated if new packages installed (`pip freeze > backend/requirements.txt`)
- [ ] Commit message follows `type: description` convention (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`)

---

### Step 5 — Update docs and CLAUDE.md/PROGRESS.md

The hook **hard blocks** if `mkdocs build` fails. You must also:

- [ ] Update `PROGRESS.md` — add a dated session entry (what changed, why, commit hash)
- [ ] Update `CLAUDE.md` — reflect any changes to project structure, API, decisions, or How to Run
- [ ] Update the relevant `docs/` page(s) — new endpoints → `docs/backend/api.md`, decisions → `docs/dev/decisions.md`, etc.
- [ ] Run `mkdocs serve` locally to verify rendered output before pushing

---

## Backend Details

### `backend/main.py`
- All server state is encapsulated in `ChatServer`, which owns a `ToolRegistry`, an `AgentRegistry`, and the `FastAPI` app.
- Module-level startup at the bottom creates the singleton and exposes `app` for uvicorn.
- Two endpoints:
  - **`POST /chat`** — request: `{"message": str, "history": [...], "agent_id": str = "general"}`; response: `{"response": str, "agent_id": str}`
  - **`GET /agents`** — returns `{"agents": [{"id", "name", "description"}, ...]}`
- Error handling: `404` when `agent_id` is not registered; `500` on unhandled agent exceptions. Both use `HTTPException`, not error strings in a `200` body.

### `backend/logging_config.py`
- Single public function: `setup_logging(level, log_to_file, log_dir)`.
- Always adds a console (`stdout`) handler.
- When `log_to_file=True`, adds a `TimedRotatingFileHandler` that writes to `logs/agent.log`, rotates at midnight, keeps 7 days.
- Clears existing handlers before adding new ones so uvicorn hot-reload does not duplicate log lines.
- Log format: `YYYY-MM-DD HH:MM:SS,mmm | LEVEL    | logger.name | message`

### `backend/config.py`
- `Settings` is a Pydantic `BaseSettings` model; fields: `groq_api_key`, `anthropic_api_key`, `serpapi_api_key`, `log_level` (`"DEBUG"`), `log_to_file` (`True`).
- Reads from env vars; also reads `backend/.env` if present (env vars take precedence).
- `get_settings()` is cached with `@lru_cache` — parsed once per process.

### `backend/agents/`

**`base.py`**
- `AgentConfig` — dataclass with fields: `agent_id`, `name`, `description`, `model`, `temperature`, `system_prompt`, `tool_names`.
- `BaseAgent` — ABC implementing the full agentic loop in `run()`:
  1. Convert `history` dicts to `HumanMessage`/`AIMessage` objects.
  2. Invoke `llm_with_tools`.
  3. Execute all tool calls via `ToolRegistry.invoke()`, append `ToolMessage` results.
  4. Repeat until the model returns no tool calls, then return `response.content`.
- Subclasses only implement `_build_llm()` to supply a provider-specific chat model.

**`registry.py`**
- `AgentRegistry` — maps `agent_id` strings to `BaseAgent` instances.
- `register(agent)`, `get(agent_id) -> Optional[BaseAgent]`, `list_agents() -> list[dict]`.
- `get()` logs a `WARNING` (not an exception) when an ID is not found.

**`general_agent.py`**
- `GeneralAgent(BaseAgent)` — implements `_build_llm()` returning `ChatGroq(model=..., temperature=...)`.
- `create_general_agent(tool_registry)` — factory that builds an `AgentConfig` with `agent_id="general"`, model `"openai/gpt-oss-120b"`, and tools `["get_current_time", "search_web"]`, then returns a `GeneralAgent`.

### `backend/tools/`

**`registry.py`**
- `ToolRegistry` — maps tool name strings to LangChain `BaseTool` instances.
- `register(tool)`, `get(name)`, `get_tools(names) -> list[BaseTool]`, `invoke(name, args) -> str`, `list_names() -> list[str]`.
- `invoke()` returns `"Unknown tool: <name>"` rather than raising when a tool is missing, so the LLM receives a meaningful `ToolMessage`.

**`time_tool.py`**
- `get_current_time()` — `@tool`-decorated function; returns `str(datetime.datetime.now())`.

**`search_tool.py`**
- `search_web(query: str)` — `@tool`-decorated function; calls `SerpAPIWrapper().run(query)` (requires `SERPAPI_API_KEY`).
- Wraps in `try/except`; on failure returns `"Search failed: <reason>"` so the LLM receives a `ToolMessage` rather than an unhandled exception.

### Switching back to Claude (2-line change in `agents/general_agent.py`)
```python
# Line 1 — change import
from langchain_anthropic import ChatAnthropic

# Line 2 — change return in GeneralAgent._build_llm()
return ChatAnthropic(model=self.config.model, temperature=self.config.temperature)
```
Also update the `model` field in `create_general_agent()` to `"claude-sonnet-4-6"` and set `ANTHROPIC_API_KEY` instead of `GROQ_API_KEY`.

---

## Frontend Details

### `frontend/app/page.tsx`
- Single-page chat UI, `"use client"` component
- State: `messages` (array of `{role, content, timestamp}`), `input`, `loading`
- On send: appends user message, POSTs to backend with full `history` array, appends assistant reply
- Multi-turn: every request sends the full prior conversation as `history`

**UI elements:**
- Header with "✦ AI Agent / Claude Sonnet 4.6" badge + clear chat button (trash icon, only shown when messages exist)
- Chat bubbles: indigo for user (right), white card for assistant (left)
- Avatars: gradient "✦" circle for assistant, "You" circle for user
- Timestamps below each bubble
- Three-dot bouncing typing indicator while loading
- Auto-growing textarea (max 160px), resets after send
- Enter to send, Shift+Enter for newline
- Empty state with centered prompt when no messages

---

## Git & GitHub

- **Remote**: `git@github.com:asequitytrading-design/ai-agent-ui.git`
- **Branch**: `main`

| Commit | Message |
|--------|---------|
| `6604b74` | Initial commit: agentic chat app with Claude Sonnet 4.6 |
| `ee7967f` | chore: swap LLM back to Groq (openai/gpt-oss-120b) for testing |
| `ef643f7` | feat: implement search_web tool with SerpAPI (real Google results) |
| `89d7eb4` | docs: update CLAUDE.md and add PROGRESS.md session log |
| `fa20966` | refactor: OOP backend restructure with agents/, tools/ packages and structured logging |
| `f7f1cbc` | docs: add MkDocs site with full project documentation |

---

## Decisions Made

- **Virtualenv name is `demoenv`** — the root `.gitignore` covers it with `demoenv/` and `*env/`
- **`frontend/.git` was removed** — it was a nested git repo causing submodule issues; frontend is now tracked as regular files inside the root repo
- **SerpAPI chosen over Google Custom Search API** — simpler setup (one API key, no Google Cloud project), free tier is sufficient, already supported by `langchain-community`
- **`requirements.txt` is now frozen** — populated from `demoenv` with `pip freeze`; update it whenever new packages are installed
- **OOP refactor adopted** — backend restructured into `agents/` and `tools/` packages with `BaseAgent` ABC, `ToolRegistry`, and `AgentRegistry` for extensibility; adding a new agent or tool requires no changes to routing code
- **`ChatServer` class in `main.py`** — all server-level state (registries, app) encapsulated in a single class; avoids module-level globals
- **Structured logging over `print()`** — `logging_config.setup_logging()` configures the root logger; all modules use `logging.getLogger(__name__)` so log lines are filterable by module
- **Rotating file logs** — `TimedRotatingFileHandler` writes to `backend/logs/agent.log`; rotates daily, keeps 7 days; `logs/` directory is gitignored
- **`config.py` with Pydantic Settings** — env vars validated at startup; `.env` file supported; `get_settings()` cached with `@lru_cache`
- **Google-style Sphinx docstrings** added to all backend Python files (module-level + class + method)
- **Python 3.9 type annotation compat** — `X | Y` union syntax (PEP 604, Python 3.10+) replaced with `Optional[X]` from `typing`, since `demoenv` runs Python 3.9.13
- **MkDocs with material theme** — documentation site added; `mkdocs==1.6.1` and `mkdocs-material==9.7.2` installed in `demoenv`; 11 pages covering backend, frontend, API, decisions, and changelog; served with `mkdocs serve`
- **Pre-push git hook** — `hooks/pre-push` (committed; install with `cp hooks/pre-push .git/hooks/pre-push && chmod +x`); AST-based checks for `print()` (hard block) and module docstrings (warning); `mkdocs build` (hard block); only enforced on pushes to `main`

---

## Known Limitations / TODOs

- **Anthropic API not working** — currently on Groq as a workaround; switch back when resolved (see 2-line change in `agents/general_agent.py` → `_build_llm()` above)
- **`SERPAPI_API_KEY` must be set** — `search_web` will return an error string without it; get key at serpapi.com (100 free searches/month)
- **No streaming** — backend waits for full agentic loop before responding; SSE or WebSockets would improve perceived speed
- **No session persistence** — history lives only in React state, lost on page refresh
- **Backend URL hardcoded** — `http://127.0.0.1:8181` in `page.tsx`; move to `frontend/.env.local` before deploying

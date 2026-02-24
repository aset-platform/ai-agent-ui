# Changelog

Session-by-session record of what was built, changed, and fixed.

---

## Feb 24, 2026 (continued)

### Streaming, request timeout, iframe cross-origin, and dashboard light theme

Four independent improvements committed as a single session.

**Backend:**

| File | Change |
|------|--------|
| `backend/config.py` | Added `agent_timeout_seconds: int = 120` to `Settings` |
| `backend/agents/base.py` | Added `stream()` method — yields NDJSON events; added `json`, `Iterator` imports |
| `backend/main.py` | Added `asyncio`/`queue`/`threading`/`StreamingResponse` imports; `/chat` now uses `asyncio.wait_for` (HTTP 504 on timeout); new `POST /chat/stream` endpoint |

**Dashboard:**

| File | Change |
|------|--------|
| `dashboard/app.py` | `dbc.themes.DARKLY` → `dbc.themes.FLATLY`; added `@server.after_request` `allow_iframe` hook |
| `dashboard/assets/custom.css` | Full rewrite — light palette with CSS variables; indigo accent matching chat UI |
| `dashboard/callbacks.py` | All `template="plotly_dark"` → `"plotly_white"`; explicit `paper_bgcolor`/`plot_bgcolor`/`font`/`gridcolor`; annotation colors updated for light bg; stock card `text-white` → `text-dark`; table class updated |
| `dashboard/layouts.py` | NAVBAR `color="light"`, `dark=False`; H2 `text-white` removed; input `bg-dark text-white` removed; controls rows `bg-dark` → `bg-light border`; loading spinners `#4c8eff`/`#4caf50` → `#4f46e5` |

**Frontend:**

| File | Change |
|------|--------|
| `frontend/app/page.tsx` | `axios.post` → `fetch()` + `ReadableStream`; `TypingDots` → `StatusBadge`; `statusLine` state; `iframeLoading`/`iframeError` state; spinner + error banner on iframe; "Open in new tab ↗" in header; `switchView` resets iframe states; `handleInternalLink` resets iframe states |

**Commit:** *(this session)*

---

## Feb 24, 2026

### SPA navigation, session persistence, and UI hardening

Eight improvements across frontend and backend committed as a single session.

**Backend:**

| File | Change |
|------|--------|
| `backend/agents/base.py` | Added `MAX_ITERATIONS = 15` constant; guard at top of agentic loop logs `WARNING` and breaks when `iteration > MAX_ITERATIONS` |

**Frontend new files:**

| File | Description |
|------|-------------|
| `frontend/.env.local.example` | Committed reference for `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_DASHBOARD_URL`, `NEXT_PUBLIC_DOCS_URL` |

**Frontend changes (`frontend/app/page.tsx`):**

| Change | Detail |
|--------|--------|
| Env-based backend URL | `http://127.0.0.1:8181` replaced with `` `${process.env.NEXT_PUBLIC_BACKEND_URL}/chat` `` |
| localStorage persistence | Load-on-mount + save-on-change `useEffect` hooks; `Date` objects revived on load |
| `View` state + SPA routing | `"chat" \| "docs" \| "dashboard"` state; docs and dashboard rendered as full-height `<iframe>`; chat state preserved on view switch |
| `iframeUrl` state | Stores the specific URL when opened via an internal link; reset to `null` when switching via menu |
| Navigation menu | Grid icon FAB (bottom-right); `NAV_ITEMS` array with Chat / Docs / Dashboard; active view highlighted |
| `preprocessContent()` | Replaces chart file paths with dashboard links; strips data file paths |
| `handleInternalLink()` | Sets `view` + `iframeUrl` when a dashboard/docs link in chat is clicked |
| Internal link routing in `MarkdownContent` | `onInternalLink` prop; `a` renderer renders `<button>` for internal links, `<a target="_blank">` for external |

**Commit:** *(this session)*

---

## Feb 23, 2026 (continued)

### Per-agent history, analysis cache, and market news tool

Three independent improvements committed as `895df0f`.

**New files:**

| File | Description |
|------|-------------|
| `backend/tools/agent_tool.py` | `create_search_market_news_tool(general_agent)` — wraps `GeneralAgent` as a `@tool` callable by the stock agent |

**Modified:**

| File | Change |
|------|--------|
| `frontend/app/page.tsx` | Replaced single `messages` state with `histories: Record<string, Message[]>` keyed by `agentId`; switching agents now preserves each conversation independently |
| `backend/tools/price_analysis_tool.py` | Added same-day text cache (`data/cache/{TICKER}_analysis_{date}.txt`); `analyse_stock_price` returns cached result immediately on repeat calls |
| `backend/tools/forecasting_tool.py` | Same-day cache added (`data/cache/{TICKER}_forecast_{N}m_{date}.txt`); `forecast_stock` skips Prophet retraining if cache exists |
| `backend/agents/stock_agent.py` | Added `"search_market_news"` to `tool_names`; updated system prompt step 5 to call it before finalising each report |
| `backend/main.py` | Creates and registers `search_market_news` tool between general and stock agent construction (dependency order) |
| `.gitignore` | Added `data/cache/` |

**Commit:** `895df0f` — *feat: per-agent history, analysis cache, and market news tool*

---

## Feb 23, 2026

### Plotly Dash Dashboard (Phase 8)

Completed the four-page interactive web dashboard.

**New files:**

| File | Description |
|------|-------------|
| `dashboard/__init__.py` | Package marker with module docstring |
| `dashboard/app.py` | Dash entry point — DARKLY theme, `dcc.Location` routing, `dcc.Store`, `dcc.Interval`, `server` attr for gunicorn |
| `dashboard/layouts.py` | `home_layout`, `analysis_layout`, `forecast_layout`, `compare_layout` factories + `NAVBAR` |
| `dashboard/callbacks.py` | All interactive callbacks registered via `register_callbacks(app)` |
| `dashboard/assets/custom.css` | Dark theme overrides (cards, sliders, dropdowns, tables) |
| `run_dashboard.sh` | Convenience launcher script |
| `docs/dashboard/overview.md` | This documentation page |

**Bug fix:**

- Added `allow_duplicate=True` on `forecast-accuracy-row.children` in `run_new_analysis` callback — two callbacks write to that output and Dash requires explicit opt-in for duplicate outputs.

**Commits:**

| Hash | Message |
|------|---------|
| `c219dac` | feat: add Plotly Dash stock analysis dashboard (Phase 8) |
| `422e85b` | fix: allow duplicate forecast-accuracy-row output in run\_new\_analysis callback |

---

### Stock Analysis Agent

Added a full stock analysis capability backed by Yahoo Finance, Prophet, and Plotly.

**New files:**

| File | Description |
|------|-------------|
| `backend/agents/stock_agent.py` | `StockAgent(BaseAgent)` + `create_stock_agent` factory |
| `backend/tools/stock_data_tool.py` | 6 `@tool` functions — delta fetch + parquet storage |
| `backend/tools/price_analysis_tool.py` | `analyse_stock_price` — technical indicators + 3-panel chart |
| `backend/tools/forecasting_tool.py` | `forecast_stock` — Prophet forecast + confidence chart |
| `docs/stock_agent.md` | Stock agent documentation |

**Modified:**

- `backend/agents/base.py` — added `SystemMessage` support; `_build_messages()` prepends system prompt when set.
- `backend/main.py` — registered 8 stock tools and `StockAgent`.
- `frontend/app/page.tsx` — agent selector toggle (General / Stock Analysis).

**Commit:** `bdd3701` — *feat: add stock analysis agent with Yahoo Finance delta fetching, Prophet forecasting, price analysis, and Plotly charts*

---

## Feb 22, 2026

### OOP Backend Refactor

Deleted `backend/agent.py` and replaced it with a proper package structure.

**New files:**

| File | Description |
|------|-------------|
| `backend/agents/__init__.py` | Package marker |
| `backend/agents/base.py` | `AgentConfig` dataclass + `BaseAgent` ABC with full agentic loop |
| `backend/agents/registry.py` | `AgentRegistry` — maps agent IDs to agent instances |
| `backend/agents/general_agent.py` | `GeneralAgent(BaseAgent)` + `create_general_agent` factory |
| `backend/tools/__init__.py` | Package marker |
| `backend/tools/registry.py` | `ToolRegistry` — maps tool names to `BaseTool` instances |
| `backend/tools/time_tool.py` | `get_current_time` `@tool` |
| `backend/tools/search_tool.py` | `search_web` `@tool` (with try/except) |
| `backend/config.py` | `Settings(BaseSettings)` with `@lru_cache` singleton |
| `backend/logging_config.py` | `setup_logging()` — console + rotating file handler |

**Rewritten:**

- `backend/main.py` — full rewrite as `ChatServer` class; added `GET /agents` endpoint; `POST /chat` now accepts `agent_id` and returns it in the response; errors now raise `HTTPException` (404/500) instead of returning error strings in 200 bodies.

**Updated:**

- `.gitignore` — added `logs/` entry.
- `CLAUDE.md` — full sync with new file tree, API shapes, new decisions.
- `PROGRESS.md` — Feb 22 session log added.

**Commit:** `fa20966` — *refactor: OOP backend restructure with agents/, tools/ packages and structured logging*

---

### MkDocs Setup

- Installed `mkdocs==1.6.1` and `mkdocs-material==9.7.2` into `demoenv`.
- Created `mkdocs.yml` with material theme (indigo, light/dark toggle), navigation tabs, code copy buttons, and full nav tree.
- Created `docs/` directory structure with all pages.

---

## Feb 21, 2026

### Initial Build

Built the complete application from scratch in a single session.

**Backend (`backend/main.py`, `backend/agent.py`):**

- FastAPI server with CORS open to all origins.
- `POST /chat` endpoint accepting `message` and `history`.
- LangChain agentic loop in `run_agent()`: invokes LLM → executes tool calls → feeds `ToolMessage` results back → repeats until no tool calls → returns `response.content`.
- Two tools: `get_current_time` and `search_web`.

**Frontend (`frontend/app/page.tsx`):**

- Single-page chat UI with message bubbles, avatars, timestamps, typing indicator, auto-growing textarea.
- Full conversation history sent with every request.
- Error state shown in the chat bubble on network failure.

**LLM history:**

| Commit | LLM | Reason |
|--------|-----|--------|
| `6604b74` | Claude Sonnet 4.6 | Initial implementation |
| `ee7967f` | Groq `openai/gpt-oss-120b` | Anthropic API not working during testing |
| `ef643f7` | Groq (unchanged) | Added real SerpAPI search tool |

**Commits:**

| Hash | Message |
|------|---------|
| `6604b74` | Initial commit: agentic chat app with Claude Sonnet 4.6 |
| `ee7967f` | chore: swap LLM back to Groq (openai/gpt-oss-120b) for testing |
| `ef643f7` | feat: implement search_web tool with SerpAPI (real Google results) |
| `89d7eb4` | docs: update CLAUDE.md and add PROGRESS.md session log |

---

## Known Issues / Pending Work

| Issue | Priority | Notes |
|-------|----------|-------|
| Anthropic API not working | High | Switch back once access is fixed — see [How to Run](how-to-run.md) |

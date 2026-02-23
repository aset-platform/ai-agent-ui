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
├── STOCK_AGENT_PLAN.md    # Build plan for the stock analysis agent
├── mkdocs.yml             # MkDocs config (material theme)
├── docs/                  # MkDocs source pages
│   ├── index.md
│   ├── stock_agent.md     # Stock agent documentation
│   ├── backend/           # overview, api, agents, tools, config, logging
│   ├── frontend/          # overview
│   └── dev/               # how-to-run, decisions, changelog
│
├── data/                  # Stock data (gitignored except metadata/)
│   ├── raw/               # OHLCV parquet files: {TICKER}_raw.parquet
│   ├── processed/         # Dividend history parquet
│   ├── forecasts/         # Prophet forecast parquet: {TICKER}_{N}m_forecast.parquet
│   └── metadata/          # Tracked by git
│       ├── stock_registry.json        # Fetch registry (ticker, date, rows, path)
│       └── {TICKER}_info.json         # Company metadata cache (daily refresh)
│
├── charts/                # Generated Plotly HTML charts (gitignored)
│   ├── analysis/          # {TICKER}_analysis.html — candlestick + volume + RSI
│   └── forecasts/         # {TICKER}_forecast.html — price + confidence band
│
├── dashboard/             # Plotly Dash web dashboard (Phase 8 — complete)
│   ├── __init__.py        # Package init
│   ├── app.py             # Entry point — Dash app, DARKLY theme, page routing
│   ├── callbacks.py       # All interactive callbacks (analysis, forecast, compare)
│   ├── layouts.py         # Four page-layout factories + global NAVBAR
│   └── assets/
│       └── custom.css     # Dark theme overrides on top of DARKLY
├── run.sh                 # Unified launcher — start/stop/status/restart all four services
│
├── frontend/              # Next.js app
│   ├── .gitignore         # Next.js-specific ignores (.next/, node_modules/, etc.)
│   ├── app/
│   │   ├── page.tsx       # Main chat UI — agent selector toggle added
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
    │   ├── base.py          # AgentConfig dataclass + BaseAgent ABC (SystemMessage support added)
    │   ├── registry.py      # AgentRegistry
    │   ├── general_agent.py # GeneralAgent (Groq) + factory function
    │   └── stock_agent.py   # StockAgent (Groq) + create_stock_agent factory
    ├── tools/
    │   ├── __init__.py
    │   ├── registry.py      # ToolRegistry
    │   ├── time_tool.py     # get_current_time @tool
    │   ├── search_tool.py   # search_web @tool
    │   ├── stock_data_tool.py     # 6 @tools: fetch/load/list stock data (Yahoo Finance + parquet)
    │   ├── price_analysis_tool.py # 1 @tool: technical indicators + 3-panel Plotly chart
    │   └── forecasting_tool.py    # 1 @tool: Prophet forecast + confidence chart
    ├── requirements.txt     # Frozen pip deps (from demoenv)
    ├── logs/                # Created at runtime — gitignored
    └── demoenv/             # Python virtualenv — NOT committed
```

---

## How to Run

### All services at once (recommended)
```bash
export GROQ_API_KEY=...          # required for chat backend
export SERPAPI_API_KEY=...       # required for search_web tool

./run.sh start      # starts all four services in the background
./run.sh status     # show PID + URL for each service
./run.sh stop       # stop everything
./run.sh restart    # stop then start
```

| Service | URL |
|---------|-----|
| Backend (FastAPI) | http://127.0.0.1:8181 |
| Frontend (Next.js) | http://localhost:3000 |
| Docs (MkDocs) | http://127.0.0.1:8000 |
| Dashboard (Dash) | http://127.0.0.1:8050 |

Logs are written to `/tmp/ai-agent-ui-logs/`.

### Individual services (manual)
```bash
# Backend
cd backend && source demoenv/bin/activate
uvicorn main:app --port 8181 --reload

# Frontend
cd frontend && npm install && npm run dev

# Dashboard
source backend/demoenv/bin/activate && python dashboard/app.py

# Docs
source backend/demoenv/bin/activate && mkdocs serve
```

Optionally set `LOG_LEVEL` (default `DEBUG`) and `LOG_TO_FILE` (default `true`) as env vars, or put them in a `backend/.env` file.

### MkDocs — static build
```bash
source backend/demoenv/bin/activate
mkdocs build --site-dir site/
```

### Stock agent — run pipeline manually (without the LLM)
```bash
cd ai-agent-ui
source backend/demoenv/bin/activate
python -c "
import sys; sys.path.insert(0, 'backend')
from tools.stock_data_tool import fetch_stock_data, list_available_stocks
from tools.price_analysis_tool import analyse_stock_price
from tools.forecasting_tool import forecast_stock

print(fetch_stock_data.invoke({'ticker': 'AAPL'}))
print(analyse_stock_price.invoke({'ticker': 'AAPL'}))
print(forecast_stock.invoke({'ticker': 'AAPL', 'months': 9}))
print(list_available_stocks.invoke({}))
"
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

## Stock Agent Dependencies

Installed into `demoenv` during the Feb 23, 2026 session:

| Package | Version | Purpose |
|---|---|---|
| `yfinance` | 1.2.0 | Yahoo Finance OHLCV + company info |
| `pandas` | 2.3.3 | DataFrame manipulation |
| `numpy` | 2.0.2 | Numerical operations |
| `scikit-learn` | 1.6.1 | Available for ML extensions |
| `prophet` | 1.3.0 | Meta time-series forecasting |
| `plotly` | 6.5.2 | Interactive HTML charts |
| `ta` | 0.11.0 | Technical analysis indicators |
| `pyarrow` | 17.0.0 | Parquet file read/write (capped <18 for Python 3.9) |
| `dash` | 4.0.0 | Web dashboard framework (Phase 8) |
| `dash-bootstrap-components` | 2.0.4 | Dashboard styling (Phase 8) |

Yahoo Finance requires no API key. All stock data is stored locally in parquet format.

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

### `backend/agents/stock_agent.py`
- `StockAgent(BaseAgent)` — extends `BaseAgent`, overrides only `_build_llm()` to return `ChatGroq`.
- `_STOCK_SYSTEM_PROMPT` — instructs the LLM to follow the fetch → analyse → forecast pipeline and format responses as structured reports.
- `create_stock_agent(tool_registry)` — factory; `agent_id="stock"`, model `"openai/gpt-oss-120b"`, 8 tool names.
- Same 2-line Claude switch as `GeneralAgent`.

### `backend/tools/stock_data_tool.py`
Six `@tool` functions for Yahoo Finance data management:
- `fetch_stock_data(ticker, period="10y")` — full fetch on first call, delta fetch on subsequent calls, skips if up to date. Saves to `data/raw/{TICKER}_raw.parquet`.
- `get_stock_info(ticker)` — company metadata, cached to `data/metadata/{TICKER}_info.json` (daily refresh).
- `load_stock_data(ticker)` — summary of locally stored parquet (no network call).
- `fetch_multiple_stocks(tickers, period="10y")` — batch wrapper over `fetch_stock_data`.
- `get_dividend_history(ticker)` — saves to `data/processed/{TICKER}_dividends.parquet`.
- `list_available_stocks()` — reads `stock_registry.json`, prints formatted table.

### `backend/tools/price_analysis_tool.py`
One `@tool` function (`analyse_stock_price`) backed by private helpers:
- Computes SMA 50/200, EMA 20, RSI 14, MACD, Bollinger Bands, ATR 14 using `ta` library.
- Analyses bull/bear phases, max drawdown, support/resistance, annualised volatility, Sharpe ratio.
- Generates 3-panel Plotly dark chart (candlestick + volume + RSI), saved to `charts/analysis/{TICKER}_analysis.html`.
- Returns a formatted string report with all metrics.

### `backend/tools/forecasting_tool.py`
One `@tool` function (`forecast_stock`) backed by private helpers:
- Prepares data in Prophet `ds`/`y` format using `Adj Close`.
- Trains Prophet with yearly + weekly seasonality, US federal holidays, 80% confidence interval.
- Generates price targets at 3, 6, 9 month marks.
- Evaluates accuracy via 12-month in-sample backtest (MAE, RMSE, MAPE).
- Saves forecast to `data/forecasts/{TICKER}_{N}m_forecast.parquet`.
- Generates Plotly forecast chart (historical + forecast + confidence band + annotations), saved to `charts/forecasts/{TICKER}_forecast.html`.

### `backend/agents/base.py` — fix applied Feb 23, 2026
- `SystemMessage` import added; `_build_messages()` now prepends a `SystemMessage` when `config.system_prompt` is non-empty.
- `GeneralAgent` unaffected (`system_prompt=""` by default).

### Switching back to Claude (2-line change in `agents/general_agent.py` and `agents/stock_agent.py`)
```python
# Line 1 — change import
from langchain_anthropic import ChatAnthropic

# Line 2 — change return in GeneralAgent._build_llm()
return ChatAnthropic(model=self.config.model, temperature=self.config.temperature)
```
Also update the `model` field in `create_general_agent()` to `"claude-sonnet-4-6"` and set `ANTHROPIC_API_KEY` instead of `GROQ_API_KEY`.

---

## Dashboard Details

### How to Run the Dashboard
```bash
./run.sh start      # starts all four services including the dashboard
./run.sh status     # shows PID + URL for each service
./run.sh stop       # stops everything
```

Dashboard URL: `http://127.0.0.1:8050`. No API keys required — reads local parquet files directly.

### Four Pages

| Page | Route | What it does |
|------|-------|-------------|
| Home | `/` | Stock cards (price, 10Y return, AI sentiment), search/dropdown to navigate |
| Analysis | `/analysis` | 3-panel interactive chart (candlestick + RSI + MACD), date-range slider, overlay toggles (SMA 50/200, BB, Volume), 6 stat cards |
| Forecast | `/forecast` | Prophet forecast chart, price-target cards (3m/6m/9m), accuracy metrics, "Run New Analysis" button |
| Compare | `/compare` | Normalised performance chart, metrics table (Sharpe, drawdown, RSI, MACD, 6M upside), returns correlation heatmap |

### Architecture

- **`dashboard/app.py`** — Creates the `dash.Dash` instance with `dbc.themes.DARKLY`, a `dcc.Location` for routing, a `dcc.Store` (`nav-ticker-store`) to pass selected tickers between pages, a 5-minute `dcc.Interval` to auto-refresh stock cards, and the `display_page` page-routing callback. Exposes `server` for gunicorn deployment.
- **`dashboard/layouts.py`** — Stateless layout factories; reads the stock registry once at call time to populate dropdowns. No callbacks live here.
- **`dashboard/callbacks.py`** — All interactive logic via `register_callbacks(app)`. Reads OHLCV parquet with `_load_raw()` and forecast parquet with `_load_forecast()`. The "Run New Analysis" button imports backend tool functions directly (no HTTP) to run a full fetch → Prophet pipeline.
- **`dashboard/assets/custom.css`** — Dark theme overrides (stock cards, stat cards, sliders, dropdowns, tables).

### Key Callback Interactions

| Callback | Inputs | Outputs |
|----------|--------|---------|
| `refresh_stock_cards` | `registry-refresh.n_intervals`, `url.pathname` | `stock-cards-container.children`, `home-registry-dropdown.options` |
| `navigate_to_analysis` | `search-btn.n_clicks`, `home-registry-dropdown.value` | `url.pathname`, `nav-ticker-store.data` |
| `sync_analysis_ticker` | `url.search`, `url.pathname` | `analysis-ticker-dropdown.value` |
| `update_analysis_chart` | ticker, date-range slider, overlay toggles | `analysis-chart.figure`, `analysis-stats-row.children` |
| `update_forecast_chart` | ticker, horizon radio, refresh store | forecast chart, target cards, accuracy row |
| `run_new_analysis` | `run-analysis-btn.n_clicks` | status alert, refresh store, accuracy row |
| `update_compare` | `compare-ticker-dropdown.value` | perf chart, metrics table, heatmap |

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

- **Anthropic API not working** — currently on Groq as a workaround; switch back when resolved (see 2-line change in `agents/general_agent.py` and `agents/stock_agent.py` → `_build_llm()` above)
- **`SERPAPI_API_KEY` must be set** — `search_web` will return an error string without it; get key at serpapi.com (100 free searches/month)
- **No streaming** — backend waits for full agentic loop before responding; SSE or WebSockets would improve perceived speed
- **No session persistence** — history lives only in React state, lost on page refresh
- **Backend URL hardcoded** — `http://127.0.0.1:8181` in `page.tsx`; move to `frontend/.env.local` before deploying

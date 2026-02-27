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
├── .github/
│   ├── workflows/ci.yml           # GitHub Actions CI (dev/qa/release/main jobs)
│   ├── CODEOWNERS                 # Reviewer groups per merge path
│   └── pull_request_template.md   # Standard PR checklist
├── .gitignore             # Root gitignore (covers both frontend + backend)
├── CLAUDE.md              # This file — project context for Claude Code
├── PROGRESS.md            # Session log: what was done, what's pending
├── STOCK_AGENT_PLAN.md    # Build plan for the stock analysis agent
├── details.txt            # Branching strategy spec
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
    │   ├── agent_tool.py    # create_search_market_news_tool — wraps GeneralAgent as @tool
    │   ├── stock_data_tool.py     # 6 @tools: fetch/load/list stock data (Yahoo Finance + parquet)
    │   ├── price_analysis_tool.py # 1 @tool: technical indicators + 3-panel Plotly chart + same-day cache
    │   └── forecasting_tool.py    # 1 @tool: Prophet forecast + confidence chart + same-day cache
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

### Install the pre-commit hook (one-time setup)
```bash
cp hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

Runs on every commit. Operates only on staged modified/created files (`git diff --cached --diff-filter=ACM`). Four checks:
1. **Code quality** — bare `print()` (blocks), missing Google docstrings (warning), naming, OOP, XSS/SQL injection; auto-fixed via Claude (`claude-sonnet-4-6`) when `ANTHROPIC_API_KEY` is set; shows `(branch: <name>)` in the banner
2. **Meta-files** — CLAUDE.md, PROGRESS.md, README.md updated if stale; single Claude call
3. **Docs** — affected `docs/` pages patched when their source files are staged; `mkdocs build` runs after (warn-only, non-blocking)
4. **Changelog** — `docs/dev/changelog.md` H2 date headings verified descending; auto-reordered (no API)

**Branch awareness** — warns (does not block) when committing directly to `main`, `qa`, or `release`.

Environment variables: `SKIP_PRE_COMMIT=1` (bypass all), `SKIP_CLAUDE_CHECKS=1` (static-only, no API), `ANTHROPIC_API_KEY` (enables LLM auto-fix + doc updates).

To test without committing:
```bash
bash hooks/pre-commit
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

### `backend/tools/agent_tool.py`
Factory function `create_search_market_news_tool(general_agent)` — call after `create_general_agent()` and before `create_stock_agent()`:
- Returns a `@tool`-decorated `search_market_news(query: str) -> str` that delegates to `general_agent.run(query, history=[])`.
- Stock agent calls this tool to enrich reports with live news before finalising.
- Error-safe: returns `"News search failed: <reason>"` on exception.

### `backend/tools/price_analysis_tool.py`
One `@tool` function (`analyse_stock_price`) backed by private helpers:
- Checks `data/cache/{TICKER}_analysis_{YYYY-MM-DD}.txt` — returns cached result immediately if found.
- Computes SMA 50/200, EMA 20, RSI 14, MACD, Bollinger Bands, ATR 14 using `ta` library.
- Analyses bull/bear phases, max drawdown, support/resistance, annualised volatility, Sharpe ratio.
- Generates 3-panel Plotly dark chart (candlestick + volume + RSI), saved to `charts/analysis/{TICKER}_analysis.html`.
- Saves result to cache and returns formatted string report.

### `backend/tools/forecasting_tool.py`
One `@tool` function (`forecast_stock`) backed by private helpers:
- Checks `data/cache/{TICKER}_forecast_{N}m_{YYYY-MM-DD}.txt` — returns cached result immediately if found.
- Prepares data in Prophet `ds`/`y` format using `Adj Close`.
- Trains Prophet with yearly + weekly seasonality, US federal holidays, 80% confidence interval.
- Generates price targets at 3, 6, 9 month marks.
- Evaluates accuracy via 12-month in-sample backtest (MAE, RMSE, MAPE).
- Saves forecast to `data/forecasts/{TICKER}_{N}m_forecast.parquet`.
- Saves result to cache and generates Plotly forecast chart, saved to `charts/forecasts/{TICKER}_forecast.html`.

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
| `refresh_stock_cards` | `registry-refresh.n_intervals`, `url.pathname` | `stock-raw-data-store.data`, `home-registry-dropdown.options` |
| `update_market_filter` | `filter-india-btn.n_clicks`, `filter-us-btn.n_clicks` | `market-filter-store.data`, button colors, `home-pagination.active_page` |
| `render_home_cards` | `stock-raw-data-store.data`, `market-filter-store.data`, `home-pagination.active_page`, `home-page-size.value` | `stock-cards-container.children`, pagination `max_value`, count text |
| `render_users_page` | `users-store.data`, `users-pagination.active_page`, `users-search.value`, `users-page-size.value` | users table, pagination `max_value`, count text |
| `render_audit_page` | `audit-data-store.data`, `audit-pagination.active_page`, `audit-search.value`, `audit-page-size.value` | audit table, pagination `max_value`, count text |
| `navigate_to_analysis` | `search-btn.n_clicks`, `home-registry-dropdown.value` | `url.pathname`, `nav-ticker-store.data` |
| `sync_analysis_ticker` | `url.search`, `url.pathname` | `analysis-ticker-dropdown.value` |
| `update_analysis_chart` | ticker, date-range slider, overlay toggles | `analysis-chart.figure`, `analysis-stats-row.children` |
| `update_forecast_chart` | ticker, horizon radio, refresh store | forecast chart, target cards, accuracy row |
| `run_new_analysis` | `run-analysis-btn.n_clicks` | status alert, refresh store, accuracy row |
| `update_compare` | `compare-ticker-dropdown.value` | perf chart, metrics table, heatmap |

### Home Page — Market Filter + Pagination (added Feb 25, 2026)

- `_get_market(ticker)` in `callbacks.py`: returns `'india'` for `.NS`/`.BO` tickers, `'us'` otherwise
- **Data/render split**: `refresh_stock_cards` stores raw serialisable dicts in `dcc.Store(id="stock-raw-data-store")`; `render_home_cards` reads the store, filters by market, paginates, builds card components
- Default market: India (`.NS`/`.BO`); UI shows "🇮🇳 India" / "🇺🇸 US" `dbc.ButtonGroup`
- Page size: 12 default; `dbc.Select(id="home-page-size")` with options 10/25/50/100
- Pagination footer sits above the fixed Next.js FAB + Plotly watermark (`paddingBottom: "5rem"` on `#page-content`)

### Admin — Users + Audit Log Pagination (added Feb 25, 2026)

- `load_users_table` → `users-store.data` (raw list); `render_users_page` → sliced table + count text
- `load_audit_log` → `audit-data-store.data` (raw list); `render_audit_page` → sliced table + count text
- Search: `dbc.Input(debounce=True)` above each table; users search filters name/email/role; audit search filters event_type/actor/target/metadata
- Page size: `dbc.Select` (10/25/50/100) beside each paginator; filter or size change resets to page 1

---

## Frontend Details

### `frontend/app/page.tsx`
- `"use client"` component — full SPA with three embedded views
- **`type View = "chat" | "docs" | "dashboard"`** — controls which surface is shown

**State:**
- `view` — active surface; switching keeps the component mounted so chat state is preserved
- `iframeUrl` — specific URL for the iframe (e.g. `/analysis?ticker=AAPL`); `null` falls back to the service base URL; reset to `null` when switching via the menu
- `histories: Record<string, Message[]>` keyed by `agentId` — per-agent chat history, persisted to `localStorage`
- `input`, `loading`, `agentId`, `menuOpen`

**Navigation:**
- Bottom-right FAB (grid icon, `fixed bottom-6 right-6 z-50`) opens a popup menu with Chat / Docs / Dashboard items
- Active view highlighted with indigo background + dot indicator
- `switchView(v)` sets `view`, resets `iframeUrl` to `null`, closes menu
- When `view !== "chat"`: `<iframe src={iframeUrl ?? baseUrl} className="flex-1 w-full border-0" />` fills remaining height
- When `view === "chat"`: scrollable `<main>` + sticky `<footer>` input area

**Internal link routing:**
- `preprocessContent()` replaces `*/charts/analysis/{TICKER}_analysis.html` → `[View {TICKER} Analysis →]({DASHBOARD_URL}/analysis?ticker={TICKER})` and forecast paths; strips `*/data/...` paths
- `MarkdownContent` takes `onInternalLink: (href) => void`; the `a` renderer checks if `href` starts with `NEXT_PUBLIC_DASHBOARD_URL` or `NEXT_PUBLIC_DOCS_URL` — if so, renders a `<button>` calling `onInternalLink` (opens in-app); otherwise `<a target="_blank">` for external links
- `handleInternalLink(href)` sets `view` + `iframeUrl` so the iframe loads the exact page

**Session persistence:**
- Load-on-mount `useEffect` reads `chat_histories` from `localStorage` and revives `Date` objects
- Save-on-change `useEffect` writes `histories` to `localStorage` on every update

**Header adapts by view:**
- Chat: agent selector toggle (General / Stock Analysis) + clear button (when messages exist)
- Docs / Dashboard: text breadcrumb ("Documentation" / "Dashboard"); no input area shown

**`frontend/.env.local` (gitignored) / `frontend/.env.local.example` (committed):**
```
NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:8181
NEXT_PUBLIC_DASHBOARD_URL=http://127.0.0.1:8050
NEXT_PUBLIC_DOCS_URL=http://127.0.0.1:8000
```

---

## Branching Strategy (added Feb 27, 2026)

Full `feature/* → dev → qa → release → main` workflow. See `details.txt` for the complete spec.

### Branch hierarchy

| Branch | Purpose | Environment |
|--------|---------|-------------|
| `feature/*` | Individual features, branched off `dev` | Local |
| `dev` | Active development / integration | Dev |
| `qa` | QA / testing | QA |
| `release` | Release candidates, tagged versions | Staging / pre-prod |
| `main` | Production-ready | Production |

`hotfix/*` branches off `main` for urgent production fixes, then backported to `dev`.

### Flow

```
feature/* → dev → qa → release → main
hotfix/*  → release + main (emergency, then backport to dev)
```

### CI/CD (`.github/workflows/ci.yml`)

| Branch | CI steps |
|--------|----------|
| `dev` | Python deps + mkdocs build + Next.js lint |
| `qa` | All dev steps + backend smoke test (curl `/agents`) |
| `release` | All qa steps + staging deploy marker |
| `main` | All checks + production deploy gate |

### GitHub files

| File | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | Per-branch CI jobs |
| `.github/CODEOWNERS` | Reviewer groups per merge path |
| `.github/pull_request_template.md` | Standard PR checklist |

### Branch protection rules (apply manually in GitHub → Settings → Branches)

- `main`: no direct push, 2 approvals, CI must pass
- `release`: PR from `qa` only, 1 approval + QA lead, CI must pass
- `qa`: PR from `dev` only, 1 approval, integration tests must pass
- `dev`: PR from `feature/*`, 1 approval, unit tests + lint must pass

### Claude Code rules

- Always branch off `dev` for new features (never off `main` or `qa`)
- Pre-commit hook **warns** when committing directly to `main`, `qa`, or `release`
- PR title format: `[TYPE] Short description` — Types: `feat | fix | chore | refactor | hotfix | docs`
- Tag releases: `git tag -a v1.0.0 -m "Release v1.0.0"`

---

## Git & GitHub

- **Remote**: `git@github.com:asequitytrading-design/ai-agent-ui.git`
- **Branches**: `main`, `dev`, `qa`, `release`

| Commit | Message |
|--------|---------|
| `6604b74` | Initial commit: agentic chat app with Claude Sonnet 4.6 |
| `ee7967f` | chore: swap LLM back to Groq (openai/gpt-oss-120b) for testing |
| `ef643f7` | feat: implement search_web tool with SerpAPI (real Google results) |
| `89d7eb4` | docs: update CLAUDE.md and add PROGRESS.md session log |
| `fa20966` | refactor: OOP backend restructure with agents/, tools/ packages and structured logging |
| `f7f1cbc` | docs: add MkDocs site with full project documentation |
| `eb4e64a` | feat: render assistant messages as markdown HTML in chat UI |
| `895df0f` | feat: per-agent history, analysis cache, and market news tool |

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
- **Per-agent chat history** — `histories: Record<string, Message[]>` in `page.tsx` replaces a single `messages` array; switching between agents no longer clears conversations
- **Same-day cache for analysis/forecast** — `analyse_stock_price` and `forecast_stock` write results to `data/cache/{TICKER}_{key}_{YYYY-MM-DD}.txt`; repeat calls within the same calendar day return instantly; `data/cache/` is gitignored
- **Agent-to-agent tool via factory** — `create_search_market_news_tool(general_agent)` in `tools/agent_tool.py` wraps the General Agent as a `@tool` so the Stock Agent can delegate web searches without direct SerpAPI coupling; must be registered after the General Agent but before the Stock Agent
- **Agentic loop iteration cap** — `MAX_ITERATIONS = 15` constant in `backend/agents/base.py`; guard at the top of the `while True:` loop logs `WARNING` and breaks on iteration > 15
- **Frontend env configuration** — `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_DASHBOARD_URL`, `NEXT_PUBLIC_DOCS_URL` in `frontend/.env.local`; `frontend/.env.local.example` committed as reference; `.gitignore` has `!.env.local.example` negation
- **Session persistence** — `chat_histories` saved to `localStorage` on every state change; revived on mount with `new Date(m.timestamp)` to restore Date objects; clear button clears only the active agent's history (localStorage auto-updated by the save effect)
- **Bottom-left navigation menu** — fixed FAB (grid icon, `bottom-6 left-6`) opens a popup with Docs and Dashboard links; click-outside handler via `useRef` + `mousedown` event
- **Path replacement in messages** — `preprocessContent()` in `page.tsx` replaces `*/charts/analysis/{TICKER}_analysis.html` → `[View {TICKER} Analysis →]({DASHBOARD_URL}/analysis?ticker={TICKER})` and forecast equivalents; strips `*/data/...` paths; applied before `ReactMarkdown` renders

---

## Streaming (NDJSON)

`POST /chat/stream` streams one JSON event per line (`application/x-ndjson`) as the agentic loop progresses. The frontend uses native `fetch()` + `ReadableStream` to consume events and show a live `StatusBadge`.

Event types: `thinking`, `tool_start`, `tool_done`, `warning`, `final`, `error`, `timeout`.

The generator runs in a daemon thread inside `_chat_stream_handler`; events pass through a `queue.Queue`. Timeout cuts the stream after `agent_timeout_seconds` seconds and emits a `timeout` event.

The old `POST /chat` endpoint is unchanged (sync, returns `ChatResponse`), also has the timeout applied (HTTP 504 on timeout).

## Request Timeout

Configured via `agent_timeout_seconds` in `Settings` (default 120 seconds). Set `AGENT_TIMEOUT_SECONDS=3` in `backend/.env` to test. Applied to both `/chat` (asyncio) and `/chat/stream` (queue timeout).

## Dashboard Theme

Dashboard uses FLATLY (light Bootstrap theme) — `#f9fafb` background, white cards, `#4f46e5` indigo accent — matching the chat frontend. Plotly charts use `template="plotly_white"` with explicit `paper_bgcolor`/`plot_bgcolor`/`gridcolor`.

## Iframe Embedding

Dashboard Flask server sends `X-Frame-Options: ALLOWALL` and `Content-Security-Policy: frame-ancestors *` on every response (added via `@server.after_request`). Frontend `<iframe>` has a loading spinner overlay (disappears on `onLoad`) and an error banner (appears on `onError`) with an "Open in new tab ↗" link.

## Auth Module (Phases 1–5 complete, Phase 6 pending)

JWT authentication + role-based access control across all three surfaces.

### Storage
Apache Iceberg via PyIceberg + SQLite (`data/iceberg/catalog.db`). Initialise: `python auth/create_tables.py`.

### Backend (`auth/` package)
- `auth/create_tables.py` — idempotent Iceberg table init
- `auth/repository.py` — `IcebergUserRepository` (CRUD + audit log)
- `auth/service.py` — `AuthService` (bcrypt, JWT HS256, in-memory deny-list)
- `auth/models.py` — Pydantic request/response models
- `auth/dependencies.py` — `get_current_user`, `superuser_only` FastAPI deps
- `auth/api.py` — `create_auth_router()` factory (12 endpoints mounted at root)

### Endpoints
`POST /auth/login`, `POST /auth/login/form`, `POST /auth/refresh`, `POST /auth/logout`,
`POST /auth/password-reset/request`, `POST /auth/password-reset/confirm`,
`GET /users`, `POST /users`, `GET /users/{id}`, `PATCH /users/{id}`, `DELETE /users/{id}`,
`GET /admin/audit-log`

### Frontend (Next.js)
- `frontend/lib/auth.ts` — token helpers (getAccessToken, setTokens, clearTokens, isTokenExpired, getRoleFromToken, refreshAccessToken)
- `frontend/lib/apiFetch.ts` — drop-in authenticated fetch wrapper; auto-refreshes tokens; redirects to `/login` on 401
- `frontend/app/login/page.tsx` — login page
- `frontend/app/page.tsx` — auth guard on mount; logout button; `apiFetch` for API calls; Admin nav item visible to superusers only; `"admin"` view type routes to `/admin/users` Dash page

### Dashboard (Plotly Dash — Phase 5)
- `dcc.Store(id="auth-token-store", storage_type="local")` persists JWT in localStorage
- Token extracted from `?token=` query param and stored via `store_token_from_url` callback
- `display_page` validates token before rendering any page; shows `_unauth_notice()` for invalid tokens, `_admin_forbidden()` for non-superusers on `/admin/*`
- **`/admin/users`**: superuser-only page with two tabs:
  - *Users*: DataTable of all accounts + "Add User" button + per-row Edit / Deactivate-Reactivate buttons + inline modal
  - *Audit Log*: full event table (event type, actor, target, metadata)
- **Change Password**: NAVBAR button opens a global modal; calls `/auth/password-reset/request` then `/auth/password-reset/confirm`
- `_api_call(method, path, token, json_body)` helper in `callbacks.py` makes authenticated HTTP requests to the FastAPI backend (`BACKEND_URL` env var, default `http://127.0.0.1:8181`)
- Token propagated to Dash via `?token=<jwt>` query param when Next.js renders the iframe; admin view appends token to `/admin/users` URL

### Environment variables required for auth
```
JWT_SECRET_KEY=<min-32-random-chars>
ACCESS_TOKEN_EXPIRE_MINUTES=60   # default
REFRESH_TOKEN_EXPIRE_DAYS=7      # default
BACKEND_URL=http://127.0.0.1:8181  # for dashboard callbacks (default)
FRONTEND_URL=http://localhost:3000  # for redirect links in Dash (default)
```

### Phase 6 complete
- `scripts/seed_admin.py` — reads `ADMIN_EMAIL` + `ADMIN_PASSWORD` + `JWT_SECRET_KEY` from env/.env; validates password strength; creates superuser if not exists; idempotent
- `run.sh` — `_init_auth()` function runs `create_tables.py` + `seed_admin.py` on first `./run.sh start` (guards on `data/iceberg/catalog.db` existence)
- `docs/backend/auth.md` — full MkDocs auth documentation page (endpoints, models, config, security notes)
- `mkdocs.yml` — added "Auth & Users" to Backend nav section; `mkdocs build` passes

### Auth deployment fixes (Feb 25, 2026)
Two runtime bugs fixed after first deploy:

**Fix 1 — JWT env propagation (`backend/main.py`)**
`auth/dependencies.py` reads `JWT_SECRET_KEY` from `os.environ` directly. Pydantic `Settings`
reads `backend/.env` but does **not** write values back to `os.environ`. Fix: module-level startup
block in `main.py` now copies all three JWT fields from `settings` into `os.environ` if absent:
```python
if settings.jwt_secret_key and "JWT_SECRET_KEY" not in os.environ:
    os.environ["JWT_SECRET_KEY"] = settings.jwt_secret_key
```

**Fix 2 — Dashboard dotenv (`dashboard/app.py`)**
The Dash process never loaded `backend/.env`, so `_validate_token()` always got an empty secret
and returned `None` → "Authentication required" in every iframe. Fix: `_load_dotenv()` helper
added at the top of `dashboard/app.py` (before imports) reads both `.env` and `backend/.env`
into `os.environ` at startup.

---

## SSO / OAuth2 (added Feb 26, 2026)

Google + Facebook OAuth2 PKCE on top of the existing JWT auth.

### New files

| File | Purpose |
|------|---------|
| `auth/oauth_service.py` | `OAuthService` — state store, PKCE authorize URL, code exchange |
| `auth/migrate_users_table.py` | Idempotent Iceberg schema migration (adds 3 OAuth columns) |
| `frontend/lib/oauth.ts` | PKCE helpers + sessionStorage helpers |
| `frontend/app/auth/oauth/callback/page.tsx` | OAuth callback page |
| `docs/backend/oauth.md` | SSO documentation |

### New columns in `auth.users`

| Column | Type | Notes |
|--------|------|-------|
| `oauth_provider` | StringType (nullable) | `"google"` / `"facebook"` / `None` |
| `oauth_sub` | StringType (nullable) | Provider-specific user ID |
| `profile_picture_url` | StringType (nullable) | Refreshed on each SSO login |

### New API endpoints (total: 15)

- `GET /auth/oauth/providers` — list enabled providers
- `GET /auth/oauth/{provider}/authorize?code_challenge=<hash>` — get consent URL + state
- `POST /auth/oauth/callback` — exchange code → JWT pair

### Upsert logic (`get_or_create_by_oauth`)

1. Match on `(oauth_sub, oauth_provider)` — returning user
2. Match on email — link provider to existing email account
3. No match — create new account, `role="general"`, sentinel `hashed_password`

### PKCE flow

- `code_verifier` lives only in `sessionStorage`; never sent to provider
- `code_challenge = base64url(SHA-256(verifier))` sent to `/authorize`
- Backend sends verifier to Google token endpoint; Google validates internally
- Facebook doesn't support PKCE — verifier unused in FB exchange

### SSO settings (in `backend/.env`)

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
FACEBOOK_APP_ID=...
FACEBOOK_APP_SECRET=...
OAUTH_REDIRECT_URI=http://localhost:3000/auth/oauth/callback
```

Google credentials are configured; Facebook credentials are placeholders.

### Run migration once per deployment

```bash
cd ai-agent-ui && source backend/demoenv/bin/activate
python auth/migrate_users_table.py
```

---

## Known Limitations / TODOs

- **Anthropic API not working** — currently on Groq as a workaround; switch back when resolved (see 2-line change in `agents/general_agent.py` and `agents/stock_agent.py` → `_build_llm()` above)
- **`SERPAPI_API_KEY` must be set** — `search_web` will return an error string without it; get key at serpapi.com (100 free searches/month)
- **Auth module fully deployed** — `backend/.env` contains `JWT_SECRET_KEY`; superuser `asequitytrading@gmail.com` seeded; all services start cleanly with `./run.sh start`

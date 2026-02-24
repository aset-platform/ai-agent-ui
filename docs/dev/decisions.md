# Decisions

A record of the architectural and tooling decisions made during development, and the reasoning behind each one.

---

## Backend Architecture

### OOP refactor — agents/ and tools/ packages

The original backend was two flat files: `main.py` and `agent.py`. `agent.py` defined the LLM, the tools, and the agentic loop all in one place.

The refactor extracted three distinct concerns:

- **`tools/`** — tool definitions and a registry for lookup and invocation.
- **`agents/`** — agent configuration, the agentic loop (in `BaseAgent`), and a registry for routing requests to the correct agent.
- **`main.py`** — wires the two registries together and owns the FastAPI application.

The benefit: adding a new tool or a new agent type requires no changes to any other layer. The registries are the only coupling point.

### ChatServer class in main.py

All server-level state — registries and the FastAPI app — lives inside a `ChatServer` instance rather than as module-level globals. HTTP route handlers are registered as bound methods (`self._chat_handler`), giving them access to the instance's registries without importing anything at module scope.

This makes it trivial to instantiate a second server in tests with a different configuration, and avoids the class of bugs where startup order matters for module-level globals.

### BaseAgent ABC with abstract _build_llm()

The agentic loop is identical across all agent types — convert history, invoke LLM, handle tool calls, repeat. The only thing that varies between agents is the LLM provider and model.

Putting the loop in `BaseAgent` and making `_build_llm()` abstract means:

- The loop logic is tested and maintained in one place.
- Switching from Groq to Claude requires changing two lines in one file (`general_agent.py`), not rewriting the loop.
- Adding a new agent with a different LLM takes ~10 lines of code.

### Switching back to Claude (not yet done)

The intended LLM is Claude Sonnet 4.6. Groq (`openai/gpt-oss-120b`) is a temporary substitute while Anthropic API access is being resolved. The two-line switch is documented in [How to Run](how-to-run.md) and in the `general_agent.py` source file.

---

## Tools

### SerpAPI over Google Custom Search API

SerpAPI was chosen because:

- One API key, no Google Cloud project or OAuth setup required.
- Already supported by `langchain-community` (`SerpAPIWrapper`).
- Free tier (100 searches/month) is sufficient for development.

The tradeoff is that SerpAPI is a paid third-party service, not a direct Google API. For production, a direct integration or a different provider might be preferable.

### search_web error handling with try/except

The original `search_web` tool had no error handling. If SerpAPI was unavailable (missing key, network error, quota exceeded), the exception would propagate up through the agentic loop, cause the HTTP handler to return a `500`, and give the user no useful information.

With try/except, failures become a `ToolMessage` string (`"Search failed: <reason>"`). The LLM receives this as a tool result and can respond gracefully — for example, by acknowledging the failure and answering from training data instead.

---

## Configuration

### Pydantic Settings with lru_cache

Environment variables are validated once at startup through a `Settings(BaseSettings)` model. Benefits:

- Type coercion is automatic (e.g. `LOG_TO_FILE=true` becomes `bool`).
- Missing required fields would raise a clear error at startup, not deep inside a handler.
- `@lru_cache` on `get_settings()` ensures the environment is parsed exactly once.

An optional `.env` file in `backend/` is supported (lower priority than real env vars). This avoids having to `export` variables on every shell session during development.

---

## Logging

### Structured logging over print()

The original code used `print()` for debug output. The refactored backend uses Python's `logging` module throughout, with loggers named after their module (`logging.getLogger(__name__)`).

Benefits over `print()`:

- Log level filtering — set `LOG_LEVEL=WARNING` to suppress debug noise in staging.
- Structured format — timestamp, level, and logger name on every line.
- Consistent output — all modules use the same format automatically.
- File output — rotating file handler captures everything without changing code.

### TimedRotatingFileHandler — daily rotation, 7-day retention

Log files rotate at midnight. The previous 7 days are kept and older files are deleted automatically. This bounds disk usage without requiring a separate log rotation daemon (like `logrotate`).

The `logs/` directory is gitignored so log files are never accidentally committed.

### Clearing handlers on uvicorn hot-reload

uvicorn's `--reload` flag re-imports the application module on file changes. Each import would normally add a new set of handlers to the root logger (doubling, tripling, etc. the output). `setup_logging()` clears all existing handlers before adding new ones:

```python
root_logger.handlers.clear()
```

This is safe because `setup_logging()` always re-adds the correct handlers immediately after clearing.

---

## Python 3.9 Compatibility

The `demoenv` virtualenv runs Python 3.9.13. Two 3.10+ features are avoided:

- **Union type syntax** (`X | Y`, PEP 604) — replaced with `Optional[X]` from `typing`.
- No use of `match` statements (PEP 634).

The codebase does use `list[dict]` and `dict[str, T]` as type hints directly on class attributes. These are valid in Python 3.9 for annotations used at runtime in some contexts, but can cause `TypeError` at runtime if evaluated eagerly. Adding `from __future__ import annotations` to files that use these annotations would make them string-based (deferred evaluation) and fully compatible with 3.9.

---

### Agent-to-agent tool via factory function

The stock agent needs access to live web search, but `search_web` requires SerpAPI and is already owned by the general agent. Rather than registering `search_web` directly on the stock agent (which would duplicate the tool coupling), a factory function `create_search_market_news_tool(general_agent)` in `tools/agent_tool.py` wraps the general agent's `run()` method as a `@tool`. The stock agent calls this tool, which in turn triggers the general agent's full agentic loop (including `search_web`) and returns the result as a string.

The key constraint this solves: the factory must run **after** the general agent is created but **before** the stock agent is instantiated (so the tool is in the registry when `BaseAgent._setup()` fetches tool names). This ordering is enforced in `ChatServer._register_agents()`.

### Same-day text cache for analyse_stock_price and forecast_stock

Running the full analysis pipeline (technical indicators + Prophet training) takes 30–90 seconds. A user who asks about AAPL twice in the same day should not wait twice. Both tools now check for a dated text file in `data/cache/` before running. If a file matching `{TICKER}_{key}_{date.today()}.txt` exists, it is returned immediately. On the first run, the result is saved to that file. The cache is keyed by date so it automatically expires at midnight with no cron job required. `data/cache/` is gitignored.

---

## Frontend

### SPA navigation via view state + iframes

The original menu opened Docs and Dashboard in new browser tabs. The replacement uses a `View` state (`"chat" | "docs" | "dashboard"`) and renders the non-chat surfaces as full-height `<iframe>` elements.

Why iframes rather than full Next.js pages:

- The Dashboard (Dash) and Docs (MkDocs) are independent Python processes. Embedding them as iframes is the only way to show them inside the Next.js shell without rewriting them as React components.
- Iframes preserve the services' full interactivity and navigation (Dash callbacks, MkDocs search, etc.) without any duplication of their UI logic.
- The component stays mounted when switching views, so `histories`, `input`, and all other React state is preserved across navigations.

`iframeUrl` is stored separately from `view` so that:

- Clicking a link like "View AAPL Analysis →" opens the exact page (`/analysis?ticker=AAPL`).
- Clicking "Dashboard" in the menu always opens the dashboard homepage (by resetting `iframeUrl` to `null`).

### Internal link routing through onInternalLink callback

`preprocessContent()` replaces absolute chart file paths with markdown links pointing to the dashboard service URL. If these links were rendered as plain `<a>` elements they would open in a new tab, defeating the SPA design.

The `MarkdownContent` component accepts an `onInternalLink(href)` prop. The custom `a` renderer inspects every `href`: if it starts with `NEXT_PUBLIC_DASHBOARD_URL` or `NEXT_PUBLIC_DOCS_URL`, it renders a `<button onClick={() => onInternalLink(href)}>` instead. External links (news articles, Wikipedia, etc.) still use `<a target="_blank">`.

This keeps external link behaviour unchanged while routing all internal navigation through the view state.

### Frontend environment variables in .env.local

Three `NEXT_PUBLIC_*` variables replace hard-coded localhost URLs:

- `NEXT_PUBLIC_BACKEND_URL` — used in `sendMessage()` for the POST.
- `NEXT_PUBLIC_DASHBOARD_URL` — used in `preprocessContent()` and `handleInternalLink()`.
- `NEXT_PUBLIC_DOCS_URL` — used in `handleInternalLink()` and the menu.

`frontend/.env.local` is gitignored. `frontend/.env.local.example` is committed as a reference. `frontend/.gitignore` has a `!.env.local.example` negation so the example file bypasses the `.env*` rule.

### Agentic loop iteration cap (MAX_ITERATIONS = 15)

Without a guard, a misbehaving LLM could call tools indefinitely. `MAX_ITERATIONS = 15` is set as a module-level constant in `backend/agents/base.py`. The guard fires at the top of the `while True:` loop (after incrementing the counter but before the next LLM call), logs a `WARNING`, and breaks. The last available response is returned. 15 iterations is well above any legitimate tool chain observed in practice.

### Session persistence via localStorage

Chat history previously lived only in React state and was lost on page refresh. Two `useEffect` hooks now persist it:

1. **Load on mount** — reads `"chat_histories"` from `localStorage`, revives `Date` objects with `new Date(m.timestamp)` (needed because `JSON.stringify` serialises `Date` as ISO strings).
2. **Save on change** — writes `histories` on every state update.

The clear button calls `setMessages([])`, which triggers the save effect automatically — no explicit `localStorage.removeItem()` needed. Other agents' histories are preserved when clearing one agent.

### Single-file component (page.tsx)

The entire chat UI — state, handlers, and rendering — lives in one file. For a single-page app with one feature, this is appropriate. The overhead of splitting into multiple components and files would add complexity without benefit at this scale.

### Per-agent chat history (histories record instead of single messages array)

The original frontend kept a single `messages: Message[]` array. Switching between agents cleared it, which was confusing for users moving back and forth between General and Stock Analysis. The state was replaced with `histories: Record<string, Message[]>` keyed by `agentId`. A derived `messages` variable and a scoped `setMessages` helper ensure all existing message-manipulation code continues to work unchanged — the helper writes only to `histories[agentId]`. React state is still the only persistence mechanism, so histories are lost on page refresh.

### Local state only (no Redux, Context, or Zustand)

Three `useState` hooks cover everything the UI needs. There is no shared state between components (there is only one component), so a global state library would be pure overhead.

### Full history sent on every request

The backend is stateless. The frontend sends the complete conversation history with every `POST /chat` request. This is simple and correct — the LLM always has full context.

The tradeoff is that very long conversations send proportionally larger payloads. For a development/demo app this is fine. A production system would likely need server-side session storage.

### Native fetch over axios

The original send path used `axios`. The streaming implementation requires consuming a `ReadableStream` from the response body, which the native `fetch` API provides directly. `axios` does not expose the response body as a `ReadableStream` in the browser without extra wrappers. Since `fetch` covers all requirements (JSON payloads, error handling via `res.ok`, streaming), `axios` was removed from the send path.

### NDJSON over SSE for streaming

Server-Sent Events (SSE) and WebSockets were considered for live streaming. NDJSON over plain HTTP was chosen because:

- No special client library needed — `fetch()` + `ReadableStream` is standard browser API.
- FastAPI's `StreamingResponse` serves it natively.
- The format is trivially debuggable with `curl -N`.
- Token-by-token LLM streaming is out of scope; only tool-loop status events are streamed, so the lower overhead of SSE is not needed.

The tradeoff is that NDJSON is not automatically reconnected if the connection drops. For a local development tool this is acceptable.

### Request timeout: asyncio.wait_for for sync, queue.Empty for stream

Two different timeout mechanisms are used:

- **`POST /chat`** (sync) — `asyncio.wait_for(loop.run_in_executor(...), timeout=N)` wraps the blocking `agent.run()` call. On timeout, `asyncio.TimeoutError` is caught and HTTP 504 is returned.
- **`POST /chat/stream`** — the generator runs in a daemon thread. Events pass through `queue.Queue.get(timeout=1.0)`. The loop checks elapsed time each iteration; when `time.time() - start >= timeout` it emits a `timeout` event and breaks. No asyncio primitives are needed since the response is already a `StreamingResponse`.

### Dashboard light theme (FLATLY) matching the chat interface

The original DARKLY theme created a sharp visual contrast when embedding the dashboard in the SPA iframe next to the chat's white UI. Switching to FLATLY with a custom CSS variable palette (`--bg: #f9fafb`, `--card-bg: #ffffff`, `--accent: #4f46e5`) eliminates this contrast and reuses the same indigo accent color from the chat's Tailwind config. FLATLY was chosen (over LITERA, YETI, etc.) because its flat, minimal card style matches the Tailwind aesthetic most closely.

### X-Frame-Options: ALLOWALL on Flask after_request

By default Flask/Werkzeug may send `X-Frame-Options: SAMEORIGIN`, and some browsers will block iframes from different origins regardless. Adding `ALLOWALL` and the `Content-Security-Policy: frame-ancestors *` header as a Flask `@server.after_request` hook ensures the Dash app can be embedded in any origin without browser security errors. Both headers are needed for full browser compatibility (older browsers respect `X-Frame-Options`; modern browsers prefer the CSP directive).

---

## Dashboard

### Direct parquet reads instead of HTTP

The dashboard reads OHLCV data and forecasts directly from local parquet files rather than going through the FastAPI backend. This means:

- The dashboard starts in under a second with no network dependency.
- It can run fully offline after an initial data fetch.
- The FastAPI server does not need to be running.

The tradeoff is that "Run New Analysis" must import backend tool modules directly (via `sys.path` insertion in `app.py`) instead of calling an API endpoint. For a local development tool this is acceptable; a production deployment would expose a dedicated endpoint.

### register_callbacks factory pattern

All Dash callbacks are defined inside a `register_callbacks(app)` function in `callbacks.py` rather than at module scope. This avoids the circular import that would occur if `callbacks.py` imported `app` from `app.py` while `app.py` imported from `callbacks.py`. The factory receives the `app` instance as an argument.

### dcc.Store for cross-page ticker propagation

When a user clicks a stock card on the Home page, the `navigate_to_analysis` callback writes the ticker to a `dcc.Store` (`nav-ticker-store`) and updates the URL pathname. The `sync_analysis_ticker` and `sync_forecast_ticker` callbacks on the destination pages read from this store to pre-select the correct ticker in the dropdown. This avoids URL query-string parsing as the primary mechanism (though the `?ticker=AAPL` query param is also supported for direct linking).

### suppress_callback_exceptions=True

The Analysis, Forecast, and Compare page components (including their dropdowns and charts) are rendered only when the user navigates to those pages. Dash raises errors at startup if a callback references a component ID that is not yet in the layout. `suppress_callback_exceptions=True` silences these errors and defers validation to runtime, which is the standard approach for multi-page Dash apps.

### allow_duplicate=True on forecast-accuracy-row

Two callbacks write to `forecast-accuracy-row.children`:

1. `update_forecast_chart` — fires whenever the ticker or horizon changes; writes a placeholder note ("Accuracy metrics appear after clicking Run New Analysis").
2. `run_new_analysis` — fires when the button is clicked; writes the real MAE / RMSE / MAPE cards.

Dash 2+ requires `allow_duplicate=True` on any output that appears in more than one callback. This is set on the `run_new_analysis` output since `update_forecast_chart` is the primary writer.

---

## Version Control

### Virtualenv excluded from git

`backend/demoenv/` is listed in `.gitignore` as both `demoenv/` and `*env/`. The virtualenv is reconstructable from `requirements.txt`.

### frontend/.git removed

The `frontend/` directory was created with a separate git repo (`.git` subdirectory), which caused the root repo to treat it as a git submodule. The nested `.git` was removed so the frontend is tracked as regular files in the root repo.

### requirements.txt is frozen

`requirements.txt` is populated with `pip freeze` output, pinning exact versions for all direct and transitive dependencies. This ensures reproducible installs. Update it whenever new packages are installed:

```bash
pip freeze > backend/requirements.txt
```

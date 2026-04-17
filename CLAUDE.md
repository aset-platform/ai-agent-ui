# CLAUDE.md — AI Agent UI

> Slim project instructions for Claude Code. Detailed knowledge
> lives in Serena shared memories — run `list_memories` to browse.

---

## Session Startup (do these on every new session)

1. **Activate Serena** — `activate_project ai-agent-ui`
   (required before `read_memory` / `write_memory` / `list_memories`)
2. **Check Ollama** — SessionStart hook reports model status;
   run `ollama-profile coding` if delegation needed
3. **Superpowers skills** — always check for applicable skill
   before starting work (brainstorming, TDD, executing-plans)
4. **SuperClaude commands** — use `/sc:` prefix for git, build,
   test, analyze, implement, troubleshoot workflows

### Available MCP tools (verify with `list_memories`)

| Server | Purpose | Activation |
|--------|---------|------------|
| Serena | Code analysis, shared memories, symbol navigation | `activate_project` |
| Ollama | Local LLM delegation (Qwen for code gen) | auto (SessionStart hook) |
| Context7 | Library/framework docs lookup | auto |
| Playwright | Browser automation, E2E testing | auto |
| Chrome DevTools | Page inspection, performance, screenshots | auto |
| Atlassian (Jira) | Sprint/ticket management | auto |
| Sequential Thinking | Multi-step reasoning | auto |

---

## Project Overview

Fullstack agentic chat app with stock analysis and Prophet forecasting.
Volatility-regime adaptive forecasts with confidence scoring
(High/Medium/Low badges). FinBERT batch sentiment + XGBoost ensemble.
Native portfolio dashboard with TradingView lightweight-charts +
react-plotly.js. Memory-augmented chat with pgvector semantic
retrieval. Dual payment gateways (Razorpay INR + Stripe USD).
All pages fully migrated from Dash to Next.js.

| Service | Port | Entry point | Stack |
|---------|------|-------------|-------|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x, SQLAlchemy 2.0 async |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19, lightweight-charts |
| PostgreSQL | 5432 | Docker | pgvector/pgvector:pg16 (OLTP: 18 tables + pgvector) |
| Redis | 6379 | Docker | Redis 7 Alpine |
| Docs | 8000 | Docker | MkDocs Material 9 (squidfunk) |
| Alembic | — | `backend/db/migrations/` | Schema migrations for PostgreSQL |

```bash
# run.sh — Docker Compose wrapper (preferred)
./run.sh start                              # all services via docker compose
./run.sh stop                               # docker compose down
./run.sh restart                            # stop + start all
./run.sh restart frontend                   # restart only frontend
./run.sh restart backend                    # restart only backend
./run.sh stop redis                         # stop only redis
./run.sh rebuild frontend                   # rebuild image + restart (after code changes)
./run.sh rebuild backend                    # rebuild image + restart
./run.sh build                              # build all images (no restart)
./run.sh status                             # service health table
./run.sh logs backend                       # Docker service logs
./run.sh logs backend -f                    # follow Docker logs
./run.sh logs --errors                      # errors across all services
./run.sh doctor                             # diagnostic checks

# Direct Docker Compose (also works)
docker compose up -d                        # all services
docker compose build backend               # rebuild after requirements.txt changes
docker compose ps                           # health check
docker compose down                         # stop all

# Ollama (host-native, not containerized)
ollama-profile coding                       # load Qwen for code gen
ollama-profile reasoning                    # load GPT-OSS 20B
ollama-profile embedding                    # load nomic-embed-text (memory vectors)
ollama-profile status                       # check loaded model
```

**Key dirs**: `backend/` (agents, tools, config), `backend/pipeline/` (stock data pipeline, 19 CLI commands), `backend/jobs/` (scheduler executors, pipeline chaining, gap filler), `backend/db/` (ORM models, async engine, Alembic migrations, DuckDB layer), `backend/tools/` (forecast: `_forecast_regime.py`, `_forecast_features.py`, `_forecast_model.py`, `_forecast_ensemble.py`; sentiment: `_sentiment_finbert.py`, `_sentiment_scorer.py`), `auth/` (JWT + RBAC + OAuth PKCE), `stocks/` (Iceberg — 14 OLAP tables), `frontend/` (SPA), `e2e/` (Playwright — 257 tests, 51 specs), `hooks/` (pre-commit, pre-push).

**Docker files**: `Dockerfile.backend`, `Dockerfile.frontend`,
`Dockerfile.docs`, `docker-compose.yml`,
`docker-compose.override.yml` (dev hot-reload),
`.env.example` (template), `.env` (secrets, gitignored).

**Config**: `pyproject.toml` + `.flake8` (79 chars), `frontend/eslint.config.mjs`.

**Data**: `~/.ai-agent-ui/` (override: `AI_AGENT_UI_HOME`). Paths in `backend/paths.py`.

---

## LLM Cascade Architecture

`FallbackLLM` in `backend/llm_fallback.py` — N-tier cascade:

| Tier | Provider | Model | When |
|------|----------|-------|------|
| 1-5 | Groq (free) | Round-robin pools: [70b, qwen3-32b] → [gpt-oss-120b, gpt-oss-20b] → scout-17b | All (chat + batch) |
| N-1 | Ollama (local) | gpt-oss:20b | Fallback (`ollama_first=False` everywhere) |
| N | Anthropic (paid) | claude-sonnet-4-6 | Final fallback |

- `RoundRobinPool` (`backend/token_budget.py`): per-pool atomic
  counter, `get_token_budget()` singleton seeded from Iceberg.
  `ROUND_ROBIN_ENABLED=false` reverts to legacy sequential.
  Per-request model pinning via `_pinned_model` — see LLM &
  Chat gotchas for details.
- `OllamaManager` (`backend/ollama_manager.py`): TTL-cached health
  probe, load/unload profiles. If unavailable, cascade skips.
- Admin API: `GET/POST /v1/admin/ollama/{status,load,unload}`
- `ollama-profile` CLI: `coding`, `reasoning`, `embedding`, `unload`
- Observability: `provider="ollama"` in `ObservabilityCollector`

---

## Stock Data Pipeline

- **Module:** `backend/pipeline/` — 19 CLI commands via
  `PYTHONPATH=.:backend python -m backend.pipeline.runner`
- **Source strategy:** yfinance primary (bulk/daily),
  jugaad-data fallback (retry/correct), racing (chat).
- **Ticker format:** ALL Indian stocks use `.NS` suffix
  everywhere (Iceberg, PG, frontend, scheduler). Never
  store canonical format (no suffix) for data operations.
- **Market detection:** Import `detect_market` from
  `backend/market_utils.py`. NEVER add local suffix checks.
- **Docs:** `docs/backend/stock-pipeline.md` (usage guide),
  spec at `docs/superpowers/specs/2026-04-02-stock-pipeline-design.md`.

---

## Hybrid DB Architecture

OLTP/OLAP split — PostgreSQL for row-level CRUD, Iceberg for
append-only analytics.

### PostgreSQL tables (SQLAlchemy 2.0 async ORM)

| Table | Module | Pattern |
|-------|--------|---------|
| `auth.users` | `backend/db/models/user.py` | CRUD via `UserRepository` |
| `auth.user_tickers` | `backend/db/models/user_ticker.py` | Insert + delete |
| `auth.payment_transactions` | `backend/db/models/payment.py` | Insert + update |
| `stocks.registry` | `backend/db/pg_stocks.py` | Upsert (has `ticker_type`: stock/etf/index/commodity) |
| `stocks.scheduled_jobs` | `backend/db/pg_stocks.py` | Upsert (has `force` column) |
| `stocks.scheduler_runs` | `backend/db/pg_stocks.py` | Insert + row-level UPDATE |
| `stocks.recommendation_runs` | `backend/db/models/recommendation.py` | Smart Funnel run metadata |
| `stocks.recommendations` | `backend/db/models/recommendation.py` | Individual recs with data_signals JSONB |
| `stocks.recommendation_outcomes` | `backend/db/models/recommendation.py` | 30/60/90d outcome checkpoints |
| `stocks.market_indices` | `backend/db/models/market_index.py` | Single-row ticker cache (Nifty+Sensex) |
| `public.user_memories` | `backend/db/models/memory.py` | pgvector semantic memory (768-dim) |
| `public.conversation_contexts` | `backend/db/models/conversation_context.py` | Chat context persistence (cross-session) |
| `stock_master` | `backend/db/models/stock_master.py` | Pipeline universe (symbol, yf_ticker, ISIN) |
| `stock_tags` | `backend/db/models/stock_tag.py` | Temporal tagging (nifty50/100/500) |
| `ingestion_cursor` | `backend/db/models/ingestion_cursor.py` | Keyset pagination cursor |
| `ingestion_skipped` | `backend/db/models/ingestion_skipped.py` | Failed ticker log + retry |
| `pipelines` | `backend/db/models/pipeline.py` | Pipeline chain definitions |
| `pipeline_steps` | `backend/db/models/pipeline.py` | Ordered steps within pipelines |

### Iceberg tables (14 — append / scoped-delete, 27 cols in forecast_runs)

`audit_log`, `usage_history`, `company_info`, `dividends`, `ohlcv`,
`analysis_summary`, `forecast_runs`, `forecasts`, `quarterly_results`,
`llm_pricing`, `llm_usage`, `portfolio_transactions`,
`piotroski_scores`, `sentiment_scores` (stocks ns)
Note: `technical_indicators` exists but is empty/unused — indicators
computed on-the-fly from OHLCV in `_analysis_shared.py`.
Note: `scheduler_runs` and `scheduled_jobs` migrated to PG
(ASETPLTFRM-301).

### Key components

- `backend/db/engine.py` — async `session_factory` (asyncpg driver,
  `pool_pre_ping=True`)
- `backend/db/models/` — 18 SQLAlchemy ORM models (FK cascade,
  JSONB, composite PK, indexes, pgvector)
- `backend/db/migrations/` — Alembic async migrations
- `auth/repo/repository.py` — `UserRepository` facade
  (session_factory injection, per-call sessions)
- `backend/db/pg_stocks.py` — registry + scheduler + pipeline PG functions
- `backend/db/models/pipeline.py` — Pipeline + PipelineStep ORM
- `backend/jobs/pipeline_executor.py` — sequential chain execution
  with skip-on-failure and resume-from-step
- `backend/db/duckdb_engine.py` — DuckDB query engine (primary
  Iceberg read path). Has in-memory metadata cache; call
  `invalidate_metadata()` after writes (auto-wired in repo).
- `scripts/migrate_iceberg_to_pg.py` — one-time data migration

---

## Hard Rules (NON-NEGOTIABLE)

These rules MUST be followed in every interaction.

### Performance (think throughput-first)

1. **Batch reads, not per-ticker loops** — single DuckDB
   `SELECT ... WHERE ticker IN (...)` before parallel loops.
   Pre-load into dict. Never N individual Iceberg reads.
2. **Bulk writes** — accumulate results in memory, write in
   1-2 Iceberg commits after the loop. Never per-ticker
   `_append_rows` inside workers.
3. **Iceberg = append-only analytics only** — row-level
   `update` does full table scan + overwrite (~9s). Use
   PostgreSQL for mutable state (status, counters, timestamps).
4. **NullPool for sync→async PG** — `_pg_session()` uses
   `NullPool`. Never thread-local or per-call pooled engines.
   See `shared/debugging/pg-nullpool-sync-async-bridge`.
5. **No nested parallelism** — outer `ThreadPoolExecutor`
   workers must NOT spawn inner `ProcessPoolExecutor`.
   Prophet CV: `parallel=None`. `workers = cpu_count // 2`.
6. **Cache scope-level data** — VIX, index, macro regressors
   identical across tickers in same scope. Cache with TTL.
   Only per-ticker data (sentiment) fetched individually.
7. **Throttle expensive I/O** — if an update costs >100ms,
   batch into finalize or use time-based intervals.
8. **No OHLCV full-table scans** — 1.4M rows. Use
   `ROW_NUMBER() OVER (PARTITION BY ticker ...)` or
   `WHERE ticker IN (...)` with date filters.

### Code Style

9. **Line length 79 chars** — black, isort, flake8 aligned.
10. **No bare `print()`** — use `logging.getLogger(__name__)`.
11. **`X | None`** not `Optional[X]` (Python 3.12, PEP 604).
12. **No module-level mutable globals** — all state in class
    instances. Exception: `_logger` is OK.
13. **No bare `except:`** — always `except Exception` or
    specific type.
14. **`apiFetch`** not `fetch` — auto-refreshes JWT.
15. **`<Image />`** not `<img>` — enforced by ESLint.
16. **Patch at SOURCE module** — not the importing module.

### Data & Writes

17. **Iceberg writes MUST NOT be silenced** — let errors
    propagate. Never swallow append/overwrite exceptions.
18. **Scoped deletes** — delete by `In("ticker", batch)` not
    `EqualTo("score_date")`. Prevents cross-market overwrites.
19. **Ticker format** — ALL Indian stocks `.NS` suffix
    everywhere. Import `detect_market` from `market_utils.py`.
20. **NEVER delete Iceberg metadata/parquet files directly**
    — use `overwrite()` or `delete_rows()` API only. Direct
    deletion breaks the SQLite catalog. Backup before any
    maintenance operation.

### Process & Git

21. **Branch off `dev`** — NEVER push to `dev`, `qa`,
    `release`, `main`.
22. **Co-Authored-By in commits** — always use:
    `Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>`
23. **Update `PROGRESS.md`** after every session (dated entry).
24. **Commit Serena memories** — always `git add .serena/`
    before final push. Shared memories are checked into git.
25. **Test-after-feature** — happy path + 1 error path minimum.
26. **Jira story points** — update `customfield_10016`
    (estimate). `customfield_10036` not settable via API.

### Infra & Config

27. **`NEXT_PUBLIC_BACKEND_URL` = `http://localhost:8181`** —
    never `127.0.0.1`. Hostname mismatch breaks cookies.
28. **No `@traceable` on `FallbackLLM.invoke()`** — breaks
    LangChain tool call parsing.
29. **Ollama for experiments only** — host-native, not
    containerized. Cascade falls back to Groq/Anthropic.
30. **LHCI can't audit authenticated routes** — use
    `npm run perf:full` (Playwright) instead.

---

## Serena Shared Memories

Run `list_memories` to browse all topics. Key categories:
`shared/architecture/`, `shared/conventions/`,
`shared/debugging/`, `shared/onboarding/`.

---

## Gotchas (learned the hard way)

### Data & Pipeline

- **yfinance pre-market flat candles**: Fetching before
  settlement returns O=H=L with NaN close. Pipelines at
  08:00 IST. Must delete NaN rows and re-fetch.
- **Forecast backtest convention**: `horizon_months=0` in
  `forecasts` table. Actual price in `lower_bound`. Batch
  executor must persist when CV runs.
- **Prophet CV from stdin**: `parallel="processes"` fails
  with `FileNotFoundError: /app/<stdin>`. Use `parallel=None`.
- **DuckDB metadata cache**: `invalidate_metadata()` called
  in `_retry_commit()`. If stale reads after write, check
  invalidation is wired.
- **Iceberg `company_info` is upsert**: deletes existing
  row for ticker before appending. One row per ticker.
- **Iceberg concurrent writes**: SQLite catalog conflicts
  under Semaphore(10). Fundamentals job uses Semaphore(1).
- **Iceberg flush window**: `ObservabilityCollector` flushes
  every 30s. Restarts lose unflushed events. Seed on startup.
- **yfinance sector names**: "Technology" not "IT",
  "Financial Services" not "Financials".
- **jugaad-data timeout**: No built-in timeout. NseSource
  wraps in `asyncio.wait_for(timeout=60.0)`.
- **Forecast regime classification**: Tickers classified by annualized
  volatility into stable (<30%), moderate (30-60%), volatile (>60%).
  Each regime gets different Prophet config (growth, cps, log-transform).
- **Log-transform**: Applied for moderate/volatile regimes. Guarantees
  non-negative predictions. `np.log(y)` before fit, `np.exp(yhat)` after.
- **Technical bias**: RSI/MACD/volume signals dampen forecast by up to
  ±15%, tapering over 30 days. Does NOT change model — post-processing.
- **Confidence score**: 5-component weighted score (direction, MASE,
  coverage, interval, data completeness). <0.25 = rejected (hidden).
- **Sector indices**: 10 sector index tickers (5 India, 5 US)
  in pipeline for bulk-download. `sector_relative_strength`
  dropped from Prophet (|beta| < 0.001) but available for
  future use.
- **FinBERT sentiment**: `sentiment_scorer=finbert` in config
  routes batch scoring to ProsusAI/finbert (CPU, zero API
  cost). LLM cascade kept for chat.
  `refresh_ticker_sentiment()` has idempotent check — won't
  re-score if today's data exists even on forced runs.
- **XGBoost indicator casing**: `compute_indicators()` returns
  Title-case (RSI_14) but `_FEATURES` expects lowercase
  (rsi_14). Fix: `tech.columns = [c.lower()]` after load.
  Without this, all 5 technical indicators silently dropped.
- **Forecast run dedup**: Multiple runs on same `run_date` —
  use `computed_at` (exact UTC timestamp) for dedup, not
  `run_date`. Affects `get_dashboard_forecast_runs()` and
  `get_latest_forecast_run()`.
- **NeuralProphet incompatible**: pandas 3.0 breaks
  `Series.view()` and `groupby().apply()` in NP 0.8.0–
  1.0.0rc10. Project stale since June 2024. Do NOT attempt
  integration until pandas 3.0 support ships.
- **Portfolio period overlap**: `_parse_period()` computes
  both periods from today. For comparison ("2W vs 1W"),
  `get_portfolio_comparison` uses `_period_to_days()` to
  build non-overlapping windows: period2=recent ending today,
  period1=preceding ending where period2 starts.
- **Portfolio bfill**: `_compute_daily_portfolio()` uses
  `ffill().bfill()` on ticker DataFrame. Without `bfill()`,
  first rows with partial tickers inflate returns to 4000%+.

### Database & PG

- **`_pg_session()` NullPool**: ~2-5ms per call. Don't use
  in hot loops — batch via DuckDB or bulk PG writes.
- **asyncpg `pool_pre_ping=True`**: Required in
  `engine.py` — stale connections crash on uvicorn reload.
- **Iceberg NaT/NaN → PG**: Sanitize before PG insert —
  PG rejects NaT timestamps and NaN floats.
- **Sync→async PG bridges**: See memory
  `shared/debugging/pg-nullpool-sync-async-bridge`.
- **UserMemory `extend_existing`**: Dual import causes
  "Table already defined". Fix: `extend_existing=True`.
- **FastAPI Query default**: Pass `Query()` params
  explicitly in internal calls (`ticker=None`).

### Docker & Infra

- **Docker health check**: `/v1/health` not `/health`.
- **Docker Iceberg mount**: Mount `~/.ai-agent-ui` at SAME
  path inside container. SQLite catalog stores absolute paths.
- **Ollama in Docker**: Use `host.docker.internal:11434`.
- **`.pyiceberg.yaml` in Docker**: Mount at
  `/app/.pyiceberg.yaml:ro`. Without it, reads fail silently.
- **Frontend Docker**: `node:22-slim` (glibc required).
  `HOSTNAME=0.0.0.0` to bind all interfaces.
- **Docker seed script**: Set `PYICEBERG_CATALOG__LOCAL__URI`
  before pyiceberg import. Mount `fixtures/` volume.
- **Redis cache**: After code changes: `redis-cli FLUSHALL`.
- **Retention API blocking**: `RetentionManager.run_cleanup()`
  is synchronous Iceberg I/O. Must wrap in
  `asyncio.to_thread()` when called from async route
  handlers, otherwise blocks uvicorn event loop.
- **Data health fix-ohlcv blocking**: Both `backfill_nan`
  and `backfill_missing` actions are sync Iceberg I/O.
  Wrapped in `asyncio.to_thread()` in `routes.py`.

### Iceberg Maintenance (CRITICAL)

- **NEVER delete Iceberg metadata files** — the SQLite
  catalog (`~/.ai-agent-ui/data/iceberg/catalog.db`)
  stores absolute paths to `.metadata.json` files.
  Deleting them breaks `load_table()` with
  `FileNotFoundError`. If this happens, fix with:
  ```sql
  sqlite3 ~/.ai-agent-ui/data/iceberg/catalog.db
  UPDATE iceberg_tables
  SET metadata_location='file:///path/to/latest.metadata.json'
  WHERE table_name='ohlcv';
  ```
- **NEVER delete parquet data files directly** — use
  Iceberg `overwrite()` or `delete_rows()` API only.
  Direct file deletion causes `IOException` on reads.
  Orphan cleanup must only remove empty directories.
- **Compaction**: Use `overwrite()` (read all via DuckDB
  → write back as single batch). Produces 1 file per
  partition instead of 27. OHLCV went from 8,670 files
  to 817, reads from ~9s to 0.24s.
- **Backup before maintenance**: Always `run_backup()`
  before compaction or retention purge. Backup includes
  BOTH the warehouse dir AND `catalog.db`. Location:
  `/Users/abhay/Documents/projects/ai-agent-ui-backups/`
  (2 latest rotated).
- **OHLCV freshness gate**: Uses `latest >= today` (not
  `yesterday`). A ticker is "fresh" only if it has
  today's candle. Evening runs re-fetch closing data.
- **OHLCV upsert**: Append-only dedup skips rows where
  `(ticker, date)` exists. For today's data, a scoped
  delete + re-append ensures intraday flat candles are
  replaced by closing data.
- **Post-pipeline expiry**: `pipeline_executor.py` calls
  `expire_snapshots()` after successful pipeline runs.
  Currently logs snapshot count (safe no-op) since
  PyIceberg's snapshot removal API is fragile.
- **Maintenance module**: `backend/maintenance/` —
  `backup.py` (rsync + rotate), `iceberg_maintenance.py`
  (compact, expire, retain, orchestrate).
- **torch CPU-only in Docker**: Install via
  `pip install torch --index-url .../whl/cpu`. Do NOT add
  torch to requirements.txt (needs special index URL).
  Add `transformers>=4.40` to requirements.txt.

### LLM & Chat

- **TokenBudget**: Use `reserve()`/`release()` (atomic),
  not `can_afford()`/`record()` (TOCTOU race).
- **Hallucination guardrail**: `_is_hallucinated()` rejects
  3+ stock patterns with zero tool_done events.
- **Chat clarification**: Question ending with `?` bypasses
  keyword gate. See `_is_clarification()` in `guardrail.py`.
- **ReAct iteration**: `sub_agents.py` MUST pass
  `iteration=iteration+1`.
- **Groq tool call IDs**: `_sanitize_tool_ids()` cleans
  before Anthropic fallback.
- **`bind_tools` model_lookup**: Must rebuild after
  `FallbackLLM.bind_tools()`.
- **Groq TPD limits**: 5 models ~2.0M combined TPD.
  `TokenBudget` seeds from Iceberg on restart.
- **Model pinning**: `_pinned_model` in `FallbackLLM` locks
  model after first invoke per request. If pinned model hits
  budget, compresses first, then unpins and cascades. Call
  `pin_reset()` before each new ReAct loop.
- **Double-synthesis**: Sub-agent synthesis + graph-level
  synthesis caused hallucinations. Portfolio uses
  `skip_synthesis=True` — tools return formatted tables.
  Graph synthesis passthroughs responses >100 chars.

### Frontend

- **ChatInput `readOnly` not `disabled`**: Keeps focus.
- **Sidebar collapsed by default**: Hover flyout for submenus.
- **ECharts dark/light**: Use `MutationObserver` on `<html>`
  class, not `useTheme()`. Set `notMerge={true}` + `key`.
- **Perf script login**: Use `type()` not `fill()` — React
  `onChange` needs keystroke events.
- **`apiFetch` requires full URL**: Always use
  `${API_URL}/path` not relative `/path`. Relative paths
  hit Next.js (port 3000), not the backend (port 8181).
- **Market ticker**: `MarketTicker` in `AppHeader.tsx` center.
  NSE India + Yahoo Finance dual-source, 30s poll, PG+Redis.
  Off-hours: zero upstream calls (serves PG data).
- **ticker_type**: `stock_registry.ticker_type` classifies
  tickers: `stock` (755), `etf` (54), `index` (4),
  `commodity` (4). `_analyzable_tickers()` (stock+etf)
  for analytics/sentiment/forecast. `_has_financials()`
  (stock only) for Piotroski. Data health uses split
  totals per card.
- **ETF bulk-download**: `--tickers` flag expects symbols
  WITHOUT `.NS` suffix. Script auto-appends from
  `stock_master.yf_ticker`. Double-suffix = 404.
- **DuckDB stale reads**: Data health calls
  `invalidate_metadata()` before queries. Without it,
  fix results don't show until next container restart.
- **Confidence badge in `<p>`**: Use `<span>` not `<div>`
  for inline elements inside `<p>` tags. `<div>` inside
  `<p>` causes React hydration errors.

### Testing & Config (unit/integration)

- **Test mock dates**: Never hardcode — use
  `str(int(time.time()) - 86400)` for "yesterday".
- **`settings.local.json`**: No `()` in Bash patterns.
- **slowapi rate limiter**: `limiter.enabled = False` in
  test fixtures, not `limiter.reset()`.
- **`get_settings().debug`**: Use `getattr()` with fallback.
- **StockRepository**: Always use `_require_repo()`.
- **Superuser insights**: `_get_user_tickers()` shows all
  registry for superusers, watchlist-only for general.

### E2E Testing (Playwright)

**Config** (`e2e/playwright.config.ts`):

| Setting | Local | CI |
|---------|-------|----|
| Workers | 1 | 2 |
| Video | off | retain-on-failure |
| maxFailures | 10 | 0 |
| Retries | 1 | 2 |

**Projects** (run one at a time locally):

| Project | Auth | Tests |
|---------|------|-------|
| `frontend-chromium` | superuser | chat, billing, profile, dark-mode, navigation |
| `analytics-chromium` | general user | dashboard, insights, marketplace, portfolio-crud |
| `admin-chromium` | superuser | admin CRUD, observability, scheduler |

**Key conventions**:
- **Credentials**: `admin@demo.com` / `Admin123!`.
  Run `seed_demo_data.py` if login fails.
- **Chat panel**: Collapsible side panel on `/dashboard`.
  `ChatPage.goto()` clicks "Toggle chat panel" to open.
  All locators scoped to `data-testid="chat-panel"`.
- **Testid constants**: All in `e2e/utils/selectors.ts`
  (FE object). Page objects use `this.tid(FE.xxx)`.
  Never hardcode testid strings in tests.
- **Visual baselines**: Regenerate after UI changes with
  `npx playwright test --update-snapshots`. Baselines
  in `*.spec.ts-snapshots/` dirs, committed to git.

**Gotchas**:
- **Never use `networkidle`** — dashboard has continuous
  polling (30s) + WebSocket. Use explicit element waits:
  `page.getByTestId("sidebar").toBeVisible()`.
- **Below-fold widgets**: WatchlistWidget, Forecast, P&L
  need `waitFor({ state: "attached" })` then
  `scrollIntoViewIfNeeded()` before visibility asserts.
- **After `page.reload()`**: Chat panel closes. Wait for
  sidebar not chat input.
- **CSS uppercase vs DOM text**: `getByText("CURRENT
  PLAN")` fails when DOM has "Current Plan" with CSS
  `uppercase`. Use `getByTestId` or exact case.
- **Strict mode**: `/cancel|close/i` matches both Cancel
  button + Close X icon. Use `/^cancel$/i`.
- **Statement type options**: Quarterly tab uses `income`,
  `balance`, `cashflow` (not `balance_sheet`).
- **Never increase workers** — 3 workers consumed >1000%
  CPU and starved Docker services.

---

## Quick Reference

```bash
# Lint
black backend/ auth/ stocks/ scripts/
isort backend/ auth/ stocks/ scripts/ --profile black
flake8 backend/ auth/ stocks/ scripts/
cd frontend && npx eslint . --fix

# Test
python -m pytest tests/ -v        # all (~839 tests)
cd frontend && npx vitest run     # frontend (18 tests)
cd e2e && npm test                # E2E (~257 tests, needs live services)

# E2E (run one project at a time — 1 worker, ~3 min each)
cd e2e && npx playwright test --project=frontend-chromium    # auth, chat, billing, profile
cd e2e && npx playwright test --project=analytics-chromium   # dashboard, insights, marketplace
cd e2e && npx playwright test --project=admin-chromium       # admin CRUD, observability
cd e2e && npx playwright test --update-snapshots             # regenerate visual baselines

# Database migrations (PostgreSQL)
PYTHONPATH=. alembic upgrade head              # apply all migrations
PYTHONPATH=. alembic revision --autogenerate -m "desc"  # new migration
PYTHONPATH=backend python scripts/migrate_iceberg_to_pg.py  # one-time data migration

# Seed (required before first E2E run)
PYTHONPATH=backend python scripts/seed_demo_data.py
# Docker seed (when running via Docker Compose)
docker compose exec backend python scripts/seed_demo_data.py

# Stock Data Pipeline
PYTHONPATH=.:backend python -m backend.pipeline.runner download    # fetch Nifty 500 CSV
PYTHONPATH=.:backend python -m backend.pipeline.runner seed --csv data/universe/nifty500.csv
PYTHONPATH=.:backend python -m backend.pipeline.runner bulk-download  # yfinance batch OHLCV
PYTHONPATH=.:backend python -m backend.pipeline.runner fill-gaps   # patch company_info gaps
PYTHONPATH=.:backend python -m backend.pipeline.runner status      # check cursor progress
PYTHONPATH=.:backend python -m backend.pipeline.runner analytics --scope india   # compute analysis summary
PYTHONPATH=.:backend python -m backend.pipeline.runner sentiment --scope india   # LLM sentiment scoring
PYTHONPATH=.:backend python -m backend.pipeline.runner forecast --scope india    # Prophet forecasts
PYTHONPATH=.:backend python -m backend.pipeline.runner screen      # Piotroski F-Score
PYTHONPATH=.:backend python -m backend.pipeline.runner refresh --scope india --force  # full pipeline chain

# Performance (run from frontend/)
npm run perf:check                # LHCI on /login (pre-PR gate)
npm run perf:audit                # Playwright 10-route quick check
npm run perf:full                 # Full 42-point surface audit
npm run analyze                   # Bundle treemap (ANALYZE=true)
```

# CLAUDE.md — AI Agent UI

> Slim project instructions for Claude Code. Detailed rationale and
> historical context live in Serena shared memories — run
> `list_memories` to browse.

---

## Session Startup (every new session)

1. **Activate Serena** — `activate_project ai-agent-ui` (required
   before `read_memory` / `write_memory` / `list_memories`)
2. **Check Ollama** — SessionStart hook reports model status; run
   `ollama-profile coding` if delegation needed
3. **Superpowers skills** — check for applicable skill before work
   (brainstorming, TDD, executing-plans)
4. **SuperClaude commands** — `/sc:` prefix for git, build, test,
   analyze, implement, troubleshoot

### MCP tools

| Server | Purpose |
|--------|---------|
| Serena | Code analysis, shared memories, symbol nav |
| Ollama | Local LLM delegation (Qwen for code gen) |
| Context7 | Library/framework docs lookup |
| Playwright | Browser automation, E2E testing |
| Chrome DevTools | Page inspection, perf, screenshots |
| Atlassian (Jira) | Sprint/ticket management |
| Sequential Thinking | Multi-step reasoning |

---

## Project Overview

Fullstack agentic chat app with stock analysis and Prophet
forecasting. Volatility-regime adaptive forecasts with confidence
scoring (High/Medium/Low). FinBERT batch sentiment + XGBoost
ensemble. Native portfolio dashboard (lightweight-charts +
react-plotly). Memory-augmented chat with pgvector retrieval.
Dual payment gateways (Razorpay INR + Stripe USD). Chat agent
supports BYO provider keys (Groq / Anthropic) for non-superuser
users past 10-turn free allowance — see `docs/backend/byom.md`.
All pages migrated from Dash to Next.js.

| Service | Port | Entry | Stack |
|---------|------|-------|-------|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x, SQLAlchemy 2.0 async |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19 |
| PostgreSQL | 5432 | Docker | pgvector/pg16 (18 OLTP tables) |
| Redis | 6379 | Docker | Redis 7 Alpine |
| Docs | 8000 | Docker | MkDocs Material 9 |
| Alembic | — | `backend/db/migrations/` | PG schema migrations |

```bash
./run.sh start | stop | restart [svc] | rebuild [svc]
./run.sh status | logs <svc> [-f] | logs --errors | doctor
docker compose up -d                     # alt to run.sh
docker compose build backend             # after requirements.txt
ollama-profile coding|reasoning|embedding|status|unload
```

**Key dirs**: `backend/` (agents, tools), `backend/pipeline/` (19
CLI cmds), `backend/jobs/` (scheduler, pipeline chain, bulk OHLCV),
`backend/db/` (ORM, async engine, Alembic, DuckDB),
`backend/insights/` (ScreenQL parser, 39-field catalog),
`backend/maintenance/` (backup, compact, retain), `backend/tools/`
(forecast, sentiment, analysis), `auth/` (JWT + RBAC + OAuth),
`stocks/` (Iceberg, 12 OLAP tables), `frontend/`,
`frontend/providers/` (Chat, Layout, PortfolioActions contexts),
`e2e/` (Playwright), `hooks/`.

**Docker**: `Dockerfile.{backend,frontend,docs}`,
`docker-compose.yml`, `docker-compose.override.yml` (hot-reload),
`.env.example` / `.env`.

**Config**: `pyproject.toml` + `.flake8` (79 chars),
`frontend/eslint.config.mjs`.

**Data**: `~/.ai-agent-ui/` (override `AI_AGENT_UI_HOME`); paths
in `backend/paths.py`.

---

## LLM Cascade Architecture

`FallbackLLM` in `backend/llm_fallback.py` — N-tier:

| Tier | Provider | Model |
|------|----------|-------|
| 1-5 | Groq (free) | Round-robin pools: [70b, qwen3-32b] → [gpt-oss-120b, gpt-oss-20b] → scout-17b |
| N-1 | Ollama | gpt-oss:20b (`ollama_first=False`) |
| N | Anthropic | claude-sonnet-4-6 |

- `RoundRobinPool` (`backend/token_budget.py`): atomic per-pool
  counter, `get_token_budget()` singleton seeded from Iceberg.
  `ROUND_ROBIN_ENABLED=false` → legacy sequential.
- `OllamaManager` (`backend/ollama_manager.py`): TTL-cached health
  probe; cascade skips when unavailable.
- Admin API: `GET/POST /v1/admin/ollama/{status,load,unload}`.
- Per-request model pinning via `_pinned_model` — see LLM gotchas.

---

## Stock Data Pipeline

- **Module**: `backend/pipeline/` — 19 CLI cmds via
  `PYTHONPATH=.:backend python -m backend.pipeline.runner`
- **Sources**: yfinance primary (bulk/daily), jugaad-data fallback
  (retry/correct), racing (chat).
- **Ticker format**: ALL Indian stocks use `.NS` suffix everywhere.
  Never canonical (no-suffix) for data ops.
- **Market detection**: `detect_market` from
  `backend/market_utils.py`. Never local suffix checks.
- **Docs**: `docs/backend/stock-pipeline.md`.
- **Daily chain (6 steps, cron 08:00/08:15 IST Tue–Sat)**:
  `data_refresh → compute_analytics → run_sentiment → run_piotroski
  → recommendation_outcomes → iceberg_maintenance` (step 6 =
  backup-then-compact, fail-closed if backup fails).

---

## Hybrid DB Architecture

OLTP/OLAP split — PostgreSQL for row CRUD, Iceberg for append-only
analytics.

### PostgreSQL tables (SQLAlchemy 2.0 async ORM)

| Table | Module | Pattern |
|-------|--------|---------|
| `auth.users` | `db/models/user.py` | CRUD via `UserRepository` |
| `auth.user_tickers` | `db/models/user_ticker.py` | Insert + delete |
| `auth.payment_transactions` | `db/models/payment.py` | Insert + update |
| `stocks.registry` | `db/pg_stocks.py` | Upsert (`ticker_type`: stock/etf/index/commodity) |
| `public.scheduled_jobs` | `db/pg_stocks.py` | Upsert (has `force`) — schema is `public`, NOT `stocks` |
| `public.scheduler_runs` | `db/pg_stocks.py` | Insert + UPDATE — schema is `public` |
| `stocks.recommendation_runs` | `db/models/recommendation.py` | Smart Funnel meta. `run_type ∈ {manual,chat,scheduled,admin,admin_test}`; `admin_test` hidden from user reads |
| `stocks.recommendations` | `db/models/recommendation.py` | `data_signals` JSONB; `acted_on_date` auto-set by portfolio hook |
| `stocks.recommendation_outcomes` | `db/models/recommendation.py` | 30/60/90d checkpoints |
| `stocks.market_indices` | `db/models/market_index.py` | Single-row Nifty+Sensex cache |
| `public.user_memories` | `db/models/memory.py` | pgvector 768-dim |
| `public.conversation_contexts` | `db/models/conversation_context.py` | Cross-session chat ctx |
| `stock_master` | `db/models/stock_master.py` | symbol, yf_ticker, ISIN |
| `stock_tags` | `db/models/stock_tag.py` | nifty50/100/500 temporal |
| `ingestion_cursor`/`_skipped` | `db/models/` | Keyset pagination + retry log |
| `sentiment_dormant` | `db/models/sentiment_dormant.py` | Per-ticker headline-fetch dormancy (capped expo cooldown 2/4/8/16/30d, 5% probe re-test) |
| `pipelines` / `pipeline_steps` | `db/models/pipeline.py` | Chain definitions + steps |

### Iceberg tables (12 active — append / scoped-delete)

`company_info`, `dividends`, `ohlcv` (1.5M rows), `analysis_summary`,
`forecast_runs` (27 cols), `forecasts`, `quarterly_results`,
`piotroski_scores`, `sentiment_scores`, `llm_pricing`, `llm_usage`,
`portfolio_transactions`. Maintenance: `backend/maintenance/`.

### Key components

- `db/engine.py` — async `session_factory` (asyncpg, `pool_pre_ping=True`)
- `db/models/` — 18 ORM models (FK cascade, JSONB, pgvector)
- `auth/repo/repository.py` — `UserRepository` (per-call sessions)
- `db/pg_stocks.py` — registry + scheduler + pipeline PG funcs
- `jobs/pipeline_executor.py` — sequential chain, skip-on-fail,
  resume-from-step
- `db/duckdb_engine.py` — DuckDB read engine; call
  `invalidate_metadata()` after writes (auto-wired in repo)

---

## Hard Rules (NON-NEGOTIABLE)

### Performance

1. **Batch reads** — single DuckDB `WHERE ticker IN (...)` →
   pre-load dict. Never N individual Iceberg reads.
2. **Bulk writes** — accumulate in memory, write 1-2 Iceberg
   commits after the loop. Never per-ticker `_append_rows`.
3. **Iceberg = append-only analytics** — row-level `update` does
   full table scan + overwrite (~9s). Use PG for mutable state.
4. **NullPool for sync→async PG** — `_pg_session()` uses NullPool.
   See `shared/debugging/pg-nullpool-sync-async-bridge`.
5. **No nested parallelism** — outer `ThreadPoolExecutor` workers
   must NOT spawn inner `ProcessPoolExecutor`. Prophet CV
   `parallel=None`. `workers = cpu_count // 2`.
6. **Cache scope-level data** — VIX, indices, macro identical
   across tickers in scope. TTL cache.
7. **Throttle expensive I/O** — >100ms costs go to finalize batch
   or time-interval.
8. **No OHLCV full scans** — 1.4M rows. Use `ROW_NUMBER() OVER
   (PARTITION BY ticker)` or `WHERE ticker IN (...)` + date filter.

### Code Style

9. Line length 79 chars (black/isort/flake8 aligned).
10. No bare `print()` — `logging.getLogger(__name__)`.
11. `X | None` not `Optional[X]` (PEP 604).
12. No module-level mutable globals (exception: `_logger`).
13. No bare `except:` — `except Exception` or specific.
14. `apiFetch` not `fetch` (auto-refreshes JWT).
15. `<Image />` not `<img>` (ESLint enforced).
16. Patch at SOURCE module, not the importing module.

### Data & Writes

17. Iceberg writes MUST NOT be silenced — let errors propagate.
18. **Scoped deletes** — `In("ticker", batch)` not
    `EqualTo("score_date")`. Prevents cross-market overwrite.
19. Indian stocks `.NS` suffix everywhere; use `detect_market`.
20. **NEVER delete Iceberg metadata/parquet files directly** — use
    `overwrite()` / `delete_rows()` API only. Direct deletion
    breaks SQLite catalog. Backup before maintenance.

### Process & Git

21. Branch off `dev` — NEVER push to `dev`/`qa`/`release`/`main`.
22. Co-Authored-By: `Abhay Kumar Singh <asequitytrading@gmail.com>`.
23. Update `PROGRESS.md` after every session (dated).
24. `git add .serena/` before final push (memories are tracked).
25. Test-after-feature — happy path + 1 error path minimum.
26. Jira story points — set BOTH `customfield_10016` (estimate)
    AND `customfield_10036` (board display). `_10036` works on
    Stories but not Tasks.
26a. **PR merge strategy on `dev`: squash only.** Branch protection
    blocks merge-commit (`Merge commits are not allowed`) and
    rebase (`This branch can't be rebased`). Use
    `gh pr merge <n> --squash`. Sprint-level commit history is
    preserved on the source branch (not on `dev`) — don't delete
    the feature branch until history is no longer needed.

### Infra & Config

27. `NEXT_PUBLIC_BACKEND_URL=http://localhost:8181` — never
    `127.0.0.1` (cookie hostname mismatch).
28. No `@traceable` on `FallbackLLM.invoke()` — breaks LangChain
    tool-call parsing.
29. Ollama is host-native (not containerized); cascade falls back
    to Groq/Anthropic.
30. LHCI can't audit authenticated routes — use `npm run perf:full`.
31. **Container `TZ=Asia/Kolkata`** in `docker-compose.yml` backend
    service. `schedule` lib uses local time — UTC fires cron 5.5h
    late.
32. **`scheduler_catchup_enabled=False` default** — startup catchup
    of "missed" jobs silently pulled mid-day partial data. Opt-in
    via env if needed.

---

## Serena Shared Memories

`list_memories` to browse. Categories: `shared/architecture/`,
`shared/conventions/`, `shared/debugging/`, `shared/onboarding/`.

---

## Gotchas (learned the hard way)

### Data & Pipeline

- **yfinance pre-market flat candles**: O=H=L with NaN close
  before settlement. Pipelines at 08:00 IST — delete NaN, refetch.
- **Forecast backtest convention**: `horizon_months=0`, actual in
  `lower_bound`. Persist when CV runs.
- **Prophet CV from stdin**: `parallel="processes"` →
  `FileNotFoundError: /app/<stdin>`. Use `parallel=None`.
- **DuckDB metadata cache**: `invalidate_metadata()` in
  `_retry_commit()`. Stale-read ⇒ check invalidation wired.
- **Iceberg `company_info` upsert**: deletes existing ticker row
  before append. One row per ticker.
- **Iceberg concurrent writes**: SQLite catalog conflicts under
  Semaphore(10). Fundamentals uses Semaphore(1).
- **Iceberg flush window**: `ObservabilityCollector` flushes 30s.
  Restarts lose unflushed events. Seed on startup.
- **yfinance sectors**: "Technology" not "IT", "Financial Services"
  not "Financials".
- **jugaad-data timeout**: NseSource wraps in
  `asyncio.wait_for(timeout=60.0)`.
- **Forecast regime**: stable (<30% vol), moderate (30-60%),
  volatile (>60%). Each gets different Prophet config.
- **Log-transform**: applied for moderate/volatile. `np.log(y)`
  before fit, `np.exp(yhat)` after — guarantees non-negative.
- **Technical bias**: RSI/MACD/volume dampen forecast ±15%, taper
  30d. Post-processing, not model change.
- **Confidence score**: 5-component (direction, MASE, coverage,
  interval, completeness). <0.25 rejected (hidden).
- **FinBERT sentiment**: `sentiment_scorer=finbert` routes batch
  to ProsusAI/finbert (CPU, free); LLM cascade for chat only.
  `refresh_ticker_sentiment()` idempotent — won't re-score if
  today's data exists, even forced.
- **XGBoost casing**: `compute_indicators()` returns Title-case
  (`RSI_14`); `_FEATURES` expects lowercase. Fix:
  `tech.columns = [c.lower() for c in tech.columns]`.
- **Forecast run dedup**: use `computed_at` (UTC ts) not
  `run_date`. Affects `get_dashboard_forecast_runs()` /
  `get_latest_forecast_run()`.
- **Portfolio period overlap**: `get_portfolio_comparison` uses
  `_period_to_days()` for non-overlapping windows.
- **Portfolio bfill**: `_compute_daily_portfolio()` needs
  `ffill().bfill()` — without bfill, partial first-rows inflate
  returns to 4000%+.
- **Portfolio ETF sector NaN**: `company_info.sector` is `NaN`
  (float) for ETFs. Use `safe_str` / `safe_sector` (see NaN-truthy
  trap below).
- **ForecastTarget nullable**: `target_price`, `pct_change`,
  `lower_bound`, `upper_bound` must be `float | None`. Sparse
  portfolios → `None` else 500.
- **Bulk OHLCV download**: `_bulk_fetch_ohlcv()` uses
  `yf.download()` batches of 100 (99.8% vs 56% per-ticker).
  `^`-prefixed indices fail in bulk.
- **company_info snapshot bloat**: per-ticker appends without
  retention → 4055 files for 830 rows. `overwrite()` to compact.

### Database & PG

- **`_pg_session()` NullPool**: ~2-5ms/call. Don't use in hot
  loops — batch via DuckDB or bulk PG.
- **asyncpg `pool_pre_ping=True`** required in `engine.py` —
  stale conns crash on uvicorn reload.
- **Iceberg NaT/NaN → PG**: sanitize before insert; PG rejects.
- **UserMemory `extend_existing=True`** — dual import causes
  "Table already defined".
- **FastAPI `Query()` defaults**: pass explicitly in internal
  calls (`ticker=None`).

### Docker & Infra

- Health check: `/v1/health` not `/health`.
- Iceberg mount: `~/.ai-agent-ui` at SAME path inside container
  (SQLite catalog stores absolute paths).
- Ollama: `host.docker.internal:11434`.
- `.pyiceberg.yaml`: mount `/app/.pyiceberg.yaml:ro` else silent
  read fail.
- Frontend: `node:22-slim` (glibc), `HOSTNAME=0.0.0.0`.
- Seed script: set `PYICEBERG_CATALOG__LOCAL__URI` before pyiceberg
  import; mount `fixtures/`.
- Backup: `rsync` not in container — run from host or install in
  image.
- After code changes touching cache: `redis-cli FLUSHALL`.
- **Sync Iceberg I/O in async routes**: wrap in
  `asyncio.to_thread()`. Applies to retention cleanup,
  `backfill_nan` / `backfill_missing` data-health actions.
- **`env_file` reload**: `docker compose restart backend` does NOT
  re-read `.env`. New env vars need
  `docker compose up -d --force-recreate backend`.
- **Alembic `.pyc` cache**: renaming a migration file isn't enough
  — also edit the `revision: str = "..."` line inside AND clear
  `/app/backend/db/migrations/versions/__pycache__/` in container,
  else cycle/duplicate-revision errors.
- **uvicorn `--reload` doesn't re-register routes/models**: adding
  a new FastAPI route or a new field on an existing Pydantic
  response model triggers `StatReload`, but `app.include_router()`
  and `response_model` schema binding happen at app-startup time.
  Result: the file is reloaded but OpenAPI + the live worker still
  use the old shape — new routes return 404, new fields are
  silently dropped from responses. Fix: `docker compose restart
  backend` (or `--force-recreate` if env also changed). Verify via
  `curl /openapi.json | jq` that the new schema/route is present.
- **Backend restart asyncpg shutdown race**: `docker compose
  restart backend` first request returns 500 with empty body for
  ~5 s while the asyncpg pool finishes terminating ("Event loop is
  closed" in logs). Sleep 5 s before any auth-dependent test/curl,
  or you'll waste a debug cycle on a phantom failure.
- **`BACKEND_URL=http://backend:8181` required on dev frontend**
  for RSC server-side fetches (`frontend/lib/serverApi.ts`).
  `localhost` from inside the frontend container points to the
  container itself, not the host backend. Set in
  `docker-compose.override.yml` for both `frontend` and
  `frontend-perf` services.

### Iceberg Maintenance (CRITICAL)

- **NEVER delete metadata files** — SQLite catalog stores absolute
  paths to `.metadata.json`. Recovery:
  ```sql
  sqlite3 ~/.ai-agent-ui/data/iceberg/catalog.db
  UPDATE iceberg_tables
  SET metadata_location='file:///path/to/latest.metadata.json'
  WHERE table_name='ohlcv';
  ```
- **NEVER delete parquet directly** — use `overwrite()` /
  `delete_rows()`. Orphan cleanup may only remove empty dirs.
- **Compaction**: `overwrite()` (read all via DuckDB → write back
  as one batch). OHLCV: 8670 → 817 files, 9s → 0.24s reads.
- **Backup before maintenance**: always `run_backup()`. Includes
  warehouse + `catalog.db`. Location:
  `/Users/abhay/Documents/projects/ai-agent-ui-backups/` (2 latest).
- **OHLCV freshness**: `latest >= today` (not yesterday). Evening
  runs re-fetch closing data.
- **OHLCV upsert (NaN-replaceable)**: dedup query filters
  `WHERE close IS NOT NULL AND NOT isnan(close)` so a stuck NaN row
  doesn't block a future re-fetch. Pre-delete NaN rows for the
  to-be-inserted `(ticker, date)` set before append. Today's data
  additionally uses scoped delete + re-append for intraday → close
  correction. Pattern in both `insert_ohlcv` + `batch_data_refresh`.
- **Daily auto-compaction**: `iceberg_maintenance` step in both
  daily pipelines runs `run_backup()` (fail-closed) then
  `compact_table()` for `ohlcv`, `sentiment_scores`, `company_info`,
  `analysis_summary`. `rsync` installed in `Dockerfile.backend` for
  container-side backup. Without this, OHLCV file count grew to 16K
  parquets within a week → reads 5+s, `Clean NaN Rows` 5+ min.
- **Snapshot expiry**: current `expire_snapshots()` in
  `pipeline_executor.py` and `iceberg_maintenance.py` is a no-op
  (logs only). The "PyIceberg API fragile" comment is **outdated** —
  PyIceberg 0.11.1 ships a working `tbl.maintenance.expire_snapshots()
  .by_ids(...).commit()` (also `.older_than()`, `.by_id()`).
  ASETPLTFRM-338 implements the proper orphan sweep using it; design
  in `.serena/memories/shared/architecture/iceberg-orphan-sweep-design.md`.
- **DEAD_TABLES** in `iceberg_maintenance.py` is now empty after
  2026-04-25 cleanup that dropped `stocks.scheduler_runs`,
  `stocks.scheduled_jobs`, `stocks.technical_indicators` (commit
  `c0447dc`). PG `public.scheduler_runs/scheduled_jobs` are the
  canonical sources; `_analysis_indicators.py` computes TA on demand.
- **Schema evolution + backend restart**: after
  `tbl.update_schema().add_column()`, backend worker's
  in-process DuckDB connection caches the old schema.
  `invalidate_metadata()` + Redis FLUSHALL are NOT
  enough — must `docker compose restart backend`.
  Apply to every deploy env (dev/qa/release/main)
  after running an `evolve_*` function.
- **torch CPU-only**: install via
  `pip install torch --index-url .../whl/cpu`. Do NOT add to
  requirements.txt. Add `transformers>=4.40` separately.

### LLM & Chat

- **TokenBudget**: use `reserve()`/`release()` (atomic), not
  `can_afford()`/`record()` (TOCTOU).
- **Hallucination guardrail**: `_is_hallucinated()` rejects 3+
  stock patterns with zero `tool_done` events.
- **Chat clarification**: `?` ending bypasses keyword gate —
  `_is_clarification()` in `guardrail.py`.
- **ReAct iteration**: `sub_agents.py` MUST pass
  `iteration=iteration+1`.
- **Groq tool-call IDs**: `_sanitize_tool_ids()` cleans before
  Anthropic fallback.
- **`bind_tools` model_lookup**: must rebuild after
  `FallbackLLM.bind_tools()`.
- **Groq TPD**: ~2.0M combined across 5 models. `TokenBudget`
  seeds from Iceberg on restart.
- **Model pinning**: `_pinned_model` locks model after first invoke
  per request. Hits budget → compress, then unpin + cascade. Call
  `pin_reset()` before each new ReAct loop.
- **Double-synthesis**: portfolio uses `skip_synthesis=True` —
  tools return formatted tables. Graph synthesis passes through
  responses >100 chars.

### Recommendations

- **Monthly-per-scope quota**: 1 run per `(user, scope, IST month)`.
  All entry points (widget/chat/scheduler) → `get_or_create_monthly_run`
  in `jobs/recommendation_engine.py`. `scope="all"` expands to
  india + us. IST via `ZoneInfo("Asia/Kolkata")`.
- **`run_type`**: `manual | chat | scheduled | admin | admin_test`.
  User reads filter `admin_test` via `exclude_test=True`. Admin tab
  passes `exclude_test=False`.
- **Admin force-refresh + promote** (superuser-only):
  `POST /v1/admin/recommendations/force-refresh {user_id|email,
  scope}` bypasses quota → `admin_test`.
  `POST /admin/recommendation-runs/{id}/promote` deletes existing
  non-test run for same `(user, scope, month)` + relabels to
  `run_type='admin'`.
- **`expire_old_recommendations` IS scope-aware** — don't regress.
  Cross-scope wipe was a real bug.
- **Acted-on auto-detect**: `POST/PUT/DELETE /v1/users/me/portfolio`
  fires daemon thread → `update_recommendation_status(uid, ticker,
  actions, "acted_on")` via NullPool bridge. BUY/ACCUMULATE on POST;
  SELL/REDUCE/TRIM on qty-decrease (PUT) or delete. Only matches
  `status='active'`; expired recs silently skipped.
- **Stats**: `/recommendations/stats` + `/history` are scope-aware
  (`?scope=india|us|all`). `total_acted_on` derived from
  `acted_on_date`, NOT `recommendation_outcomes` (those are
  30/60/90d price checks).

### Auth & RBAC

- **Three roles**: `general | pro | superuser`
  (`auth.users.role VARCHAR(50)`). Pydantic Literals enforce in
  `UserCreateRequest`/`UserUpdateRequest`.
- **Tier → role auto-sync** (`auth/repo/user_writes.py::update()`):
  when `subscription_tier` in updates AND role ≠ `superuser`,
  flips: `free→general`, `pro|premium→pro`. **Superuser is
  sticky**. Fires `ROLE_PROMOTED`/`ROLE_DEMOTED` post-commit.
- **Dependency guards**: `superuser_only` for ~45 admin endpoints;
  `pro_or_superuser` alias for `/admin/audit-log`,
  `/admin/metrics`, `/admin/usage-stats`. Pro forced to
  `scope=self`; `scope=all` → 403 unless superuser.
- **JWT role cached**: `get_current_user` reads claim (no DB
  re-read). Role change propagates only after `/auth/refresh`
  (≤60 min). `BillingTab.tsx` calls `refreshAccessToken()` after
  subscription writes.
- **Audit vocabulary**: `LOGIN`, `PASSWORD_RESET`, `OAUTH_LOGIN`,
  `USER_CREATED/UPDATED/DELETED`, `ADMIN_PASSWORD_RESET`,
  `ROLE_PROMOTED/DEMOTED`, `BYO_KEY_ADDED/UPDATED/DELETED`.
  `PATCH /auth/me` writes `USER_UPDATED` (actor==target).

### Sentiment Batch

- **Step-5 read uses PyIceberg, NOT DuckDB**: `query_iceberg_df`
  returns empty under concurrent commits because filesystem-glob
  latest-snapshot lookup is racy (DuckDB reads a metadata file
  whose manifests aren't yet visible). Use
  `tbl.refresh().scan(EqualTo(score_date, today))` for the
  post-worker freshness re-query. Pre-fix: 802/802 market_fallback
  overwrote finbert.
- **Step-5 source-aware delete**: predicate includes
  `In("source", ["market_fallback", "none"])` so force-runs cannot
  clobber finbert/llm rows.
- **Hot classifier source filter**: `IN ('finbert', 'llm')` (was
  `'llm'`-only — stale post-FinBERT cutover).
- **market_cap selector**: top-50 learning batch joins
  `stocks.company_info.market_cap` (registry doesn't expose it →
  was sorted alphabetically, picked obscure A-prefixed small caps).
- **Workers 5 (was 15)**: Yahoo/Google rate-limit above ~5 parallel.
  Combined with dormancy (~60% fewer total HTTP calls), throughput
  unchanged.
- **Sentiment dormancy**: `sentiment_dormant` PG table tracks
  tickers returning 0 headlines K times. Excluded from
  learning/cold; 5% probe re-tested by oldest `last_checked_at`.
  `force=True` runs ignore dormancy. Capped expo cooldown via
  `_compute_next_retry()` in `pg_stocks.py`.
- **News widget 21-day max-age + unanalyzed chip**:
  `/portfolio/news` drops articles >21d old; response includes
  `unanalyzed_tickers: list[str]` (tickers whose latest sentiment
  is `market_fallback`/`none`). See Frontend → stale-data chip.
- **Per-source 10s timeout**: `_fetch_yfinance`, `_fetch_yahoo_rss`,
  `_fetch_google_rss`, `fetch_market_headlines` wrapped with
  `_run_with_timeout(fn, *args, timeout=10)` in
  `tools/_sentiment_sources.py`. Without it, `yf.Ticker().news`
  deadlocks the 15-worker pool.
- **Learning-set cap**: `execute_run_sentiment` caps `learning` at
  top-50 by `market_cap`; tail → Step-5 fallback. Tune
  `_LEARNING_CAP` if more coverage needed.
- **FinBERT mode skips LLM build**: when
  `settings.sentiment_scorer == "finbert"`, `refresh_sentiment`
  passes `llm=None`. LLM only built when config is `"llm"` or
  FinBERT fails.
- **`source` column**: `finbert | llm | market_fallback | none`.
  `none` = no headlines fetched. `score_headlines_with_source()`
  returns `(score, source)`; `score_headlines()` is back-compat.
- **Force = upsert**: scheduler `force=true` → skips per-ticker
  idempotency. `insert_sentiment_score` already upserts (scoped
  delete + append by `(ticker, score_date)`).

### Insights ticker scoping (three-tier)

- `insights_routes.py::_scoped_tickers(user, scope)` is the single
  helper. Scope: `"discovery" | "watchlist" | "portfolio"`.
- **Tab → scope**:
  - `discovery` (Screener, ScreenQL, Sectors, Piotroski) →
    pro/superuser see full universe (`stock`+`etf`); general sees
    watchlist ∪ holdings.
  - `watchlist` (Risk, Targets, Dividends) → everyone sees
    watchlist ∪ holdings.
  - `portfolio` (Correlation, Quarterly) → holdings only
    (`get_portfolio_holdings` where `quantity > 0`).
- **Full-universe filter**: `ticker_type IN ('stock', 'etf')`.
  Indices (`^NSEI`, `^GSPC`) and commodity (`GC=F`) excluded.
- **Per-user cache key** on Piotroski: include `user_id` in Redis
  key (was platform-wide; cross-user cache leak fixed).
- Legacy shim `_get_user_tickers(user)` resolves to `watchlist`.

### NaN-truthy trap

- `float('nan')` is truthy. `row.get("sector") or "Other"` KEEPS
  the NaN. yfinance returns NaN for ETF sector/industry/name —
  leaked literal `"NaN (41.8%)"` into LLM prompts.
- **Use shared helpers** in `backend/market_utils.py`:
  - `safe_str(val) -> str | None` — None/NaN/whitespace → None.
    Also rejects stringified-NaN sentinels `{nan, none, null, n/a,
    na, nat}` (case-insensitive, post-strip). Legit substrings
    (`Naniwa`, `Financial Services`) pass.
  - `safe_sector(val, fallback="Other") -> str` — always non-empty.
- Applied at write-paths (repo, batch_refresh, pipeline
  fundamentals, universe, screener, stock_data_tool) and read-paths
  (recommendation_engine stage2, dashboard_routes portfolio,
  report_builder, insights_routes). Existing rows may still have
  NaN; reads handle it.
- **NaN propagation through arithmetic**: `val += qty * NaN` makes
  `val` NaN. `NaN > 0` is False → silently drops the date in any
  `if val > 0: append(...)` aggregator. Always guard with
  `math.isnan` (or use `_safe_float` returning None) BEFORE
  accumulating in any per-ticker → portfolio aggregation loop.

### BYOM (Bring Your Own Model)

- **Product rule**: every non-superuser gets 10 free chat turns.
  After 10, must configure Groq and/or Anthropic key — else 429
  "Configure a Groq or Anthropic key on the My LLM Usage page".
  Non-chat flows (recs, sentiment, forecast) + superusers stay on
  platform keys.
- **ContextVar pattern**: `backend/llm_byo.py` exposes `BYOContext`
  + `apply_byo_context()`. MUST be set INSIDE worker thread —
  `run_in_executor` doesn't propagate ContextVars. Every chat entry
  point (`/chat`, `/chat/stream`, LangGraph variants, WS
  `_run_graph`/`_run_legacy`) wraps worker in
  `apply_byo_context(byo_ctx)`.
- **Post-chat side-effect trap**: `update_summary()` MUST run inside
  the `apply_byo_context()` block or it leaks to platform keys.
  Use `_update_summary_in_byo_scope()` (WS) or nest in `with` (HTTP).
- **`chat_request_count` bump**: guard with `byo_active=bool` so
  free-allowance counter freezes once BYO kicks in. Scope-self
  response clamps `free_allowance_used = min(count, 10)`.
- **Raw `ChatGroq`/`ChatAnthropic` audit**: any node bypassing
  `FallbackLLM` MUST also check `get_active_byo_context()` and swap
  to user-keyed client. Past offender:
  `agents/nodes/llm_classifier.py` (fixed). Audit when adding new
  nodes.
- **Fernet setup**: `BYO_SECRET_KEY` env (32-byte URL-safe base64).
  Generate via `Fernet.generate_key().decode()`. Container must be
  recreated to pick up new env: `docker compose up -d
  --force-recreate backend`.
- **Redis counter**: `byo:month_counter:{user_id}:{yyyy-mm}` (IST
  month, 40d TTL). `CacheService.set()` uses `ttl=` (NOT `ex=`);
  wrong kwarg → silent `TypeError`. `cache.get()` returns `str`
  (decode_responses=True), not `bytes`.
- **`FallbackLLM.bind_tools()`** stores `_bound_tools` +
  `_bound_tools_kwargs` so BYO clients rebind on each
  `_try_model()`. Don't remove.
- **Per-user client cache** keyed `(provider, model,
  sha256(key)[:12])`.
- **WS 429 delivery**: `_handle_chat` MUST send errors directly
  via `ws.send_json({"type":"error"})` + `{"type":"final"}` —
  returning `event_queue` after enqueueing error spins drain loop
  forever. Same trap on monthly-quota path.
- **Iceberg `key_source` column**: nullable on `stocks.llm_usage`.
  Legacy null rows = `platform` at read time. Schema evolved via
  `tbl.update_schema().add_column()` — no backfill.

### ContextVar propagation (async → thread)

- `loop.run_in_executor(executor, fn)` does NOT copy calling task's
  ContextVars. ContextVars set in async route handler are EMPTY in
  worker thread.
- **Fix**: set ContextVar INSIDE worker via scoped context manager
  (`apply_byo_context`, `apply_X_context`) or
  `contextvars.copy_context().run(fn)`.
- Block-exit auto-clears for next request — verify in unit test
  (`test_apply_and_auto_clear`).

### Tool-result truncation hallucination

- `MessageCompressor` (`backend/message_compressor.py`) truncates
  `ToolMessage.content` with literal `[truncated N chars]` marker.
  Mid-table marker → LLM hallucinates missing rows in synthesis.
- **Defaults**: `max_tool_result_chars=4000`; pass 2 = 2500;
  pass 3 = 1500. Don't drop <3000 without testing portfolio +
  screener.
- **Prompt guardrails**: `_SYNTHESIS_PROMPT` + portfolio sub-agent
  prompt include NO HALLUCINATION ON TRUNCATION clause. Don't
  remove. Mirror in any new table-returning sub-agent prompt.

### Frontend

- **RSC + cookie-auth pattern** (Sprint 8, ASETPLTFRM-334):
  authenticated routes can pre-fetch on the server via
  `frontend/lib/serverApi.ts` (reads `access_token` cookie set by
  `/v1/auth/login`). `frontend/proxy.ts` (Next.js 16 rename of
  `middleware.ts`) gates protected routes on cookie presence —
  must accept EITHER `access_token` OR `refresh_token`, otherwise
  pre-A.1 sessions infinite-loop. RSC `page.tsx` can ONLY have a
  default async export — move shared types (e.g. `MarketFilter`)
  to the sibling client file. Full guide:
  `docs/frontend/ssr-patterns.md`.
- **`cacheComponents: false`** in `next.config.ts` is intentional
  (Next 16 renamed `experimental.ppr` to top-level
  `cacheComponents`). Flipping to `true` surfaces "new Date() /
  useTheme() in Client Component without Suspense" errors on
  /dashboard, /analytics, /admin. Activate only after wrapping
  those Client Components in `<Suspense>` (Sprint 9 candidate
  TBD-D).
- **ScreenQL multi-line AND**: newlines are implicit AND. Lines
  starting with `AND`/`OR` don't add another. Parser handles in
  `tokenize()` smart newline join.
- **ScreenQL RSI field**: `rsi_14` extracted via
  `TRY_CAST(regexp_extract(rsi_signal, 'RSI:\\s*([\\d.]+)', 1) AS
  DOUBLE)` — not standalone column. Map in `screen_parser.py`.
- **ScreenQL columns**: base 5 always (ticker, company, sector,
  mcap, price); query fields auto-added; `currency` and `market`
  hidden helpers.
- **CSV download**: `downloadCsv()` in `frontend/lib/`.
  `InsightsTable.onDownload` receives sorted (not paginated) rows.
  Excludes `action` cols.
- **KpiTooltip**: clamp `left` to `viewport - tipWidth - 8px` to
  prevent right-edge overflow.
- **Admin API caching**: `data-health` 60s, backup 120s in Redis.
  First load post-expiry slow (~1-12s) due to 113K orphans.
- **ChatInput**: `readOnly` not `disabled` (keeps focus).
- **Sidebar**: collapsed by default; hover flyout for submenus.
- **ECharts dark/light**: `MutationObserver` on `<html>` class,
  not `useTheme()`. `notMerge={true}` + `key`.
- **Perf script login**: `type()` not `fill()` (React `onChange`
  needs keystrokes).
- **`apiFetch`** requires full URL: `${API_URL}/path`, not
  relative `/path` (relative hits Next.js 3000 not backend 8181).
- **Market ticker**: `MarketTicker` in `AppHeader.tsx` center.
  NSE + Yahoo dual-source, 30s poll, PG+Redis. Off-hours: zero
  upstream calls.
- **Yahoo `^BSESN` freezes mid-session**: Yahoo's BSE feed stops
  emitting new ticks after ~10 min of market open. Detected via
  `regularMarketTime` in `_is_yahoo_quote_stale()` (>300s old
  during market hours). Fallback: Google Finance scrape
  (`SENSEX:INDEXBOM`, `data-last-price` regex) overlaid on
  Yahoo's `prev_close`. Nifty unaffected (NSE primary source).
- **`ticker_type`**: stock (755), etf (54), index (4), commodity
  (4). `_analyzable_tickers()` = stock+etf;
  `_has_financials()` = stock only.
- **ETF bulk-download**: `--tickers` expects symbols WITHOUT `.NS`
  (script auto-appends from `stock_master.yf_ticker`).
- **DuckDB stale reads**: data-health calls `invalidate_metadata()`
  before queries, else fix results don't show until restart.
- **`<div>` in `<p>`**: use `<span>` for inline (e.g. confidence
  badge) — `<div>` causes hydration error.
- **Screener column selector**: 39-column catalog in
  `SCREENER_COL_CATALOG` in `insights/page.tsx`; user
  selection persists via `localStorage` through
  `useColumnSelection` hook. ScreenQL equivalent via
  `display_columns` on `/screen` request body. Adding
  a new Screener field = edit `ScreenerRow` (backend
  Pydantic + frontend TS) + `screenerCols` + catalog
  entry + CSV entry (same file).
- **PortfolioActionsProvider**: Add/Edit/Delete/**Transactions**
  portfolio modals mounted ONCE at
  `frontend/app/(authenticated)/layout.tsx`. Pages use
  `usePortfolioActions()` → `{openAdd, openEdit, openDelete,
  openTransactions}`. Do NOT route-redirect to
  `/dashboard?add=TICKER` — stacks behind open slideover.
  `openTransactions(ticker)` opens `PortfolioTransactionsModal`
  (date-sorted txns + per-row edit pencil + summary footer).
  Eye icon on portfolio rows opens this; inline edit pencil REMOVED
  from portfolio rows (view-first-edit-from-within UX).
- **Portfolio P&L NaN truncation**: `_build_portfolio_performance`
  used to drop entire dates when any held ticker had NaN close
  (`val += qty * NaN` → `val > 0` False). Four defenses now in
  place: (1) `math.isnan` guard in daily loop, (2) per-ticker
  `df["close"].ffill()` pre-`close_maps`, (3) `stale_tickers` field
  + amber chip, (4) ffill-to-series-end so a ticker missing a
  trailing date carries last close to series end.
- **Stale-data chip pattern** (reusable):
  `PLTrendWidget::StaleTickerChip` + `NewsWidget::UnanalyzedChip`.
  Backend exposes `stale_tickers: list[StalePriceTicker]` /
  `unanalyzed_tickers: list[str]`. Amber chip near panel title,
  hover/click tooltip lists entities, auto-clears when list empty.
  When adding a new aggregate over per-entity values where some
  can be stale, mirror this pattern (Serena
  `shared/architecture/portfolio-pl-stale-ticker-chip`).
- **OHLCV chart triple-dedup**: defensive layers — Iceberg
  (NaN-replaceable upsert), backend route (`drop_duplicates` before
  serializing), frontend chart (`Map`-keyed by time before
  `setData`). Lightweight-charts asserts on duplicate timestamps;
  any single layer regressing won't crash the chart.
- **Modal z-index**: Add/Edit/Confirm = `z-[70]` (above
  `RecommendationSlideOver` `z-[60]`). New modals triggered from
  inside slideovers must also be `z-[70]`.
- **Shared `DownloadCsvButton`**:
  `frontend/components/common/DownloadCsvButton.tsx` (icon + "CSV"
  text, Screener pattern). All CSV exports use it. Place next to
  pagination, not headers. `loading` prop for async-collecting.
- **Pro role admin UI**: `frontend/app/(authenticated)/admin/page.tsx`
  has single `ALL_TABS` array with `roles: Role[]` per entry. Pros
  see only `my_account`, `my_audit`, `my_llm`. Route gate redirects
  general → `/dashboard`.
- **`MyLLMUsageTab.tsx`**: standalone (NOT delegating to superuser
  `ObservabilityTab`). Consumes scope-self shape (`quota`,
  `providers`, `daily_trend`, per-model rollup with
  `requests_platform` + `requests_user`). Mirror backend shape
  changes in `frontend/lib/types.ts::UserModelUsage`.
- **Scope-aware admin hooks**: `useAdminAudit(scope)`,
  `useObservability(scope)`, `getUsageStats(scope)` all take
  `"self" | "all"`. `obsFetcher` skips superuser-only `tier-health`
  GET on `scope="self"`.
- **Idempotent DELETE UX**: handlers via confirm modal treat 404
  as success (already-removed) alongside 204. See
  `useAdminData::deleteKey`.
- **Timestamp parsing**: `fmtRelative()` needs ISO 8601 with TZ
  marker (`Z` or `+HH:MM`). Iceberg timestamps come tz-naive —
  backend must stamp `Z` (shared `_iso_utc()` in `routes.py`).
  Without it, `new Date()` parses as local → "5h ago" in IST.

### Testing & Config

- **Test mock dates**: never hardcode — use
  `str(int(time.time()) - 86400)` for "yesterday".
- **`settings.local.json`**: no `()` in Bash patterns.
- **slowapi rate limiter**: `limiter.enabled = False` in test
  fixtures, not `limiter.reset()`.
- **`get_settings().debug`**: use `getattr()` with fallback.
- **StockRepository**: always via `_require_repo()`.
- **Superuser insights**: `_get_user_tickers()` shows full registry
  for superusers, watchlist-only for general.

### E2E Testing (Playwright)

`e2e/playwright.config.ts`: Local 1 worker; CI 2 workers, retries
2, `maxFailures 0`, video retain-on-failure.

| Project | Auth | Tests |
|---------|------|-------|
| `frontend-chromium` | superuser | chat, billing, profile, dark-mode, nav |
| `analytics-chromium` | general | dashboard, insights, marketplace, portfolio-crud |
| `admin-chromium` | superuser | admin CRUD, observability, scheduler |

**Conventions**:
- Credentials: `admin@demo.com` / `Admin123!` (run
  `seed_demo_data.py` if login fails).
- Chat panel: collapsible on `/dashboard`. `ChatPage.goto()` clicks
  "Toggle chat panel". Locators scoped to `[data-testid="chat-panel"]`.
- Testid constants: all in `e2e/utils/selectors.ts` (FE object).
  Page objects use `this.tid(FE.xxx)`. Never hardcode strings.
- Visual baselines: regenerate via
  `npx playwright test --update-snapshots`. In `*.spec.ts-snapshots/`,
  committed.

**Gotchas**:
- **Never `networkidle`** — dashboard polls 30s + WebSocket. Use
  explicit element waits (`getByTestId("sidebar").toBeVisible()`).
- **Below-fold widgets** (Watchlist, Forecast, P&L): need
  `waitFor({ state: "attached" })` then `scrollIntoViewIfNeeded()`.
- **After `page.reload()`**: chat panel closes; wait for sidebar.
- **CSS `uppercase` vs DOM**: `getByText("CURRENT PLAN")` fails
  when DOM has "Current Plan" w/ CSS uppercase. Use `getByTestId`
  or exact case.
- **Strict mode**: `/cancel|close/i` matches Cancel button + Close
  X. Use `/^cancel$/i`.
- **Statement type options**: Quarterly tab uses `income`, `balance`,
  `cashflow` (not `balance_sheet`).
- **Never increase workers** — 3 workers consumed >1000% CPU and
  starved Docker.

---

## Quick Reference

```bash
# Lint
black backend/ auth/ stocks/ scripts/
isort backend/ auth/ stocks/ scripts/ --profile black
flake8 backend/ auth/ stocks/ scripts/
cd frontend && npx eslint . --fix

# Test
python -m pytest tests/ -v               # ~925 tests
cd frontend && npx vitest run            # 18 frontend tests
cd e2e && npm test                       # ~257 E2E (live services)

# E2E (one project at a time, 1 worker, ~3 min each)
cd e2e && npx playwright test --project=frontend-chromium
cd e2e && npx playwright test --project=analytics-chromium
cd e2e && npx playwright test --project=admin-chromium
cd e2e && npx playwright test --update-snapshots

# Migrations
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. alembic revision --autogenerate -m "desc"
PYTHONPATH=backend python scripts/migrate_iceberg_to_pg.py

# Seed
PYTHONPATH=backend python scripts/seed_demo_data.py
docker compose exec backend python scripts/seed_demo_data.py

# BYOM first-time
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
docker compose up -d --force-recreate backend  # re-read .env

# Alembic stale bytecode
docker compose exec backend rm -f /app/backend/db/migrations/versions/__pycache__/*.pyc

# Destructive maintenance
docker compose exec backend python3 scripts/truncate_recommendations.py --yes
docker compose exec backend python3 scripts/detect_illiquid.py

# Stock pipeline (PYTHONPATH=.:backend python -m backend.pipeline.runner ...)
download | seed --csv ... | bulk-download | fill-gaps | status
analytics --scope india | sentiment --scope india | forecast --scope india
screen | refresh --scope india --force

# Performance (cd frontend)
npm run perf:check       # LHCI on /login (pre-PR gate)
npm run perf:audit       # Playwright 10-route quick
npm run perf:full        # Full 42-point audit
npm run analyze          # Bundle treemap
```

# CLAUDE.md — AI Agent UI

> Slim rules. New features/bug fixes **MUST** follow §4 (hard) +
> §5 (patterns). Detail lives in Serena memories — referenced
> inline as `→ memory-name`; browse all ~170 via `list_memories`
> (paths `shared/<category>/<name>`).

---

## 1. Session Startup

1. **Serena**: `activate_project ai-agent-ui`
2. **Ollama**: `ollama-profile coding` if delegating
3. **Superpowers**: invoke applicable skill (brainstorming, TDD, executing-plans)
4. **SuperClaude**: `/sc:` for git/build/test/analyze/implement/troubleshoot
5. **Branch**: `git checkout dev && git pull && git checkout -b feature/<desc>` — NEVER commit on `dev`/`qa`/`release`/`main`

## 2. MCP Tools

| Server | Purpose |
|---|---|
| Serena | Code/symbol nav, shared memories |
| Ollama | Local LLM delegation (Qwen code-gen) |
| Context7 | Library/framework docs |
| Playwright / Chrome DevTools | Browser automation, perf |
| Atlassian (Jira) | Sprint/ticket management |
| Sequential Thinking | Multi-step reasoning |

## 3. Stack

Fullstack agentic chat: stock analysis, Prophet forecasting (vol-regime adaptive), FinBERT + XGBoost sentiment, portfolio dashboard, pgvector chat memory, Razorpay INR + Stripe USD billing, BYO Groq/Anthropic past 10-turn free.

| Service | Port | Entry | Stack |
|---|---|---|---|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x, SQLAlchemy 2.0 async |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19 |
| PostgreSQL | 5432 | Docker | pgvector/pg16 (19 OLTP tables) |
| Redis | 6379 | Docker | Redis 7 Alpine |
| Alembic | — | `backend/db/migrations/` | PG schema migrations |

DB inventory: 19 PG OLTP + 12 Iceberg OLAP → `db-table-inventory`. Data home: `~/.ai-agent-ui/` (override `AI_AGENT_UI_HOME`); paths in `backend/paths.py`. Container `TZ=Asia/Kolkata`.

`./run.sh start|stop|restart [svc]|rebuild [svc]|status|logs <svc> [-f]|doctor` — see §7 for rebuild commands.

---

## 4. Hard Rules — NON-NEGOTIABLE

### 4.1 Performance

1. **Batch reads** — single `WHERE ticker IN (...)` → dict. Never N reads.
2. **Bulk writes** — accumulate, write 1–2 Iceberg commits. Never per-ticker `_append_rows`.
3. **Iceberg = append-only**. Row-level `update` = full scan + overwrite. Mutable state → PG.
4. **NullPool for sync→async PG** — `_pg_session()`. → `pg-nullpool-sync-async-bridge`
5. **No nested parallelism** — outer `ThreadPoolExecutor` workers must NOT spawn `ProcessPoolExecutor`. Prophet `parallel=None`. `workers = cpu_count // 2`.
6. **Cache scope-level data** — VIX/indices/macro identical across tickers; TTL-cache.
7. **Throttle expensive I/O** (>100ms) — finalize batch or time-interval.
8. **No OHLCV full scans** (1.5M rows). `ROW_NUMBER() OVER (PARTITION BY ticker)` or `WHERE ticker IN (...)` + date filter.

### 4.2 Code style

9. Line 79 chars (black/isort/flake8).
10. No bare `print()` — `_logger = logging.getLogger(__name__)`. Never log secrets at INFO. **Caught exceptions in long-running jobs MUST log with `exc_info=True`**.
11. `X | None` not `Optional[X]` (PEP 604).
12. No module-level mutable globals (exception: `_logger`).
13. No bare `except:` — `except Exception` or specific.
14. `apiFetch` not bare `fetch` (auto-refreshes JWT).
15. `<Image />` not `<img>` (ESLint).
16. Patch at SOURCE module, not importer. → `mock-patching-gotchas`

### 4.3 Data & writes

17. Iceberg writes MUST propagate errors — never silence.
18. **Scoped deletes** — `In("ticker", batch)` not `EqualTo("score_date")`.
19. Indian stocks `.NS`; use `detect_market` from `market_utils.py`. Never local suffix checks.
20. **NEVER `rm` Iceberg metadata/parquet** — use `overwrite()` / `delete_rows()` API or `cleanup_orphans_v2()`. → `iceberg-orphan-sweep-design`
21. **New write-heavy Iceberg table → enroll in BOTH** `_HOT_ICEBERG_TABLES` (`backend/jobs/executor.py`) AND `ALL_TABLES` (`backend/maintenance/iceberg_maintenance.py`) — same PR as DDL. → `iceberg-maintenance-enrollment`
22. **★ Iceberg table storage design — universal checklist** (every new table, no exceptions). → `iceberg-table-design-checklist`
    a. **Partition spec** — NEVER `IdentityTransform` on a column with cardinality > 50 (ticker, user_id, session_id). Use `BucketTransform(N)` (N ∈ {8, 16, 32}). For ticker × time data the canonical spec is `BucketTransform(16, ticker) + MonthTransform(date)` → ~192 partitions/year.
    b. **Time grain** — `MonthTransform` on a `DateType` column for any table writing > 100 commits/day. `DayTransform` only for daily-aggregate tables. `IdentityTransform` on a `StringType("YYYY-MM-DD")` is forbidden (defeats Iceberg date pruning).
    c. **Sort order** — declare a `SortOrder` on `(bucket_or_partition_key, primary_filter, timestamp)` at create time. Drives compaction layout + predicate pushdown.
    d. **Schema** — `DateType` for date columns (not `StringType("YYYY-MM-DD")`); primitives only; tz-naive `TimestampType` (strip tz before write).
    e. **1-year file budget** — estimate `writes/day × distinct_partitions × 365`; reject the spec if projected active file count > 5,000.
    f. **Enrollment in same PR as DDL** — write-heavy (≥ 10 commits/day): `_HOT_ICEBERG_TABLES` + `ALL_TABLES` + the writing pipeline's scoped maintenance payload. Low-write (< 10 commits/day): `ALL_TABLES` + Weekly Long-Tail Iceberg Maintenance pipeline.
    g. **Type evolution is one-way** — `StringType` cannot become `DateType` post-hoc without a nuke-rebuild. Get types right on day 1.

### 4.4 Process & git

23. Branch off `dev`; never push to `dev`/`qa`/`release`/`main`. Hotfix: branch off `main`, PR to `main`, sync DOWN. Keep feature branch until sprint history stale.
24. Co-Authored-By: `Abhay Kumar Singh <asequitytrading@gmail.com>`.
25. Update `PROGRESS.md` per session (dated); `git add .serena/` before push. Doc triggers: `docs/` for API changes · new Serena memory per new pattern · `README.md` env-vars table for new config · `stocks/create_tables.py` + `docs/` for new Iceberg table · this file for new regression-preventing rule.
26. Test-after-feature — write immediately after smoke test passes; happy + 1 error path minimum.
27. **PR merge on `dev`: squash only** (merge-commit + rebase blocked).
28. Jira 3-phase: create → In Progress → comment+Done. Both `customfield_10016` (numeric) + `customfield_10036` (string) for story points. → `jira-3phase-lifecycle`

### 4.5 Infra & config

29. `NEXT_PUBLIC_BACKEND_URL=http://localhost:8181` — never `127.0.0.1` (cookie mismatch).
30. `BACKEND_URL=http://backend:8181` on dev frontend container (RSC fetches).
31. **`API_URL` for all API calls** (mounted under `/v1/`); `BACKEND_URL` for static + WS. WS `/ws/chat` NOT versioned. → `api-versioning`
32. No `@traceable` on `FallbackLLM.invoke()` — breaks LangChain tool-call parsing.
33. `scheduler_catchup_enabled=False` default (startup catchup pulled mid-day partial data).
34. After cache-touching code change: `redis-cli FLUSHALL`.

---

## 5. Patterns — ALWAYS follow

### 5.1 Backend

- **Iceberg vs PG**: mutable → PG; append-only → Iceberg. → `db-table-inventory`
- **Iceberg writes**: bulk + scoped delete (`In("ticker", batch)`). NaN-replaceable upsert: filter dedup to non-NaN AND scoped pre-delete NaN rows for incoming keys. → `iceberg-nan-replaceable-dedup`
- **Chat-tool freshness**: check Iceberg first; yfinance only if stale (7-day window). → `iceberg-freshness-checks`
- **Tools return error strings; routes raise `HTTPException`** — `@tool` functions: `return f"Error: {exc}"`; FastAPI: `raise HTTPException(...)`.
- **`_pg_session()` ~2-5ms/call** — don't use in hot loops. `_run_pg(_call)` (callable, NOT coroutine) from sync threads. `pool_pre_ping=True` mandatory.
- **Scheduler-job PG access** — use `disposable_pg_session()` (NullPool, per-call). Cached `get_session_factory()` binds to uvicorn loop; reusing under `asyncio.run()` raises "Future attached to a different loop". → `pg-nullpool-sync-async-bridge`
- **Iceberg `TimestampType` is tz-naive** — strip tz before write; ISO-8601 `Z` on read via `_iso_utc()`. → `iceberg-tz-naive-timestamps`
- **Per-ticker refresh = 6 steps**: OHLCV → company_info → dividends → technical → quarterly → Prophet. Status endpoint MUST invalidate `cache:dash:*`, `cache:chart:*:{t}`, `cache:insights:*`. → `per-ticker-refresh`, `ohlcv-freshness-gate`
- **ContextVar through worker thread**: `run_in_executor` does NOT copy ContextVars. Set INSIDE worker via `apply_byo_context`. Post-chat side-effects run inside same `with`. → `contextvar-run-in-executor`
- **Sync I/O in async routes**: wrap with `asyncio.to_thread()`.
- **Pipeline step pattern**: fail-closed step 0 backup before destructive maintenance. → `iceberg-daily-pipeline-compaction`, `pipeline-chaining-dag`, `pipeline-quality-assertions`
- **Iceberg schema evolution requires backend restart** (in-process DuckDB caches old schema). → `backend-restart-triggers`
- **DuckDB read-after-write**: `invalidate_metadata()` after every Iceberg write. Under concurrent writes prefer `tbl.refresh().scan(filter)` over DuckDB filesystem-glob.

### 5.2 Chat agent

- Cascade routing, sentiment, tool-result truncation, hallucination guardrail, model pinning, TokenBudget, FallbackLLM/`bind_tools`, cascade profiles, sub-agent message regimes, tool-forcing, WebSocket events → `.claude/rules/chat-agent.md` (path-scoped; auto-loads on `backend/agents/**` + `llm_fallback.py`/`token_budget.py`/`ws.py`/agent tools).

### 5.3 Frontend

- **Data fetch**: ALWAYS `apiFetch` + SWR hook in `frontend/hooks/`. Never raw `useEffect + fetch`. 2-min dedup, `revalidateOnFocus: false`. → `swr-data-fetch-pattern`
- **Authenticated route SSR**: RSC + cookie auth + `serverApiOrNull` + SWR `fallbackData`. `proxy.ts` (Next 16 middleware rename) accepts either `access_token` or `refresh_token` cookie. → `cookie-auth-rsc-pattern`
- **ECharts theme**: `useDarkMode` (MutationObserver on `<html>` class) NOT `useTheme()`. `notMerge={true}` + `key={isDark ? "d" : "l"}`. Tree-shake: register only used types in `frontend/lib/echarts.ts` (200KB vs 800KB). → `echarts-theme-hydration`, `portfolio-analytics`
- **TradingView theme**: `useDomDark(isDarkProp)` from `components/charts/useDarkMode.ts`. → `ssr-hydration-mismatches`
- **Currency**: `tickerCurrency(ticker)` helper. Never hardcode `$`.
- **SSR safety**: localStorage in `useEffect`; `crypto.randomUUID` guarded by `typeof window`; explicit locale in `toLocaleString`.
- **`<span>` not `<div>` inside `<p>`** (hydration).
- **Loading shells need text/img/svg** (Lighthouse FCP doesn't fire on pure-CSS divs). → `lighthouse-fcp-text-heuristic`
- **LCP anti-pattern**: top-level `if (loading) return <Skeleton/>` over prop-driven hero text hides LCP candidate. Render structure with `?? 0` placeholders; keep inner gate for conditional charts / wide cells / heatmaps. → `loading-gate-lcp-anti-pattern`
- **`<Suspense fallback={null}>` blanks SSR** when subtree calls `useSearchParams`. Replace with `<h1>` + `min-h-[Npx]` mirror. → `suspense-fallback-null-ssr-hole`
- **React effects** must satisfy `react-hooks/set-state-in-effect`: defer synchronous setState via `queueMicrotask` + cancellation flag, OR use `useState` lazy initializer. Avoid impure calls (`Date.now()`) in render — track in state with interval tick.
- **Sign Out** MUST POST `/v1/auth/logout` BEFORE `clearTokens()` — proxy.ts edge gate accepts either cookie. Canonical: `AppHeader.handleSignOut`, `ChatHeader.handleSignOut`. Wrap try/catch.

### 5.4 ★ Tabular pages (Insights, Admin)

- **Every new table/list page (catalog ≥ 8 cols) MUST use** `useColumnSelection` + `<ColumnSelector>` + `<DownloadCsvButton>` (shared CSV/visible-cols filter), server-side pagination if `total > 200`, column-header sort, locked ticker column.
- Full checklist + references → `.claude/rules/tabular-pages.md` (path-scoped; auto-loads on insights/admin/analytics table components).

### 5.5 ★ Stale-data transparency chip

Aggregating N entities with stale inputs → amber chip in panel title with hover tooltip. Backend: `stale_tickers: list[StaleTicker]` or `unanalyzed_tickers: list[str]`. Frontend: auto-clears when empty. Reference: `PLTrendWidget::StaleTickerChip`. → `portfolio-pl-stale-ticker-chip`

### 5.6 ★ Modals

z-index ladder: slideovers `z-[60]` · modals `z-[70]` · tooltips/popovers `z-[80]` · toasts `z-[90]`.

Cross-page portfolio modals mounted ONCE in `(authenticated)/layout.tsx` via `PortfolioActionsProvider`. Dispatch via `usePortfolioActions()`. NEVER route-redirect to open a modal.

View-first edit-from-within: eye icon → view modal; edit pencil INSIDE view modal per-row. Confirm-modal DELETE handlers MUST treat 404 as success alongside 204. → `modal-stacking-pattern`, `portfolio-management`, `portfolio-watchlist-sync`

### 5.7 ★ Admin scope-aware (pro vs superuser)

- `?scope=self|all` (pro forced `self`, superuser `all`), `TabDef.roles` tab filtering, tier→role auto-sync, audit event vocabulary (new events MUST be enum + test) → `.claude/rules/admin.md` (path-scoped; auto-loads on `auth/endpoints/admin_routes.py` + admin frontend).

### 5.8 Recommendation engine

- Monthly quota, run_type, acted-on detect, 14-mo retention, /performance cohorts, outcomes job → `.claude/rules/recommendation.md` (path-scoped; auto-loads on `backend/recommendation_*.py` + engine).

### 5.9 Insights ticker scoping (3-tier)

`insights_routes.py::_scoped_tickers(user, scope)`. Scope ∈ `{discovery, watchlist, portfolio}`:

| Tab → scope | Who sees what |
|---|---|
| `discovery` (Screener, ScreenQL, Sectors, Piotroski) | Pro/superuser: full universe (`stock`+`etf`); General: watchlist ∪ holdings |
| `watchlist` (Risk, Targets, Dividends) | Watchlist ∪ holdings |
| `portfolio` (Correlation, Quarterly) | Holdings only (`quantity > 0`) |

Full-universe filter: `ticker_type IN ('stock', 'etf')`. Per-user cache key MUST include `user_id`.

### 5.10 Forecast pipeline

- Vol-regime Prophet config, log-transform, technical bias, confidence score, sanity gates, backtest → `.claude/rules/forecast.md` (path-scoped; auto-loads on `backend/tools/*forecast*.py`).

### 5.11 Payments (Razorpay INR + Stripe USD)

- Tier-from-Iceberg-not-JWT, mandatory webhook sig verify, PATCH-not-cancel upgrades, `_safe_update` retries, payment_transactions ledger → `.claude/rules/payments.md` (path-scoped; auto-loads on `auth/**subscription**` + razorpay/stripe files).

### 5.12 Chat memory layer (pgvector)

- **Write**: async fire-and-forget from WS worker via `asyncio.run_coroutine_threadsafe()`. Skip responses <50 chars.
- **Read**: sync before `graph.invoke()` — top-5 cosine, 3s timeout, `[Memory context]` block in prompt.
- **Embeddings**: Ollama `nomic-embed-text` (768 dim). Falls back to `ConversationContext.summary` when Ollama down.
- **ConversationContext**: dual-layer (in-memory + PG via `ConversationContextStore.upsert()` SYNCHRONOUS — daemon thread fails inside uvicorn).
- Cross-session resume via `get_latest_for_user(user_id)`.

→ `memory-augmented-chat`, `conversation-context-persistence`

### 5.13 ★ Redis caching

Every new endpoint returning Iceberg-derived data:
- **TTL constants**: `TTL_VOLATILE=60` (per-user), `TTL_STABLE=300` (charts, insights), `TTL_ADMIN=30`. Don't invent new TTLs.
- **Key schema**: `cache:<area>:<endpoint>:<scope>` (e.g. `cache:dash:home:{user_id}`). Per-user keys MUST include `user_id`.
- **Write-through invalidation**: every Iceberg write through `_retry_commit()` calls `_invalidate_cache(table)` via `_CACHE_INVALIDATION_MAP`. New Iceberg table → add map entry.
- **Pattern**: `cache.get(key)` → return; else compute, `cache.set(key, json, TTL_*)`, return.
- **kwarg is `ttl`** NOT `ex` (silent `TypeError`). `cache.invalidate(pattern)` glob; `cache.invalidate_exact(*keys)` exact.
- No-op when `REDIS_URL` empty (graceful). → `redis-cache-layer`

### 5.14 ★ E2E (Playwright)

- **Every new interactive element MUST have `data-testid`** (frontend-authoring rule; e2e specs depend on it).
- POM, testid registry, auth fixtures, locator scoping, no-`networkidle`, workers, maxFailures → `.claude/rules/e2e.md` (path-scoped; auto-loads on `e2e/**`).

### 5.15 ★ Performance budgets

Pre-PR `npm run perf:check` (LHCI on /login) soft gate; full audit via 34-route containerized Lighthouse (§7) before major frontend ship.

**Iterating on LCP fixes**: save `pw-lh-summary.json` per cycle, diff LCP **and** CLS (every gate removal can spike CLS — verify ≤ 0.02). Phase 0: read LCP element + phase breakdown from per-route JSON before fixing. `Render Delay = 100% of LCP` ≠ "chart paints late". → `loading-gate-lcp-anti-pattern`, `suspense-fallback-null-ssr-hole`

| Bucket | Perf | LCP | CLS |
|---|---:|---:|---:|
| `/`, `/login`, `/auth/oauth/callback` | ≥ 90 | ≤ 2.0–2.5 s | ≤ 0.1 |
| `/dashboard`, `/analytics` | ≥ 80 | ≤ 2.5 s | ≤ 0.1 |
| `/analytics/*`, `/insights` | ≥ 75 | ≤ 3.0 s | ≤ 0.1 |
| `/admin` | ≥ 70 | ≤ 3.5 s | ≤ 0.1 |
| `/docs` | ≥ 85 | ≤ 2.0 s | ≤ 0.1 |
| All pages | TBT ≤ 200 ms | — | ≤ 0.02 |

Hard: JS < 500 KB gzipped/route · mobile baseline (4× CPU, slow 4G) · heavy chart libs via `next/dynamic({ssr:false})` · `<Suspense>` around chart Client Components in RSC migration · `react-markdown` lazy.

→ `lighthouse-performance-workflow`, `auth-layout-ssr-unlock`, `lighthouse-runner-gotchas`

### 5.16 Algo trading

- Strategy promotion, gates, picker filters, dry-run rules → `.claude/rules/algo.md` (path-scoped; auto-loads on `backend/algo/**` + algo frontend).

---

## 6. Bug-Fix Patterns

### 6.1 NaN

- **String sentinels** `"NaN"`/`"None"`/`"null"`/`"N/A"`/`"na"`/`"NaT"` are truthy. Use `safe_str`/`safe_sector` from `market_utils.py`.
- **Arithmetic propagation**: `val += qty * NaN` → NaN; `NaN > 0` is False. Guard with `math.isnan`.
- **`val or default`** broken for pandas numerics — use `_safe_float(val)`. → `nan-handling-iceberg-pandas`
- **Iceberg dedup**: NaN row blocks re-fetch. Filter dedup to non-NaN AND scoped pre-delete NaN. → `iceberg-nan-replaceable-dedup`
- **NaN→PG sanitize** before insert (PG rejects NaT/NaN).
- **PyArrow `pa.string()` rejects NaN** — error: "Expected bytes, got a 'float' object". Sanitise at every `pa.table(...)` boundary.

### 6.2 Backend restart triggers (uvicorn --reload isn't enough)

| Change | Action |
|---|---|
| New `@router.get/post/...` decorator | `restart` |
| New field on `response_model` class | `restart` |
| New `app.include_router()` | `restart` |
| New `@register_job(...)` | `restart` |
| Iceberg `add_column()` | `restart` + Redis FLUSHALL |
| New env var in `.env` | `up -d --force-recreate backend` |
| Renamed Alembic migration | edit `revision:` + clear `__pycache__/*.pyc` + `restart` |
| `Dockerfile.backend` / `requirements.txt` | `compose build backend` + `up -d` |

After restart, sleep 5s before auth-dependent calls (asyncpg shutdown race). → `backend-restart-triggers`

### 6.3 Cookie hostname

`localhost` ≠ `127.0.0.1` for cookies. Use `localhost` in `NEXT_PUBLIC_BACKEND_URL`. Logout clears at `/`, `/auth`, `/v1/auth`. → `cookie-hostname-mismatch`

### 6.4 Iceberg / DuckDB

- `invalidate_metadata()` after every Iceberg write (already in `_retry_commit()`).
- Concurrent writes: use `tbl.refresh().scan(filter)` (PyIceberg) over DuckDB filesystem-glob.
- **Backup BEFORE maintenance** — `run_backup()` mandatory step 0 (fail-closed).
- **NEVER `rm` metadata/parquet** — SQLite catalog stores absolute paths. → `iceberg-table-corruption-recovery`
- `ObservabilityCollector` flushes 30s — restart loses unflushed. Seed on startup.
- **`cleanup_orphans_v2(dry_run=True)` misleading** — `dry_run` only gates *file deletion*; snapshot expiry commits regardless. Run with `skip_backup=False` (default). First run 5-15 min; subsequent ~20s.
- **Commit conflicts** under concurrent writers (`Requirement failed: branch main has changed`): wrap writes in `retry_iceberg_op()` with backoff. Watch for cross-pipeline writers (keeper + retention + maintenance) competing on same table.

### 6.5 yfinance / data

- **Pre-market flat candles** (Indian 08:00 IST): O=H=L w/ NaN close. Delete + refetch.
- **Bulk download**: `yf.download()` batches of 100 (99.8% vs 56%). `^`-indices fail in bulk — fetch separately.
- **Sectors casing**: `"Technology"` not `"IT"`, `"Financial Services"` not `"Financials"`.
- **jugaad-data timeout**: `NseSource` wraps in `asyncio.wait_for(timeout=60.0)`.
- **Yahoo `^BSESN` freezes mid-session** (~10 min after open). `_is_yahoo_quote_stale()` falls back to Google Finance (`SENSEX:INDEXBOM`).
- **Per-source 10s timeout** in sentiment fetchers (`yf.Ticker().news` deadlocks pool).
- **Pre-1980 dates** corrupt yfinance (`date=1970-01-01`). Backend filter `df[df["date"] >= "1980-01-01"]`; frontend regex `/^(19[89]\d|2\d{3})-/`. → `iceberg-epoch-dates`

### 6.6 Frontend hydration

- `<div>` inside `<p>` → hydration error.
- Mount-gate (`if (!mounted) return <Spinner/>`) in layout floors LCP. Audit providers for SSR safety. → `auth-layout-ssr-unlock`
- **LCP regression**: Render Delay = 100% w/ FCP healthy → loading-gate hides hero text, OR empty SSR HTML → `<Suspense fallback={null}>`. → `loading-gate-lcp-anti-pattern`, `suspense-fallback-null-ssr-hole`
- **Sign Out bounces to /dashboard** — must POST `/v1/auth/logout` before `clearTokens()`. See §5.3.
- Modal z-index opened from slideover MUST be `z-[70]`.
- React `set-state-in-effect` rule: see §5.3 (queueMicrotask pattern).

### 6.7 Sync→async migration

Coroutines returned silently — no compile-time error.
- **Missing `await`** on async repo methods — grep `repo\.` across codebase.
- **Test mocks**: `AsyncMock` not `MagicMock` for async repos.
- **Sync→async PG**: pass callable not coroutine (`_run_pg(_call)`). Use `_pg_session()`/`disposable_pg_session()` NOT `get_session_factory()` (loop-bound).
- **`threading.local()`** across executor boundaries: set INSIDE worker closure.
- **`pool_pre_ping=True`** mandatory in `create_async_engine()`.

→ `sync-async-migration-patterns`, `asyncpg-sync-async-bridge`

---

## 7. Quick Reference

```bash
# Lint
black backend/ auth/ stocks/ scripts/ && \
isort backend/ auth/ stocks/ scripts/ --profile black && \
flake8 backend/ auth/ stocks/ scripts/
cd frontend && npx eslint . --fix

# Test
python -m pytest tests/ -v
cd frontend && npx vitest run
cd e2e && npx playwright test --project=frontend-chromium  # ~3 min, 1 worker

# Migrations / seed
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. alembic revision --autogenerate -m "desc"
docker compose exec backend python scripts/seed_demo_data.py

# Alembic stale bytecode
docker compose exec backend rm -f /app/backend/db/migrations/versions/__pycache__/*.pyc

# Stock pipeline (PYTHONPATH=.:backend python -m backend.pipeline.runner …)
download | seed --csv … | bulk-download | fill-gaps | status
analytics --scope india | sentiment --scope india | forecast --scope india
screen | refresh --scope india --force | recommend

# Performance — containerized 34-route Lighthouse
docker compose --profile perf build frontend-perf
docker compose --profile perf up -d postgres redis backend frontend-perf
docker compose --profile perf run --rm perf   # ~15-20 min
# Output: frontend/.lighthouseci/pw-lh-summary.json
```

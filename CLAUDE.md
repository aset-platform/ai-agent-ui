# CLAUDE.md — AI Agent UI

> Slim rules. New features/bug fixes **MUST** follow §4 (hard) +
> §5 (patterns). Detail lives in Serena memories — referenced
> inline as `→ memory-name`. Pattern Index at §9.

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

`./run.sh start|stop|restart [svc]|rebuild [svc]|status|logs <svc> [-f]|doctor` — see §8 for rebuild commands.

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

### 4.4 Process & git

22. Branch off `dev`; never push to `dev`/`qa`/`release`/`main`.
23. Co-Authored-By: `Abhay Kumar Singh <asequitytrading@gmail.com>`.
24. Update `PROGRESS.md` per session (dated). `git add .serena/` before push.
25. Test-after-feature — happy + 1 error path minimum.
26. **PR merge on `dev`: squash only** (merge-commit + rebase blocked).
27. Jira 3-phase: create → In Progress → comment+Done. Both `customfield_10016` (numeric) + `customfield_10036` (string) for story points. → `jira-3phase-lifecycle`

### 4.5 Infra & config

28. `NEXT_PUBLIC_BACKEND_URL=http://localhost:8181` — never `127.0.0.1` (cookie mismatch).
29. `BACKEND_URL=http://backend:8181` on dev frontend container (RSC fetches).
30. **`API_URL` for all API calls** (mounted under `/v1/`); `BACKEND_URL` for static + WS. WS `/ws/chat` NOT versioned. → `api-versioning`
31. No `@traceable` on `FallbackLLM.invoke()` — breaks LangChain tool-call parsing.
32. `scheduler_catchup_enabled=False` default (startup catchup pulled mid-day partial data).
33. After cache-touching code change: `redis-cli FLUSHALL`.

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
- **Per-ticker refresh = 6 steps**: OHLCV → company_info → dividends → technical → quarterly → Prophet. Status endpoint MUST invalidate `cache:dash:*`, `cache:chart:*:{t}`, `cache:insights:*`. → `per-ticker-refresh`
- **ContextVar through worker thread**: `run_in_executor` does NOT copy ContextVars. Set INSIDE worker via `apply_byo_context`. Post-chat side-effects run inside same `with`. → `contextvar-run-in-executor`
- **Sync I/O in async routes**: wrap with `asyncio.to_thread()`.
- **Pipeline step pattern**: fail-closed step 0 backup before destructive maintenance. → `iceberg-daily-pipeline-compaction`
- **Iceberg schema evolution requires backend restart** (in-process DuckDB caches old schema). → `backend-restart-triggers`
- **DuckDB read-after-write**: `invalidate_metadata()` after every Iceberg write. Under concurrent writes prefer `tbl.refresh().scan(filter)` over DuckDB filesystem-glob.

### 5.2 Chat agent

- **Cascade routing**: chat = BYO + platform fallback; batch (recommendations/sentiment/forecast) = platform-only; superusers always platform. → `byom-cascade-override`
- **Sentiment**: `finbert` → ProsusAI (CPU, free) for batch; LLM cascade only for chat or FinBERT failure.
- **Tool-result truncation**: `max_tool_result_chars=4000` (pass 1), 2500 (2), 1500 (3). Synthesis prompt must include "NO HALLUCINATION ON TRUNCATION". → `llm-truncation-hallucination`
- **Hallucination guardrail**: `_is_hallucinated()` rejects 3+ stock patterns with zero `tool_done` events.
- **Groq tool-call ID sanitization** before Anthropic fallback (`_sanitize_tool_ids()`).
- **Per-request model pinning**: `_pinned_model` locks after first invoke. Budget exhaust → compress, unpin, cascade. `pin_reset()` per ReAct loop.
- **TokenBudget**: atomic `reserve()`/`release()` (NOT `can_afford()`/`record()` — TOCTOU). Singleton via `get_token_budget()`; Iceberg seed on restart.
- **`bind_tools` rebuild lookup**: `_model_lookup` must rebuild after `FallbackLLM.bind_tools()`.
- **`max_retries=0` on `ChatGroq`** — SDK retries caused 45-56s pre-cascade delays.
- **Iteration counter** MUST flow into `FallbackLLM.invoke(messages, *, iteration=...)` — else progressive compression never engages.
- **Cascade profile at startup**: `tool` (loop) vs `synthesis` (final) vs `test` (no Anthropic). Route to synthesis cascade after first tool iteration. → `groq-chunking-strategy`, `llm-cascade-profiles`
- **New agent class**: subclass `BaseAgent`, override `format_response()`. Attributes used in `_build_llm()` MUST exist BEFORE constructor. → `agent-init-pattern`
- **Sub-agent message construction** — 3 regimes: (1) first = `prompt+query`; (2) same-intent follow-up = `prompt+summary+query`; (3) **intent switch = `prompt+query` only** (no history → no cross-intent contamination). → `summary-based-context`
- **Chat tool fetching new ticker** MUST call `_ensure_stock_master(ticker, info)`. → `stock-master-auto-insert`
- **Chat clarification gate**: `?` ending bypasses keyword gate.
- **Currency-aware prompt**: `_build_context_block()` injects portfolio currency mix.
- **Tool-forcing prompts**: directive ("YOUR FIRST RESPONSE MUST ONLY be a tool call"). → `llm-tool-forcing`
- **WebSocket**: auth-first handshake; events `thinking`/`tool_start`/`tool_done`/`warning`/`final`/`error`/`timeout`; close codes 4001/4002/4003. `_handle_chat` MUST send `error` + `final` events (not just return queue). → `streaming-protocol`

### 5.3 Frontend

- **Data fetch**: ALWAYS `apiFetch` + SWR hook in `frontend/hooks/`. Never raw `useEffect + fetch`. 2-min dedup, `revalidateOnFocus: false`. → `swr-data-fetch-pattern`
- **Authenticated route SSR**: RSC + cookie auth + `serverApiOrNull` + SWR `fallbackData`. `proxy.ts` (Next 16 middleware rename) accepts either `access_token` or `refresh_token` cookie. → `cookie-auth-rsc-pattern`
- **ECharts theme**: `useDarkMode` (MutationObserver on `<html>` class) NOT `useTheme()`. `notMerge={true}` + `key={isDark ? "d" : "l"}`. Tree-shake: register only used types in `frontend/lib/echarts.ts` (200KB vs 800KB). → `echarts-theme-hydration`
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

Every new table/list page (catalog ≥ 8 cols) MUST use:
- `useColumnSelection(storageKey, defaults, validKeys)` (localStorage, SSR-safe)
- `<ColumnSelector lockedKeys={["ticker"]}/>` (popover, category groups, search, reset)
- **Single source of truth**: `visibleCols = allCols.filter(c ∈ selected)` — CSV export uses SAME filter
- `<DownloadCsvButton rows={sortedRows} cols={visibleCols}/>` next to pagination (not header)
- Server-side pagination if `total > 200`; else client-side, default page size 25
- Column-header sort; locked identity column (ticker); skeleton on load, CTA on empty
- Stale-data chip per §5.5 when aggregate uses ffill

Reference: ScreenerTab, ScreenQLTab, RecommendationHistoryTab, Admin Users. → `tabular-page-pattern`

### 5.5 ★ Stale-data transparency chip

Aggregating N entities with stale inputs → amber chip in panel title with hover tooltip. Backend: `stale_tickers: list[StaleTicker]` or `unanalyzed_tickers: list[str]`. Frontend: auto-clears when empty. Reference: `PLTrendWidget::StaleTickerChip`. → `portfolio-pl-stale-ticker-chip`

### 5.6 ★ Modals

z-index ladder: slideovers `z-[60]` · modals `z-[70]` · tooltips/popovers `z-[80]` · toasts `z-[90]`.

Cross-page portfolio modals mounted ONCE in `(authenticated)/layout.tsx` via `PortfolioActionsProvider`. Dispatch via `usePortfolioActions()`. NEVER route-redirect to open a modal.

View-first edit-from-within: eye icon → view modal; edit pencil INSIDE view modal per-row. Confirm-modal DELETE handlers MUST treat 404 as success alongside 204. → `modal-stacking-pattern`

### 5.7 ★ Admin scope-aware (pro vs superuser)

`?scope=self|all` query param; pro forced to `self` (403 on `all`), superuser defaults to `all`. Applies to `/admin/audit-log`, `/admin/metrics`, `/admin/usage-stats` (guard: `pro_or_superuser`). Other ~45 endpoints: `superuser_only`.

`TabDef.roles: Role[]` filters admin tab strip. Pro = 3-tab (`my_account`, `my_audit`, `my_llm`). General → `/dashboard`.

Tier→role auto-sync: `free→general`, `pro|premium→pro`, superuser sticky. Fires `ROLE_PROMOTED`/`ROLE_DEMOTED`. Frontend calls `refreshAccessToken()` after subscription writes.

**Audit event vocabulary**: `LOGIN`, `OAUTH_LOGIN`, `PASSWORD_RESET`, `USER_CREATED/UPDATED/DELETED`, `ADMIN_PASSWORD_RESET`, `ROLE_PROMOTED/DEMOTED`, `BYO_KEY_ADDED/UPDATED/DELETED`. New events MUST be enum + test. → `pro-user-role-scoped-admin`, `observability`

### 5.8 Recommendation engine

- Monthly-per-scope quota: 1 run per `(user, scope, IST month)` via `get_or_create_monthly_run`. ALL entry points (widget/chat/scheduler) MUST route through it.
- `run_type ∈ {manual, chat, scheduled, admin, admin_test}`. User reads filter `admin_test` via `exclude_test=True`.
- Acted-on auto-detect: portfolio CRUD fires daemon thread → `update_recommendation_status`.
- Scope-aware: `/stats`, `/history`, `/performance` take `?scope=india|us|all`. `total_acted_on` derives from `acted_on_date`. `expire_old_recommendations` IS scope-aware. → `recommendation-engine`
- **Retention: 14 months hard cap.** Daily `recommendation_cleanup` (03:00 IST) deletes `stocks.recommendation_runs` where `run_date < CURRENT_DATE - INTERVAL '14 months'`. FK CASCADE wipes children.
- **`/performance`** = cohort-bucketed (week/month/quarter IST-truncated) × `recommendation_outcomes`. Granularity → primary horizon (weekly→7d, monthly→30d, quarterly→90d). Hit-rate uses `excess_return_pct > 0`. `pending_count` is horizon-aware — surface via §5.5 amber chip.
- **Outcomes job**: 4 horizons {7,30,60,90}, self-healing via `id.notin_(existing)`. Computes return at close on `created_at + N days` (next trading day if weekend, ±6d scan). ⚠ `benchmark_return_pct` hardcoded 0 (TODO); `price_at_rec` not always populated.

### 5.9 Insights ticker scoping (3-tier)

`insights_routes.py::_scoped_tickers(user, scope)`. Scope ∈ `{discovery, watchlist, portfolio}`:

| Tab → scope | Who sees what |
|---|---|
| `discovery` (Screener, ScreenQL, Sectors, Piotroski) | Pro/superuser: full universe (`stock`+`etf`); General: watchlist ∪ holdings |
| `watchlist` (Risk, Targets, Dividends) | Watchlist ∪ holdings |
| `portfolio` (Correlation, Quarterly) | Holdings only (`quantity > 0`) |

Full-universe filter: `ticker_type IN ('stock', 'etf')`. Per-user cache key MUST include `user_id`.

### 5.10 Forecast pipeline

- Vol regime: stable (<30%), moderate (30-60%), volatile (≥60%) — different Prophet config per regime.
- Log-transform for moderate/volatile (`np.log(y)` fit, `np.exp(yhat)` after — non-negative guarantee).
- Technical bias: RSI/MACD/volume dampen forecast ±15%, taper 30d.
- 5-component confidence score (direction/MASE/coverage/interval/completeness). <0.25 rejected.
- Sanity gates: log-transform exp cap (`np.exp(last_log_y ± 1.5)`, max 4.5×); >200% deviation → series skip.
- Run dedup: `computed_at` (UTC ts), NOT `run_date`.
- Backtest: `horizon_months=0`, actual in `lower_bound`. → `forecast-enrichment-sanity-gates`

### 5.11 Payments (Razorpay INR + Stripe USD)

- **Read tier from Iceberg via `repo.get_by_id()`, NOT JWT** (stale cache). Same for `subscription_status`.
- **Webhook signature verification MANDATORY** — fail-closed 503 if secret missing. `_plan_id_to_tier()` returns `None` for unknown plans.
- **Upgrades use PATCH not cancel+create** — Razorpay `subscription.edit` w/ `schedule_change_at="now"`; Stripe `Subscription.modify`. Cancel+create makes orphan subs.
- Concurrent writes: `_safe_update()` retries 3× on `CommitFailedException`.
- Every payment writes to `auth.payment_transactions` ledger (`event_type` + `tier_before/after` + raw payload).

→ `subscription-billing`, `payment-transaction-ledger`, `razorpay-integration-gotchas`, `security-hardening-patterns`

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

Every new interactive element MUST have `data-testid`. Every new page test MUST use Page Object Model.

- Testid registry: `e2e/utils/selectors.ts` `FE` object. Pattern `component-element`. NEVER hardcode in spec.
- POM: extend `BasePage` (`e2e/pages/frontend/`). Use `this.tid(FE.name)`.
- Auth fixtures: storage state pre-loaded for `general-user`, `superuser`. NEVER call `/auth/login` in spec (rate-limit). `auth.setup.ts` captures Set-Cookie + rewrites domain. `e2e/.auth/*.json` cookies need access+refresh tokens with `domain: "localhost"`. → `playwright-cookie-fixture`
- Locator scoping: chat tests use `[data-testid="chat-panel"]` scope.
- NEVER `networkidle` (dashboard polls 30s + WS). Use element waits.
- Below-fold: `waitFor({state: "attached"})` then `scrollIntoViewIfNeeded()`.
- Strict mode: `/^cancel$/i` not `/cancel|close/i` (matches both Cancel + Close X).
- Workers: 1 local, 2 CI. NEVER raise (>3 starves Docker).
- `maxFailures: 10` local cap; `--max-failures=0` for tech-debt sweeps.

→ `e2e-test-patterns`, `test-isolation-gotchas`

### 5.15 ★ Performance budgets

Pre-PR `npm run perf:check` (LHCI on /login) soft gate; full audit via 34-route containerized Lighthouse (§8) before major frontend ship.

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

### 5.16 Strategy promotion workflow (algo trading)

- Lifecycle: `draft → paper → live`. Audit table `algo.strategy_mode_transitions`.
- **Gates** (enforced by `promotion.check_eligibility`): draft→paper requires fresh completed backtest + walkforward (`started_at >= strategies.updated_at`); paper→live requires fresh paper-fill events in `algo.events` (paper runtime doesn't create algo.runs rows).
- **AST edits auto-demote** non-draft → draft (audit row `reason="auto-demoted on AST edit"`).
- **Bypass to live** available only when strategy has prior `to_mode='live'` in history (earned re-promotion); requires typed-name confirmation + reason on audit row.
- **Picker filters** mode-strict: Backtest = all 3 modes; Paper = paper-only; Dry-run = paper-only; Live = live-only.
- **Dry-run mode=dryrun + source=replay** does NOT require Kite creds or live_orders_enabled caps (rehearsal step BEFORE live setup).

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

## 7. Process

- **Git**: branch off `dev`; squash-only merge; Co-Authored-By Abhay; `git add .serena/` before push; keep feature branch until sprint history no longer needed; hotfix: branch off `main`, PR to `main`, sync DOWN.
- **Jira**: 3-phase (create→In Progress→comment+Done). Story points: BOTH `customfield_10016` + `customfield_10036`. → `jira-3phase-lifecycle`
- **Tests**: write immediately after smoke test passes. Happy + 1 error path minimum.
- **Doc triggers**: `PROGRESS.md` every session · `docs/` for API changes · new Serena memory for new pattern · `README.md` env-vars table for new config · `stocks/create_tables.py` + `docs/` for new Iceberg table · this file for new regression-preventing rule.

---

## 8. Quick Reference

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

---

## 9. Pattern Index

When doing X, read §N + memory M first.

| Doing… | § | Memory |
|---|---|---|
| Add a tabular page | 5.4 | `tabular-page-pattern` |
| Aggregate w/ stale per-entity inputs | 5.5 | `portfolio-pl-stale-ticker-chip` |
| Add any modal | 5.6 | `modal-stacking-pattern` |
| Add authenticated route | 5.3 / 5.7 | `cookie-auth-rsc-pattern` |
| Add data-fetching hook | 5.3 | `swr-data-fetch-pattern` |
| Add admin tab | 5.7 | `pro-user-role-scoped-admin` |
| Add cached endpoint | 5.13 | `redis-cache-layer` |
| Add E2E test | 5.14 | `e2e-test-patterns` |
| Pre-PR perf check | 5.15 | `lighthouse-performance-workflow` |
| Add Iceberg column | 5.1 / 6.4 | `backend-restart-triggers`, `iceberg-schema-evolution-backend-restart` |
| Add new Iceberg table | 4.3 | `db-table-inventory`, `iceberg-maintenance-enrollment` |
| Add chat tool / sub-agent | 5.2 | `summary-based-context`, `llm-truncation-hallucination`, `iceberg-freshness-checks` |
| Add new agent class | 5.2 | `agent-init-pattern`, `llm-cascade-profiles` |
| Add WebSocket event | 5.2 | `streaming-protocol` |
| Touch LLM cascade | 5.2 | `byom-cascade-override`, `groq-chunking-strategy` |
| Touch ContextVar / BYO | 5.1 / 5.2 | `contextvar-run-in-executor` |
| Touch chat memory | 5.12 | `memory-augmented-chat` |
| Add recommendation entry point | 5.8 | `recommendation-engine` |
| Add forecast field / regressor | 5.10 | `forecast-enrichment-sanity-gates` |
| Add per-ticker refresh entry | 5.1 | `per-ticker-refresh`, `ohlcv-freshness-gate` |
| Add pipeline / chain step | 5.1 | `pipeline-chaining-dag`, `pipeline-quality-assertions` |
| Add scheduler-job async PG | 5.1 | `pg-nullpool-sync-async-bridge` (use `disposable_pg_session`) |
| Touch Iceberg timestamp column | 5.1 / 6.4 | `iceberg-tz-naive-timestamps` |
| Add subscription / webhook endpoint | 5.11 | `subscription-billing`, `security-hardening-patterns` |
| Touch Razorpay flow | 5.11 | `razorpay-integration-gotchas` |
| Add OAuth provider / change JWT | 5.7 | `auth-jwt-flow` |
| Add audit event | 5.7 | `observability`, `security-hardening` |
| Add ECharts chart type | 5.3 | `portfolio-analytics` (tree-shake) |
| Add portfolio CRUD endpoint | 5.6 / 5.8 | `portfolio-management`, `portfolio-watchlist-sync` |
| Strategy promotion / picker filter | 5.16 | (in-file rules) |
| Convert sync → async | 6.7 | `sync-async-migration-patterns`, `asyncpg-sync-async-bridge` |
| Patch a function in tests | 4.2 #16 | `mock-patching-gotchas`, `test-isolation-gotchas` |
| Reclaim Iceberg disk | 4.3 #20 | `iceberg-orphan-sweep-design` |
| Recover Iceberg corruption | 6.4 | `iceberg-table-corruption-recovery` |
| Read NaN-prone numeric column | 6.1 | `nan-handling-iceberg-pandas` |
| Debug LLM "hallucinating" rows | 5.2 | `llm-truncation-hallucination`, `llm-hallucination-guardrail` |
| Debug NaN crash | 6.1 | `nan-string-sentinels`, `iceberg-nan-replaceable-dedup` |
| Debug "field missing from response" | 6.2 | `uvicorn-reload-routes-models-gotcha` |
| Debug random redirect to login | 6.3 | `cookie-hostname-mismatch` |
| Debug Iceberg stale read | 6.4 | `iceberg-schema-evolution-backend-restart` |
| Debug LCP regression | 6.6 | `auth-layout-ssr-unlock`, `loading-gate-lcp-anti-pattern`, `suspense-fallback-null-ssr-hole` |
| Debug ECharts theme | 5.3 | `echarts-theme-hydration` |
| Debug Iceberg metadata bloat | 4.3 #20 | `iceberg-maintenance-enrollment`, `iceberg-orphan-sweep-design` |
| Debug silent-success pipeline | 5.1 | `pipeline-quality-assertions` |
| Debug "Future attached to a different loop" | 5.1 / 6.7 | `pg-nullpool-sync-async-bridge` |
| Sign Out / logout flow | 5.3 / 6.6 | `cookie-auth-rsc-pattern` |
| Open / close Jira ticket | 7 | `jira-3phase-lifecycle` |

All paths: `shared/<category>/<name>`. `list_memories` browses ~170 memories.

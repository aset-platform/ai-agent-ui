# CLAUDE.md — AI Agent UI

> Slim project rules. **New features and bug fixes MUST follow the
> patterns below** — they encode a year of hardening. Detailed
> architecture, table inventories, and historical debugging context
> live in Serena memories (referenced inline as `→ memory-name`).
> Pattern Index at §9.

---

## 1. Session Startup

1. **Serena**: `activate_project ai-agent-ui` (required for memory ops)
2. **Ollama**: SessionStart hook reports status; `ollama-profile coding` if delegating
3. **Superpowers skill**: invoke applicable skill before work (brainstorming, TDD, executing-plans)
4. **SuperClaude**: `/sc:` for git/build/test/analyze/implement/troubleshoot
5. **Branch**: `git checkout dev && git pull && git checkout -b feature/<desc>` — NEVER commit on `dev`/`qa`/`release`/`main`

## 2. MCP Tools

| Server | Purpose |
|---|---|
| Serena | Code analysis, shared memories, symbol nav |
| Ollama | Local LLM delegation (Qwen for code gen) |
| Context7 | Library/framework docs lookup |
| Playwright / Chrome DevTools | Browser automation, perf, screenshots |
| Atlassian (Jira) | Sprint/ticket management |
| Sequential Thinking | Multi-step reasoning |

## 3. Project Overview & Stack

Fullstack agentic chat app: stock analysis, Prophet forecasting (volatility-regime adaptive), FinBERT batch sentiment + XGBoost ensemble, native portfolio dashboard, memory-augmented chat with pgvector retrieval, Razorpay INR + Stripe USD billing, BYO chat keys (Groq / Anthropic) past 10-turn free allowance for non-superusers.

| Service | Port | Entry | Stack |
|---|---|---|---|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x, SQLAlchemy 2.0 async |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19 |
| PostgreSQL | 5432 | Docker | pgvector/pg16 (19 OLTP tables) |
| Redis | 6379 | Docker | Redis 7 Alpine |
| Docs | 8000 | Docker | MkDocs Material 9 |
| Alembic | — | `backend/db/migrations/` | PG schema migrations |

```bash
./run.sh start | stop | restart [svc] | rebuild [svc] | status | logs <svc> [-f] | doctor
docker compose up -d                     # alt to run.sh
docker compose build backend             # after requirements.txt
ollama-profile coding|reasoning|embedding|status|unload
```

DB inventory: 19 PG OLTP + 12 Iceberg OLAP — `→ db-table-inventory`. Data home: `~/.ai-agent-ui/` (override `AI_AGENT_UI_HOME`); paths in `backend/paths.py`.

---

## 4. Hard Rules — NON-NEGOTIABLE

### 4.1 Performance

1. **Batch reads** — single DuckDB `WHERE ticker IN (...)` → pre-load dict. Never N individual Iceberg reads.
2. **Bulk writes** — accumulate in memory, write 1–2 Iceberg commits after the loop. Never per-ticker `_append_rows`.
3. **Iceberg = append-only analytics** — row-level `update` is full table scan + overwrite (~9s). Use PG for mutable state.
4. **NullPool for sync→async PG** — `_pg_session()` uses NullPool. `→ pg-nullpool-sync-async-bridge`
5. **No nested parallelism** — outer `ThreadPoolExecutor` workers must NOT spawn inner `ProcessPoolExecutor`. Prophet CV `parallel=None`. `workers = cpu_count // 2`.
6. **Cache scope-level data** — VIX, indices, macro identical across tickers; TTL-cache.
7. **Throttle expensive I/O** — >100ms costs go to finalize batch or time-interval.
8. **No OHLCV full scans** — 1.5M rows. `ROW_NUMBER() OVER (PARTITION BY ticker)` or `WHERE ticker IN (...)` + date filter.

### 4.2 Code style

9. Line length 79 chars (black/isort/flake8 aligned).
10. No bare `print()` — `_logger = logging.getLogger(__name__)`. Levels: DEBUG/INFO/WARNING/ERROR. Never log secrets/tokens/passwords at INFO.
11. `X | None` not `Optional[X]` (PEP 604).
12. No module-level mutable globals (exception: `_logger`).
13. No bare `except:` — `except Exception` or specific.
14. `apiFetch` not bare `fetch` (auto-refreshes JWT).
15. `<Image />` not `<img>` (ESLint enforced).
16. Patch at SOURCE module, not the importing module. `→ mock-patching-gotchas`

### 4.3 Data & writes

17. Iceberg writes MUST NOT be silenced — let errors propagate.
18. **Scoped deletes** — `In("ticker", batch)` not `EqualTo("score_date")`. Prevents cross-market overwrite.
19. Indian stocks `.NS` everywhere; use `detect_market` from `backend/market_utils.py`. NEVER local suffix checks.
20. **NEVER delete Iceberg metadata/parquet directly** — use `overwrite()` / `delete_rows()` API. Direct `rm` breaks SQLite catalog. The sanctioned reclamation path is `cleanup_orphans_v2()` (PyIceberg 0.11.1 native expire_snapshots + reference-set guard) — `→ iceberg-orphan-sweep-design`.

### 4.4 Process & git

21. Branch off `dev` — NEVER push to `dev`/`qa`/`release`/`main`.
22. Co-Authored-By: `Abhay Kumar Singh <asequitytrading@gmail.com>`.
23. Update `PROGRESS.md` after every session (dated).
24. `git add .serena/` before final push (memories tracked).
25. Test-after-feature — happy + 1 error path minimum.
26. **PR merge on `dev`: squash only.** `gh pr merge <n> --squash` (merge-commit + rebase blocked).
27. Jira 3-phase: create → In Progress → comment+Done. `→ jira-3phase-lifecycle`

### 4.5 Infra & config

28. `NEXT_PUBLIC_BACKEND_URL=http://localhost:8181` — never `127.0.0.1` (cookie hostname mismatch).
29. `BACKEND_URL=http://backend:8181` required on dev frontend container for RSC server fetches.
30. **`API_URL` for all API calls** (mounted under `/v1/`); `BACKEND_URL` only for static assets + WS URL. WebSocket `/ws/chat` NOT versioned. `→ api-versioning`
31. No `@traceable` on `FallbackLLM.invoke()` — breaks LangChain tool-call parsing.
32. Container `TZ=Asia/Kolkata` in `docker-compose.yml` backend; `schedule` lib uses local time.
33. `scheduler_catchup_enabled=False` default — startup catchup pulled mid-day partial data.
34. After cache-touching code change: `redis-cli FLUSHALL`.

---

## 5. Development Patterns — ALWAYS follow

These encode "the one true way" for recurring problems. Deviating creates rework or trust-breaking UX inconsistency.

### 5.1 Backend

- **Iceberg vs PG**: mutable state → PG; append-only analytics → Iceberg. `→ db-table-inventory`
- **Iceberg writes**: bulk + scoped delete (`In("ticker", batch)`), never per-ticker hot loop. NaN-replaceable upsert: filter dedup query to non-NaN AND scoped pre-delete NaN rows for incoming keys before append. `→ iceberg-nan-replaceable-dedup`
- **Chat tool freshness**: every chat tool fetching from external APIs MUST check Iceberg freshness first; only call yfinance if stale. 7-day window covers weekends. `→ iceberg-freshness-checks`
- **Tools return error strings; endpoints raise `HTTPException`** — `try/except` → `return f"Error: {exc}"` for `@tool`-decorated functions; `raise HTTPException(status_code=...)` for FastAPI routes.
- **NullPool sync→async PG**: `_pg_session()` ~2-5ms/call. Don't use in hot loops — batch via DuckDB or bulk PG. `_run_pg(_call)` with callable (NOT coroutine) from sync threads. `pool_pre_ping=True` mandatory in `create_async_engine()`.
- **Iceberg `TimestampType` is tz-naive** — strip `tzinfo` BEFORE write (`v.replace(tzinfo=None)`); emit ISO 8601 UTC with `Z` on read via `_iso_utc()` in `routes.py`. `→ iceberg-tz-naive-timestamps`
- **Per-ticker refresh = 6 steps**: OHLCV (CRITICAL) → company_info → dividends → technical → quarterly → Prophet (CRITICAL). On success, status endpoint MUST invalidate `cache:dash:*`, `cache:chart:ohlcv:{t}`, `cache:chart:indicators:{t}`, `cache:chart:forecast:{t}:*`, `cache:insights:*`. `→ per-ticker-refresh`
- **ContextVar through worker thread**: `loop.run_in_executor` does NOT copy ContextVars. Set INSIDE worker via scoped context manager (`apply_byo_context`); post-chat side-effects must run inside the same `with` block. `→ contextvar-run-in-executor`
- **Sync I/O in async routes**: wrap with `asyncio.to_thread()`. Applies to retention cleanup, `backfill_nan`, `backfill_missing`.
- **Pipeline step pattern**: fail-closed step 0 backup before any destructive maintenance. If backup fails, abort. `→ iceberg-daily-pipeline-compaction`
- **Iceberg schema evolution requires backend restart**: after `update_schema().add_column()`, in-process DuckDB connection caches old schema. Redis FLUSHALL is NOT enough. `→ backend-restart-triggers`
- **DuckDB read-after-write**: `invalidate_metadata()` after every Iceberg write. Under concurrent writes prefer `tbl.refresh().scan(filter)` over DuckDB filesystem-glob (race).

### 5.2 Chat agent

- **Cascade routing**: chat = BYO + platform fallback; batch (recommendations/sentiment/forecast) = platform-only; superusers always platform. `→ byom-cascade-override`
- **Sentiment routing**: `sentiment_scorer="finbert"` → ProsusAI/finbert (CPU, free) for batch; LLM cascade ONLY for chat or FinBERT failure.
- **Tool-result truncation guards**: `max_tool_result_chars=4000` default; pass 2 = 2500; pass 3 = 1500. Don't drop <3000 without testing portfolio + screener. Synthesis prompt includes "NO HALLUCINATION ON TRUNCATION" — mirror in any new table-returning sub-agent prompt. `→ llm-truncation-hallucination`
- **Hallucination guardrail**: `_is_hallucinated()` rejects responses with 3+ stock patterns and zero `tool_done` events.
- **Tool-call ID sanitization**: `_sanitize_tool_ids()` cleans Groq IDs before Anthropic fallback.
- **Per-request model pinning**: `_pinned_model` locks model after first invoke. Budget exhaust → compress, unpin, cascade. `pin_reset()` before each new ReAct loop.
- **TokenBudget atomic ops**: `reserve()`/`release()`, NOT `can_afford()`/`record()` (TOCTOU). Singleton via `get_token_budget()`; seeds from Iceberg on restart.
- **bind_tools rebuild lookup**: `_model_lookup` must be rebuilt after `FallbackLLM.bind_tools()`.
- **`max_retries=0` on `ChatGroq`**: Groq SDK internal retries caused 45–56s delays before cascade kicked in.
- **Iteration counter MUST be passed** from sub-agent loop into `FallbackLLM.invoke(messages, *, iteration=...)` — otherwise progressive compression never engages from iter 2+.
- **Cascade profile at agent startup**: `tool` (loop) vs `synthesis` (final response) vs `test` (no Anthropic). After first tool iteration, route to synthesis cascade. `→ groq-chunking-strategy`, `llm-cascade-profiles`
- **New agent class**: subclass `BaseAgent`, override only `format_response()`. Any attribute used in `_build_llm()` MUST exist BEFORE constructor (constructor → `_setup` → `_build_llm`). `→ agent-init-pattern`
- **Sub-agent message construction** — three regimes: (1) first message = `prompt + query`; (2) same-intent follow-up = `prompt + summary + query`; (3) **intent switch = `prompt + query` only** (NO summary, NO history — cross-intent contamination causes hallucination). `→ summary-based-context`
- **Chat-tool ticker discovery**: any chat tool fetching a new ticker from yfinance MUST also call `_ensure_stock_master(ticker, info)`, else ticker never enters daily pipeline. `→ stock-master-auto-insert`
- **Chat clarification gate**: `?` ending bypasses keyword gate in `_is_clarification()`.
- **Currency-aware system prompt**: `_build_context_block()` injects portfolio currency mix; LLM must use ₹ for INR, $ for USD.
- **Tool-forcing prompts**: system prompt MUST be directive ("YOUR FIRST RESPONSE MUST ONLY be a tool call"). `→ llm-tool-forcing`
- **WebSocket protocol**: auth-first handshake; events `thinking`/`tool_start`/`tool_done`/`warning`/`final`/`error`/`timeout`; close codes 4001/4002/4003. `_handle_chat` MUST send errors via `ws.send_json({"type":"error"})` + `{"type":"final"}` — returning the queue after enqueueing error spins drain loop forever. `→ streaming-protocol`

### 5.3 Frontend

- **Data fetch**: ALWAYS via `apiFetch` + SWR hook in `frontend/hooks/`. Never raw `useEffect + fetch`. 2-min dedup, `revalidateOnFocus: false`. `→ swr-data-fetch-pattern`
- **Authenticated route SSR**: RSC + cookie auth + `serverApiOrNull` + SWR `fallbackData`. `proxy.ts` (Next 16 rename of middleware.ts) accepts EITHER `access_token` OR `refresh_token` cookie. `→ cookie-auth-rsc-pattern`
- **ECharts theme**: `useDarkMode` (MutationObserver on `<html>` class) NOT `useTheme()`. `notMerge={true}` + `key={isDark ? "d" : "l"}`. `→ echarts-theme-hydration`
- **TradingView theme**: `useDomDark(isDarkProp)` from `frontend/components/charts/useDarkMode.ts` (also MutationObserver). Apply to every TradingView chart. `→ ssr-hydration-mismatches`
- **ECharts tree-shake**: register only used types in `frontend/lib/echarts.ts` (200KB vs 800KB full). New chart type → add to `use([...])`.
- **Currency**: `tickerCurrency(ticker)` helper. Never hardcode `$`.
- **Images**: `<Image />` from `next/image` (ESLint enforced).
- **SSR safety**: localStorage in `useEffect`; `crypto.randomUUID` guarded by `typeof window`; `toLocaleString("en-US")` with explicit locale.
- **Inline content**: `<span>` not `<div>` inside `<p>` (hydration).
- **Loading shells need text**: Lighthouse FCP doesn't fire on pure-CSS divs. Include text/img/svg. `→ lighthouse-fcp-text-heuristic`
- **Loading-gate LCP anti-pattern**: top-level `if (X.loading) return <Skeleton/>` over prop-driven hero text hides the LCP candidate (Render Delay = 100% of LCP, FCP healthy). Render structure always with `?? 0` placeholders. **BUT keep the inner gate** when body has conditional charts (`rows.length > 0`), wide cells > h1 fallback (Piotroski stock names), heatmap canvases, or many empty-stat cards re-painting — page-level `<Suspense>` provides SSR LCP, inner gate keeps data-bound elements out of the LCP/CLS window. Today's keep-gated tabs: Sectors, Quarterly, Piotroski, Correlation, Observability. `→ loading-gate-lcp-anti-pattern`
- **Suspense `fallback={null}` blanks SSR** when subtree calls `useSearchParams`. Replace with a static `<h1>` + `min-h-[Npx]` reserve that mirrors the inner wrapper outer dimensions exactly (otherwise CLS spikes on swap). Verify with `curl -s -b $JAR /admin | grep -oE '<h1[^>]*>[^<]+'`. `→ suspense-fallback-null-ssr-hole`
- **Sign Out MUST POST `/v1/auth/logout` before `clearTokens()`** — proxy.ts edge gate accepts either cookie (hotfix `e33172d`); localStorage-only sign-out bounces `/login` back to `/dashboard`. Canonical call sites: `AppHeader.handleSignOut`, `ChatHeader.handleSignOut`. Wrap in try/catch.

### 5.4 ★ Tabular page pattern (Insights, Admin)

EVERY new table/list page MUST follow this. Hardened across Sprint 7 (ScreenQL) + Sprint 8 (column selector + admin tabs).

- `useColumnSelection(storageKey, defaults, validKeys)` — localStorage-backed, SSR-safe, tolerates catalog evolution.
- `<ColumnSelector catalog lockedKeys={["ticker"]} />` — popover w/ category groups, search, reset. Use when catalog ≥ 8.
- **Single source of truth**: `visibleCols = allCols.filter(c ∈ selected)`. CSV export consumes the SAME filter — never diverge.
- `<DownloadCsvButton rows={sortedRows} cols={visibleCols} />` — placed next to pagination, NOT in panel header.
- Server-side pagination if `total > 200`; else client-side. Default page size 25.
- Sort: column-header click + arrow; default = relevance.
- Locked identity column (`ticker`).
- Empty state: skeleton during load, primary CTA when empty.
- Stale-data chip in panel-title row when aggregate uses ffill (§5.5).

Reference: ScreenerTab, ScreenQLTab, RecommendationHistoryTab, Admin Users tab. Don't apply when catalog < 8 columns. `→ tabular-page-pattern`

### 5.5 ★ Stale-data transparency chip

When aggregating across N entities and some have stale inputs (NaN closes, market_fallback sentiment), use the chip pattern instead of silent ffill or hard truncation.

- Backend response: `stale_tickers: list[StaleTicker]` (or `unanalyzed_tickers: list[str]`).
- Frontend amber chip in panel-title row, hover/click tooltip lists entities, auto-clears when empty (no dismiss button).
- Smooth aggregate (no dip, no truncation) + transparency in one row.

Reference: `PLTrendWidget::StaleTickerChip`, `NewsWidget::UnanalyzedChip`. `→ portfolio-pl-stale-ticker-chip`

### 5.6 ★ Modal stacking + cross-page modals

z-index ladder: slideovers `z-[60]` · modals (incl. opened from inside slideovers) `z-[70]` · tooltips/popovers `z-[80]` · toasts `z-[90]`.

Cross-page portfolio modals (Add/Edit/Delete/Transactions) mounted ONCE in `(authenticated)/layout.tsx` via `PortfolioActionsProvider`. Pages dispatch via `usePortfolioActions()`. NEVER route-redirect to open a modal (stacks behind slideovers).

View-first edit-from-within: eye icon on rows opens view modal; edit pencil lives INSIDE the view modal per-row.

Confirm-modal DELETE handlers MUST treat 404 as success alongside 204. `→ modal-stacking-pattern`

### 5.7 ★ Admin scope-aware (pro vs superuser)

`?scope=self|all` query param; pro forced to self (403 on `all`), superuser defaults to `all`. Applies to `/admin/audit-log`, `/admin/metrics`, `/admin/usage-stats`. `pro_or_superuser` guard for those three; `superuser_only` for ~45 others.

`TabDef.roles: Role[]` filters admin tab strip. Pro = 3-tab strip (`my_account`, `my_audit`, `my_llm`). General forced to `/dashboard` by route gate.

Tier→role auto-sync: `free→general`, `pro|premium→pro`, superuser sticky. Fires `ROLE_PROMOTED`/`ROLE_DEMOTED`. Frontend calls `refreshAccessToken()` after subscription writes (JWT cached).

**Audit event vocabulary**: `LOGIN`, `OAUTH_LOGIN`, `PASSWORD_RESET`, `USER_CREATED/UPDATED/DELETED`, `ADMIN_PASSWORD_RESET`, `ROLE_PROMOTED/DEMOTED`, `BYO_KEY_ADDED/UPDATED/DELETED`. New events MUST be added to enum + test. `→ pro-user-role-scoped-admin`, `observability`

### 5.8 Recommendation engine

- Monthly-per-scope quota: 1 run per `(user, scope, IST month)` via single `get_or_create_monthly_run` — all entry points (widget/chat/scheduler) MUST go through it.
- `run_type ∈ {manual, chat, scheduled, admin, admin_test}`. User reads filter `admin_test` via `exclude_test=True`.
- Acted-on auto-detect: `POST/PUT/DELETE /portfolio` fires daemon thread → `update_recommendation_status`.
- Stats scope-aware: `/recommendations/stats`, `/history`, and `/performance` take `?scope=india|us|all`. `total_acted_on` derives from `acted_on_date`, NOT `recommendation_outcomes`.
- `expire_old_recommendations` IS scope-aware — don't regress to cross-scope wipe. `→ recommendation-engine`
- **Retention: 14 months hard cap.** Daily `recommendation_cleanup` job (`scheduled_jobs`, 03:00 IST, mon-sun) deletes `stocks.recommendation_runs` where `run_date < CURRENT_DATE - INTERVAL '14 months'`; FK CASCADE wipes child `recommendations` + `recommendation_outcomes`. Idempotent. The cohort-bucketed `/performance` endpoint reads up to 14 months; don't widen the window without widening retention.
- **Performance endpoint** `/recommendations/performance` returns cohort-bucketed analytics (week / month / quarter, IST-truncated) joining the 7/30/60/90d outcomes from `recommendation_outcomes`. Granularity drives the primary horizon shown (weekly→7d, monthly→30d, quarterly→90d). Hit-rate uses `excess_return_pct > 0` to match `/stats`. `pending_count` is granularity-aware = recs younger than the chosen horizon — surface via amber chip per §5.5. `acted_on_only=true` restricts cohort to `acted_on_date IS NOT NULL`.
- **Outcomes job (`recommendation_outcomes`)**: 4 horizons {7, 30, 60, 90}. Self-healing window — picks up any rec at least N days old that lacks an outcome at horizon N (idempotent via `id.notin_(existing)`). Computes return at the close on `created_at + N days` (next trading day if weekend, ±6d forward scan), not latest close. ⚠ `benchmark_return_pct` currently hardcoded to 0 — TODO to wire to a real index. ⚠ `price_at_rec` not always populated by the engine — fix upstream so future cohorts have baseline price; existing rows can be backfilled from OHLCV at `created_at`.

### 5.9 Insights ticker scoping (3-tier)

`insights_routes.py::_scoped_tickers(user, scope)` — single helper. Scope ∈ `{discovery, watchlist, portfolio}`.

| Tab → scope | Who sees what |
|---|---|
| `discovery` (Screener, ScreenQL, Sectors, Piotroski) | Pro/superuser: full universe (`stock`+`etf`); General: watchlist ∪ holdings |
| `watchlist` (Risk, Targets, Dividends) | Everyone: watchlist ∪ holdings |
| `portfolio` (Correlation, Quarterly) | Holdings only (`quantity > 0`) |

Full-universe filter: `ticker_type IN ('stock', 'etf')` (excludes indices + commodity). Per-user cache key MUST include `user_id` (cross-user leak fixed Sprint 7).

### 5.10 Forecast pipeline

- Volatility regime: stable (<30%), moderate (30-60%), volatile (≥60%) — different Prophet config per regime.
- Log-transform for moderate/volatile (`np.log(y)` before fit, `np.exp(yhat)` after — guarantees non-negative).
- Technical bias: RSI/MACD/volume dampen forecast ±15%, taper 30d. Post-processing.
- 5-component confidence score (direction/MASE/coverage/interval/completeness). <0.25 rejected.
- Sanity gates: log-transform exp cap (`np.exp(last_log_y ± 1.5)`, max 4.5×); >200% deviation → series skip.
- Run dedup: `computed_at` (UTC ts), NOT `run_date`.
- Backtest: `horizon_months=0`, actual in `lower_bound`. `→ forecast-enrichment-sanity-gates`

### 5.11 Payments & Subscription (Razorpay INR + Stripe USD)

- **Read tier from Iceberg via `repo.get_by_id()`, NOT JWT** (JWT is stale cache). Same for `subscription_status`.
- **Webhook signature verification MANDATORY** — fail-closed with 503 if secret not configured. `_plan_id_to_tier()` returns `None` for unknown plans (don't default to "pro").
- **Upgrades use PATCH not cancel+create** — Razorpay `subscription.edit` w/ `schedule_change_at="now"`; Stripe `Subscription.modify`. Cancel+create makes orphan subs.
- Concurrent writes: webhook + cancel race → `CommitFailedException`. `_safe_update()` retries 3×.
- Every payment event writes to `auth.payment_transactions` ledger with `event_type` + `tier_before`/`tier_after` + raw payload.

`→ subscription-billing`, `payment-transaction-ledger`, `razorpay-integration-gotchas`, `security-hardening-patterns`

### 5.12 Chat memory layer (pgvector)

- **Write path**: async fire-and-forget from WS worker via `asyncio.run_coroutine_threadsafe()`. Skip responses <50 chars.
- **Read path**: sync before `graph.invoke()` — top-5 cosine similarity from pgvector, 3s timeout, formatted as `[Memory context]` block in sub-agent prompt.
- **Embeddings**: Ollama `nomic-embed-text` (768 dim). Falls back to `ConversationContext.summary` when Ollama down.
- **ConversationContext**: dual-layer in-memory dict + PG persistence via `ConversationContextStore.upsert()` synchronous (NOT daemon thread — fails inside uvicorn).
- Cross-session resume via `get_latest_for_user(user_id)` when frontend generates new `session_id`.

`→ memory-augmented-chat`, `conversation-context-persistence`

### 5.13 ★ Redis caching strategy

EVERY new endpoint returning Iceberg-derived data MUST follow this. Hardened Sprint 4 (cache layer) + Sprint 6 (aggregate `/dashboard/home`).

- **TTL constants**: `TTL_VOLATILE=60` (per-user dashboards), `TTL_STABLE=300` (charts, insights), `TTL_ADMIN=30`. Don't invent new TTLs.
- **Key schema**: `cache:<area>:<endpoint>:<scope>` — e.g. `cache:dash:home:{user_id}`, `cache:chart:ohlcv:{ticker}`. Per-user keys MUST include `user_id`.
- **Write-through invalidation**: every Iceberg write through `_retry_commit()` calls `_invalidate_cache(table)` consulting `_CACHE_INVALIDATION_MAP`. New Iceberg table → add map entry.
- **Route pattern**: `cache.get(key)` → return Response if hit; else compute, `cache.set(key, json, TTL_*)`, return.
- **`cache.set(key, value, ttl=...)`** — kwarg is `ttl` NOT `ex` (silent `TypeError`). `cache.invalidate(pattern)` is glob; `cache.invalidate_exact(*keys)` is exact.
- No-op cache when `REDIS_URL` empty — graceful degradation. `→ redis-cache-layer`

### 5.14 ★ E2E test conventions (Playwright)

EVERY new interactive element MUST have `data-testid`. EVERY new page test MUST follow Page Object Model.

- **Testid registry**: all selectors in `e2e/utils/selectors.ts` `FE` object. Pattern `component-element`. NEVER hardcode in spec files.
- **Page Object Model**: pages extend `BasePage` (`e2e/pages/frontend/`). Use `this.tid(FE.selectorName)`.
- **Auth fixtures**: import from `fixtures/portfolio.fixture` etc. Storage state pre-loaded for `general-user` and `superuser`. NEVER call `/auth/login` in spec (rate-limit). `auth.setup.ts` MUST capture Set-Cookie + rewrite domain to frontend host; `e2e/.auth/*.json` `cookies[]` must contain access + refresh tokens with `domain: "localhost"`. `→ playwright-cookie-fixture`
- **Locator scoping**: chat tests use `[data-testid="chat-panel"]` scope; never globals that could match side panel + main content.
- **Never `networkidle`** — dashboard polls 30s + WebSocket. Use explicit element waits.
- **Below-fold widgets**: `waitFor({ state: "attached" })` then `scrollIntoViewIfNeeded()`.
- **Strict mode**: `/cancel|close/i` matches both Cancel + Close X. Use `/^cancel$/i`.
- **Workers**: 1 local, 2 CI. NEVER raise — 3 workers consumed >1000% CPU and starved Docker.
- **`maxFailures: 10` local cap** — Playwright stops scheduling new tests after 10 failures; use `--max-failures=0` when investigating tech-debt counts (CI is uncapped).

`→ e2e-test-patterns`, `test-isolation-gotchas`

### 5.15 ★ Performance budgets (per-route LCP/perf gates)

Pre-PR `npm run perf:check` (LHCI on /login) is a soft gate; full audit via containerized 34-route Lighthouse (§8) before any major frontend ship.

**Iterating on LCP fixes**: save `pw-lh-summary.json` per cycle, diff LCP **and** CLS per route (every gate removal can spike CLS — verify ≤ 0.02 held). Phase 0 always: read the LCP element + phase breakdown from the per-route JSON before fixing; `Render Delay = 100% of LCP` ≠ `chart paints late`. `→ loading-gate-lcp-anti-pattern`, `suspense-fallback-null-ssr-hole`

| Route bucket | Perf | LCP | TBT | CLS |
|---|---:|---:|---:|---:|
| `/`, `/login`, `/auth/oauth/callback` | ≥ 90 | ≤ 2.0–2.5 s | — | ≤ 0.1 |
| `/dashboard`, `/analytics` | ≥ 80 | ≤ 2.5 s | — | ≤ 0.1 |
| `/analytics/*`, `/insights` | ≥ 75 | ≤ 3.0 s | — | ≤ 0.1 |
| `/admin` | ≥ 70 | ≤ 3.5 s | — | ≤ 0.1 |
| `/docs` | ≥ 85 | ≤ 2.0 s | — | ≤ 0.1 |
| All pages | — | — | ≤ 200 ms | ≤ 0.02 |

Hard rules: JS < 500 KB gzipped per route · mobile baseline (4× CPU, slow 4G) · heavy chart libs via `next/dynamic({ ssr: false })` · `<Suspense>` around chart Client Components when migrating to RSC · `react-markdown` lazy-loaded.

`→ lighthouse-performance-workflow`, `auth-layout-ssr-unlock`, `lighthouse-runner-gotchas`

---

## 6. Bug-Fix Patterns — recurring footguns

Treat as enforced rules. Each came from a real incident.

### 6.1 NaN handling

- **String sentinels**: `"NaN"`, `"None"`, `"null"`, `"N/A"`, `"na"`, `"NaT"` are truthy. Use `safe_str` / `safe_sector` from `backend/market_utils.py` — case-insensitive post-strip rejection. Legit substrings (`"Naniwa"`, `"Financial Services"`) pass.
- **Arithmetic propagation**: `val += qty * NaN` → NaN; `NaN > 0` is False → silently drops the date. Always guard with `math.isnan` before accumulating.
- **`val or default` is broken for pandas numerics** — NaN is truthy, ALL comparisons (`<= 0`, `>= 0`, `== 0`) return False. Use `_safe_float(val)` helper. `→ nan-handling-iceberg-pandas`
- **Iceberg dedup**: a NaN row blocks future re-fetch. Filter dedup query to non-NaN AND scoped pre-delete NaN rows for incoming keys. `→ iceberg-nan-replaceable-dedup`
- **NaN→PG sanitize** before inserting (PG rejects NaT/NaN).

### 6.2 Backend restart triggers (uvicorn --reload isn't enough)

| Change | Action |
|---|---|
| New `@router.get/post/...` decorator | `restart` |
| New field on `response_model` class | `restart` |
| New `app.include_router()` call | `restart` |
| New `@register_job(...)` decorator | `restart` |
| Iceberg `add_column()` ran | `restart` + Redis FLUSHALL |
| New env var in `.env` | `up -d --force-recreate backend` |
| Renamed Alembic migration | edit `revision: str = "..."` + clear `__pycache__/*.pyc` + `restart` |
| `Dockerfile.backend` / `requirements.txt` changed | `compose build backend` + `up -d` |

After restart, sleep 5s before auth-dependent calls (asyncpg shutdown race). `→ backend-restart-triggers`

### 6.3 Cookie hostname

`localhost` and `127.0.0.1` are DIFFERENT hostnames for cookies. `NEXT_PUBLIC_BACKEND_URL=http://localhost:8181` — never `127.0.0.1`. Logout clears cookies at `/`, `/auth`, AND `/v1/auth` (legacy compat). `→ cookie-hostname-mismatch`

### 6.4 Iceberg / DuckDB

- `invalidate_metadata()` after every Iceberg write (already wired in `_retry_commit()`; if stale-read, check it's invoked).
- Concurrent-write race: DuckDB filesystem-glob latest-snapshot read can miss commits. Use `tbl.refresh().scan(filter)` via PyIceberg directly when reading after concurrent writes.
- **Backup BEFORE maintenance** — `run_backup()` is mandatory step 0 of `execute_iceberg_maintenance` (fail-closed).
- **NEVER `rm` metadata or parquet files** — SQLite catalog stores absolute paths. Recovery requires manual SQL update of `iceberg_tables.metadata_location`. `→ iceberg-table-corruption-recovery`
- `ObservabilityCollector` flushes 30s — restart loses unflushed events. Seed on startup.

### 6.5 yfinance / data

- **Pre-market flat candles** (Indian, 08:00 IST pipelines): O=H=L with NaN close. Delete + refetch.
- **Bulk download**: `yf.download()` batches of 100 (99.8% vs 56% per-ticker). `^`-prefixed indices fail in bulk — fetch separately.
- **Sectors casing**: `"Technology"` not `"IT"`, `"Financial Services"` not `"Financials"`.
- **jugaad-data timeout**: `NseSource` wraps in `asyncio.wait_for(timeout=60.0)`.
- **Yahoo `^BSESN` freezes mid-session**: stops emitting after ~10 min of market open. `_is_yahoo_quote_stale()` checks `regularMarketTime` >300s old; falls back to Google Finance scrape (`SENSEX:INDEXBOM`).
- **Per-source 10s timeout** in sentiment fetchers (`_fetch_yfinance`, `_fetch_yahoo_rss`, `_fetch_google_rss`) — without it `yf.Ticker().news` deadlocks the worker pool.
- **Pre-1980 date corruption**: yfinance returns `date=1970-01-01` (epoch zero) → TradingView crashes. Backend filter `df[df["date"] >= "1980-01-01"]` BEFORE Iceberg write; frontend regex `/^(19[89]\d|2\d{3})-/` in `aggregateOHLCV`/`aggregateIndicators`/`filterNull`. `→ iceberg-epoch-dates`

### 6.6 Frontend hydration

- `<div>` in `<p>` → hydration error. Use `<span>` for inline badges.
- Lighthouse FCP only fires on text/img/svg, not pure-CSS divs. Loading shells MUST include text.
- Mount-gate kills SSR — `if (!mounted) return <Spinner />` in layout floors LCP at hydration. Audit providers for SSR safety, then remove. `→ auth-layout-ssr-unlock`
- **LCP regression** — Render Delay = 100% of LCP w/ FCP healthy → loading-gate hiding hero text; or empty SSR HTML → `<Suspense fallback={null}>` over `useSearchParams`. `→ loading-gate-lcp-anti-pattern`, `suspense-fallback-null-ssr-hole`
- **Sign Out bounces to /dashboard** — frontend not calling `/v1/auth/logout`; cookies persist. See §5.3.
- Modal z-index opened from slideovers MUST be `z-[70]` (slideover is `z-[60]`).

### 6.7 Sync→async migration footguns

Coroutines returned silently instead of values — no compile-time error.

- **Missing `await` on async repo methods** — grep `repo\.` across `auth/`, `backend/`, `stocks/`. Bare coroutine passes truthiness checks.
- **Test mocks**: replace `MagicMock` with `AsyncMock` for any mocked async repo. Awaiting a `MagicMock` returns a coroutine.
- **Sync callers of async PG**: pass *callable* not coroutine (`_run_pg(_call)` not `_run_pg(_call())`); use `_pg_session()` (fresh engine) NOT `get_session_factory()` (cached, binds to uvicorn loop).
- **Thread-local across executor boundaries**: `threading.local()` set on event-loop thread is invisible to executor workers. Set inside the worker closure.
- `pool_pre_ping=True` mandatory in `create_async_engine()`.

`→ sync-async-migration-patterns`, `asyncpg-sync-async-bridge`

---

## 7. Process & Workflow

- **Git**: branch off `dev`; squash-only merge to `dev` (merge-commit + rebase blocked by branch protection); Co-Authored-By Abhay; `git add .serena/` before final push; keep feature branch until sprint history no longer needed; hotfix: branch off `main`, PR to `main`, sync DOWN.
- **Jira 3-phase**: create with full metadata → mark In Progress BEFORE code → comment with impl detail + transition Done at ship. Story points: BOTH `customfield_10016` (numeric) AND `customfield_10036` (string). `→ jira-3phase-lifecycle`
- **Test-after-feature**: write tests immediately after smoke test passes. Happy + 1 error path minimum. Don't defer.
- **Doc triggers**: `PROGRESS.md` every session · `docs/` for new/changed API · new Serena memory for new architecture/pattern · `README.md` env-vars table for new config · `stocks/create_tables.py` + `docs/` for new Iceberg table · this file (CLAUDE.md) for new regression-preventing rule.

---

## 8. Quick Reference

```bash
# Lint
black backend/ auth/ stocks/ scripts/ && \
isort backend/ auth/ stocks/ scripts/ --profile black && \
flake8 backend/ auth/ stocks/ scripts/
cd frontend && npx eslint . --fix

# Test
python -m pytest tests/ -v               # count drifts; see PROJECT_INDEX
cd frontend && npx vitest run            # 18 frontend tests
cd e2e && npx playwright test --project=frontend-chromium    # ~3 min, 1 worker
cd e2e && npx playwright test --project=analytics-chromium
cd e2e && npx playwright test --project=admin-chromium
cd e2e && npx playwright test --update-snapshots

# Migrations / seed
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. alembic revision --autogenerate -m "desc"
docker compose exec backend python scripts/seed_demo_data.py

# BYOM first-time
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
docker compose up -d --force-recreate backend  # re-read .env

# Alembic stale bytecode (renamed migration)
docker compose exec backend rm -f /app/backend/db/migrations/versions/__pycache__/*.pyc

# Stock pipeline (PYTHONPATH=.:backend python -m backend.pipeline.runner ...)
download | seed --csv ... | bulk-download | fill-gaps | status
analytics --scope india | sentiment --scope india | forecast --scope india
screen | refresh --scope india --force | recommend

# Performance — containerized 34-route Lighthouse
docker compose --profile perf build frontend-perf
docker compose --profile perf up -d postgres redis backend frontend-perf
docker compose --profile perf run --rm perf       # ~15-20 min
# Output: frontend/.lighthouseci/pw-lh-summary.json
```

---

## 9. Pattern Index

When you're about to do X, read pattern Y first.

| Doing... | § | Memory |
|---|---|---|
| Add a tabular list page | 5.4 | `tabular-page-pattern`, `insights-column-selector-pattern` |
| Aggregate w/ stale per-entity inputs | 5.5 | `portfolio-pl-stale-ticker-chip` |
| Add a modal (any kind) | 5.6 | `modal-stacking-pattern` |
| Add a portfolio-related modal | 5.6 | `portfolio-management` |
| Add an authenticated route | 5.3, 5.7 | `cookie-auth-rsc-pattern` |
| Add a data-fetching hook | 5.3 | `swr-data-fetch-pattern` |
| Add an admin tab | 5.7 | `pro-user-role-scoped-admin` |
| Add a cached endpoint | 5.13 | `redis-cache-layer` |
| Add an E2E test | 5.14 | `e2e-test-patterns` |
| Pre-PR perf check / hit a budget | 5.15 | `lighthouse-performance-workflow` |
| Add an Iceberg column (schema evolution) | 5.1, 6.4 | `backend-restart-triggers`, `iceberg-schema-evolution-backend-restart` |
| Add a new Iceberg table | 4.3 | `db-table-inventory`, `iceberg-maintenance` |
| Add a PG table | 4.3 | `db-table-inventory` |
| Add a chat agent tool | 5.1, 5.2 | `iceberg-freshness-checks`, `llm-tool-forcing` |
| Add a chat tool that fetches a new ticker | 5.2 | `stock-master-auto-insert` |
| Add a chat sub-agent / message-construction path | 5.2 | `summary-based-context`, `llm-truncation-hallucination` |
| Add a new agent class | 5.2 | `agent-init-pattern`, `llm-cascade-profiles` |
| Add chat intent / router branch | 5.2 | `intent-aware-routing` |
| Add WebSocket event type | 5.2 | `streaming-protocol` |
| Touch LLM cascade | 5.2 | `byom-cascade-override`, `round-robin-cascade`, `groq-chunking-strategy` |
| Touch chat ContextVar / BYO | 5.1, 5.2 | `contextvar-run-in-executor` |
| Touch chat memory / extractor / retriever | 5.12 | `memory-augmented-chat`, `conversation-context-persistence` |
| Use Ollama (cascade or embedding) | 5.2, 5.12 | `ollama-local-llm` |
| Add a sentiment source / scorer | 5.2 | `finbert-sentiment-scoring`, `sentiment-dormancy-tracking` |
| Add a recommendation entry point | 5.8 | `recommendation-engine` |
| Add a forecast field / regressor | 5.10 | `forecast-enrichment-sanity-gates` |
| Add a per-ticker refresh entry / step | 5.1 | `per-ticker-refresh`, `ohlcv-freshness-gate` |
| Add a pipeline / chain step | 5.1 | `pipeline-chaining-dag`, `iceberg-daily-pipeline-compaction` |
| Touch any Iceberg timestamp column | 5.1, 6.4 | `iceberg-tz-naive-timestamps` |
| Touch ticker_type classification / filter | 5.9 | `ticker-type-classification` |
| Add subscription gateway endpoint | 5.11 | `subscription-billing` |
| Add a webhook endpoint | 5.11 | `security-hardening-patterns` |
| Touch Razorpay flow | 5.11 | `razorpay-integration-gotchas` |
| Add payment event to ledger | 5.11 | `payment-transaction-ledger` |
| Add OAuth provider / change JWT | 5.7 | `auth-jwt-flow` |
| Add a tier-health metric / observability field | 5.7 | `observability` |
| Add new audit event | 5.7 | `observability`, `security-hardening` |
| Add input validation / rate limit / security header | 4.2, 4.5 | `security-hardening-patterns` |
| Add an ECharts chart type | 5.3 | `portfolio-analytics` (tree-shake) |
| Add a TradingView chart | 5.3 | `ssr-hydration-mismatches` |
| Add multi-source / multi-derivation metric (PEG-style) | 5.4 | `peg-ratio-multi-source-pattern` |
| Add portfolio CRUD endpoint | 5.6, 5.8 | `portfolio-management`, `portfolio-watchlist-sync` |
| Convert sync code to async | 6.7 | `sync-async-migration-patterns`, `asyncpg-sync-async-bridge` |
| Patch a function in tests | 4.2 #16 | `mock-patching-gotchas`, `test-isolation-gotchas` |
| Recover from Iceberg table corruption | 6.4 | `iceberg-table-corruption-recovery` |
| Reclaim Iceberg disk (orphan parquet sweep) | 4.3 #20 | `iceberg-orphan-sweep-design`, `docs/backend/iceberg-orphan-sweep.md` |
| Open / close a Jira ticket | 7 | `jira-3phase-lifecycle` |
| Read NaN-prone numeric column | 6.1 | `nan-handling-iceberg-pandas` |
| Debug LLM "hallucinating" rows | 5.2 | `llm-truncation-hallucination`, `llm-hallucination-guardrail` |
| Debug NaN crash / silent data loss | 6.1 | `nan-string-sentinels`, `iceberg-nan-replaceable-dedup` |
| Debug "field missing from response" | 6.2 | `uvicorn-reload-routes-models-gotcha` |
| Debug random redirect to login | 6.3 | `cookie-hostname-mismatch` |
| Debug Iceberg stale read | 6.4 | `iceberg-schema-evolution-backend-restart` |
| Debug LCP regression | 6.6 | `auth-layout-ssr-unlock`, `lighthouse-fcp-text-heuristic`, `loading-gate-lcp-anti-pattern`, `suspense-fallback-null-ssr-hole` |
| Add a new authenticated route w/ `useSearchParams` | 5.3 | `suspense-fallback-null-ssr-hole`, `cookie-auth-rsc-pattern` |
| Add or change a Sign Out / logout flow | 5.3, 6.6 | §5.3 (POST `/v1/auth/logout` before `clearTokens()`) — see `cookie-auth-rsc-pattern` |
| Add or rebuild Playwright auth fixture | 5.14 | `playwright-cookie-fixture`, `cookie-auth-rsc-pattern` |
| Debug ECharts theme | 5.3 | `echarts-theme-hydration` |
| Debug `bind_tools` empty payload | 5.2 | `bind-tools-model-lookup` |

All paths are `shared/<category>/<name>`. `list_memories` browses 158 memories (~70 architecture · 23 conventions · 37 debugging · 3 onboarding · 25 dated session checkpoints).

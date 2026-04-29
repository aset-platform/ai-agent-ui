# Project Index: AI Agent UI

> AI-agent-optimised codebase map. For human onboarding, see `docs/`.
> Last refreshed: **2026-04-29 evening** (Apr 29 hotfixes + Recommendation Performance feature — Jira `ASETPLTFRM-339`, 8 SP, Done). 14 new commits since 04-25 refresh: morning hotfixes `2faa7fb`/`567369e`/`6d57b38` (scheduler `cron_days=''` silently skipping registration; Firefox `NetworkError` on dev-server stale keep-alive sockets; `/login → /dashboard` bounce loop on stale-cookie sessions); afternoon feature shipped end-to-end on a `feature/recommendation-performance-history` branch (off `dev`) and merged into `feature/sprint8` (merge commit `c3a7585`) for verification before the eventual cleanup PR. Plus `df1aa2f` retired the 4 long-standing eslint-security warnings on `analytics/analysis/page.tsx` via a new `renderTooltip(el, segments)` helper, and `a5044f1` synced README + CHANGELOG + `docs/backend/recommendation-performance.md` + 5 Serena memories. **Current state: `feature/sprint8` is 67 commits ahead of `origin/dev` (entire Sprint 8 + 04-25 LCP follow-on + 04-29 work); cleanup PR `feature/sprint8 → dev` is the next gate.** Prior outcomes still in scope: ASETPLTFRM-338 orphan-parquet sweep (`cleanup_orphans_v2`, 17 unit tests, UI tile, **12.4 GB reclaimed** in first production sweep), LCP follow-on (mean −33.7% across 34 routes, 4202 → 2786 ms), CLAUDE.md restructure (1208 → ~580 lines, 9 sections, 57-row Pattern Index).

---

## Project Structure

```
ai-agent-ui/
├── backend/               # FastAPI application (:8181)
│   ├── main.py            # Entry point
│   ├── agents/            # LangGraph agentic framework
│   │   ├── configs/       # 7 sub-agent configs (stock, portfolio, forecast, rec, etc.)
│   │   ├── nodes/         # 10 graph nodes (guardrail, router, synthesis, etc.)
│   │   ├── graph.py       # LangGraph state graph
│   │   ├── sub_agents.py  # Sub-agent tool-calling loop factory
│   │   └── conversation_context.py  # PG-persisted multi-turn context
│   ├── tools/             # 35 LLM-callable tool modules
│   ├── jobs/              # 7 scheduler executors + pipeline chaining
│   ├── pipeline/          # CLI data pipeline (19 commands, 21 files)
│   │   ├── runner.py      # CLI entry point
│   │   ├── sources/       # yfinance, NSE, racing
│   │   ├── jobs/          # ohlcv, fundamentals, fill_gaps, seed
│   │   └── screener/      # Piotroski F-Score
│   ├── insights/          # ScreenQL query engine
│   │   ├── screen_parser.py # Tokenizer, parser, SQL generator, 39-field catalog
│   │   └── __init__.py
│   ├── maintenance/       # Iceberg ops + backup
│   │   ├── iceberg_maintenance.py # Compact, expire, purge, drop_dead_tables
│   │   ├── backup.py      # rsync + catalog.db + 2-rotation
│   │   └── __init__.py
│   ├── db/                # ORM models, migrations, DuckDB
│   │   ├── models/        # 18 SQLAlchemy models
│   │   ├── migrations/    # 11 Alembic async migrations (+ forecast_runs schema v2)
│   │   ├── engine.py      # Async session factory
│   │   ├── duckdb_engine.py # Iceberg read engine + metadata cache + query_iceberg_multi
│   │   └── pg_stocks.py   # PG CRUD (registry, scheduler, pipeline, recs)
│   ├── config.py          # Settings (Pydantic)
│   ├── routes.py          # Chat API + admin endpoints + BYO resolve
│   ├── ws.py              # WebSocket chat handler + BYO context scope
│   ├── market_routes.py   # Market ticker (Nifty/Sensex, NSE+Yahoo)
│   ├── dashboard_routes.py # Dashboard/chart API
│   ├── insights_routes.py # Screener/analytics API + _scoped_tickers
│   ├── observability.py   # LLM usage collector + Iceberg flush + key_source
│   ├── llm_fallback.py    # N-tier cascade w/ BYO override hook
│   ├── llm_byo.py         # BYOContext + ContextVar + resolve_byo_for_chat
│   ├── crypto/byo_secrets.py  # Fernet-backed BYO key encryption
│   ├── token_budget.py    # Per-model TPM/RPM/TPD/RPD sliding windows
│   └── bootstrap.py       # Tool + agent registration
├── auth/                  # JWT + RBAC + OAuth PKCE
├── stocks/                # Iceberg repository (5,200+ lines)
│   ├── repository.py      # All Iceberg reads (DuckDB-first) + writes
│   └── cached_repository.py # TTL-cached wrapper
├── frontend/              # Next.js 16 SPA (:3000)
│   ├── app/               # 12 pages (App Router)
│   ├── components/        # 30+ components (admin, charts, insights, widgets)
│   ├── hooks/             # 19 SWR data hooks
│   ├── providers/         # Chat, Layout, PortfolioActions contexts
│   └── lib/               # Types, config, apiFetch, downloadCsv
├── e2e/                   # 51 Playwright specs (~257 tests)
│   ├── tests/frontend/    # 30 spec files (auth, chat, analytics, admin, billing)
│   ├── tests/dashboard/   # 10 spec files (legacy Dash)
│   ├── tests/errors/      # 2 spec files
│   ├── tests/performance/ # 1 spec file (Lighthouse)
│   ├── pages/             # 17 page objects
│   ├── fixtures/          # auth + portfolio fixtures
│   └── utils/             # selectors, wait helpers, API helpers
├── tests/                 # 97 pytest files (~839 tests)
├── scripts/               # 28 data/migration/seed scripts
├── docs/                  # 56 MkDocs Material pages (15 dirs)
└── docker-compose.yml     # 5 services (backend, frontend, PG, Redis, docs)
```

---

## Entry Points

| Entry | Path | Port |
|-------|------|------|
| Backend API | `backend/main.py` | 8181 |
| Frontend SPA | `frontend/app/page.tsx` | 3000 |
| Pipeline CLI | `backend/pipeline/runner.py` | — |
| Scheduler | `backend/jobs/scheduler_service.py` | daemon |
| Docs | `docs/` via MkDocs | 8000 |

---

## Database (Hybrid PG + Iceberg)

**PostgreSQL (19 tables)**: users, user_tickers, payments, registry,
scheduled_jobs, scheduler_runs, recommendation_runs, recommendations,
recommendation_outcomes, market_indices, user_memories (pgvector
768-dim), conversation_contexts, stock_master, stock_tags,
ingestion_cursor, ingestion_skipped, sentiment_dormant (per-ticker
headline-fetch dormancy, capped expo cooldown 2/4/8/16/30d),
pipelines, pipeline_steps.

**Iceberg (12 active tables)**: ohlcv (1.5M rows), company_info,
dividends, quarterly_results, analysis_summary, forecast_runs
(27 cols), forecasts, piotroski_scores, sentiment_scores,
llm_pricing, llm_usage, portfolio_transactions.
**Dropped**: scheduler_runs (25GB→PG), scheduled_jobs (→PG),
technical_indicators (unused, computed on-the-fly).

**Maintenance**: `backend/maintenance/` — backup (rsync + catalog.db,
2-rotation), compaction (overwrite → 1 file/partition), 11yr retention.
Backup dir: `/Users/abhay/Documents/projects/ai-agent-ui-backups/`.
**Daily auto-backup + auto-compact** runs as step 6 of both daily
pipelines (`iceberg_maintenance` job, fail-closed if backup fails;
`rsync` installed in `Dockerfile.backend`).
**OHLCV upsert is NaN-replaceable** (Apr 23+): dedup query filters
`close IS NOT NULL AND NOT isnan(close)` + scoped pre-delete of NaN
rows for to-be-inserted `(ticker, date)` set; pattern in both
`insert_ohlcv` + `batch_data_refresh`.

**Rule**: Mutable state → PG. Append-only analytics → Iceberg.
DuckDB for ALL Iceberg reads (metadata cache, auto-invalidated).
NEVER delete metadata/parquet files directly (CLAUDE.md Rule #20).

---

## Auth & RBAC

Three roles: `general | pro | superuser`. Tier→role auto-sync
hooked in `auth/repo/user_writes.py::update()` (pinch point):
`free → general`, `pro|premium → pro`. **Superuser is sticky** —
never auto-demoted. Fires `ROLE_PROMOTED`/`ROLE_DEMOTED` audit
events post-commit.

Guards: `superuser_only` (~45 admin endpoints), `pro_or_superuser`
alias via `require_role(*allowed)` factory for 3 self-scoped
endpoints (`/admin/audit-log`, `/admin/metrics`,
`/admin/usage-stats`). Pattern: `?scope=self|all`; pro forced
to self.

JWT role is cached — role change only propagates after
`/auth/refresh`. Pro admin page shows 3 tabs (My Account,
My Audit Log, My LLM Usage); superuser sees full 7-tab strip.

**Sign Out** (commit `c9e0054`, 2026-04-25): `AppHeader.handleSignOut`
+ `ChatHeader.handleSignOut` MUST POST `/v1/auth/logout` *before*
`clearTokens()`. Backend `auth_routes.py:343` calls
`_clear_refresh_cookie` + `_clear_access_cookie`. Without this,
`proxy.ts` edge gate sees the lingering `refresh_token` cookie
(legacy-session hotfix `e33172d`) and bounces `/login` back to
`/dashboard` — Sign Out appears to do nothing.

---

## BYOM — Bring Your Own Model

Chat-agent LLM routing: non-superusers get 10 lifetime free
chat turns, then must configure a Groq and/or Anthropic key or
chat blocks with 429. Non-chat flows (recommendations,
sentiment, forecast) always use platform keys. Ollama stays
shared/native.

Key infra:
- `user_llm_keys` table (Alembic `f8e7d6c5b4a3`), Fernet-encrypted
  via `backend/crypto/byo_secrets.py` (`BYO_SECRET_KEY` env).
- `backend/llm_byo.py` — `BYOContext` + module-level `ContextVar`
  + `apply_byo_context()` + `resolve_byo_for_chat()` + Redis
  monthly counter `byo:month_counter:{uid}:{yyyy-mm}` (IST).
- `FallbackLLM._try_model` (Groq) + Anthropic fallback both
  consult `get_active_byo_context()` and swap in user-keyed
  client when BYO active. Stamps `key_source="user"` on
  `llm_usage` rows.
- `chat_request_count` bump guarded by `byo_active` so the
  free counter pins at 10 once BYO kicks in.
- `/v1/users/me/llm-keys` + `/v1/users/me/byo-settings` endpoints
  (self-scoped). `MyLLMUsageTab.tsx` renders the page.

Full workflow: `docs/backend/byom.md`.

---

## Insights tab scoping (three-tier)

Single helper `backend/insights_routes.py::_scoped_tickers(user, scope)`
drives ticker visibility for all 9 Insights tabs:

| Scope | Tabs | Pro / Superuser | General |
|---|---|---|---|
| `discovery` | Screener, ScreenQL, Sectors, Piotroski | full platform (stock + ETF) | watchlist ∪ holdings |
| `watchlist` | Risk, Targets, Dividends | watchlist ∪ holdings | watchlist ∪ holdings |
| `portfolio` | Correlation, Quarterly | holdings only | holdings only |

Full-universe scope filters `ticker_type IN ('stock', 'etf')`
— index/commodity tickers stay out of Screener/ScreenQL.

---

## Recommendation Engine

**Quota**: 1 run per `(user, scope, IST calendar month)`. All
three entry points (widget, chat, scheduler) delegate to
`get_or_create_monthly_run` in `backend/jobs/recommendation_engine.py`.
`scope="all"` auto-expands into india + us sequential calls.

**run_type vocabulary**: `manual | chat | scheduled | admin |
admin_test`. `admin_test` hidden from user-facing reads via
`exclude_test=True` default. Superuser admin tab passes
`exclude_test=False`.

**Admin flow**: `POST /admin/recommendations/force-refresh`
bypasses quota → writes `admin_test`. `POST /admin/recommendation-runs/{id}/promote`
deletes existing non-test run for same `(user, scope, IST month)`
+ relabels target to `admin`.

**Acted-on**: `POST/PUT/DELETE /users/me/portfolio` fires daemon
thread → `update_recommendation_status(uid, ticker, actions,
"acted_on")`. BUY/ACCUMULATE on POST; SELL/REDUCE/TRIM on qty
decrease or delete. Only matches `status='active'`.

**Performance analytics** (ASETPLTFRM-339, 2026-04-29):
`GET /v1/dashboard/portfolio/recommendations/performance` returns
cohort-bucketed analytics over `recommendation_outcomes` (4 horizons
`{7, 30, 60, 90}`). Granularity `week|month|quarter` drives the
primary horizon shown (Weekly→7d, Monthly→30d, Quarterly→90d).
Frontend at `/analytics/analysis?tab=recommendations&subtab=performance`
(new `RecommendationsPanel` parent + `RecommendationPerformanceTab` view
+ reusable `InfoTooltip`). `pending_count` granularity-aware. PG helper
`get_recommendation_performance_buckets()` in `backend/db/pg_stocks.py`
— CTE-based raw SQL with IST `date_trunc` + `CAST(:scope AS VARCHAR)`
for asyncpg NULL-param compat. Cache key `cache:portfolio:recs:{uid}:perf:*`,
TTL_STABLE 300s. Full doc: `docs/backend/recommendation-performance.md`.

**Retention**: 14-month hard cap via daily `recommendation_cleanup`
job (03:00 IST mon-sun). FK CASCADE wipes child recs + outcomes.
Idempotent. Cache invalidates `cache:portfolio:recs:*` on non-zero
deletes.

**Outcomes pipeline** (overhauled 2026-04-29): self-healing
window-match — picks up any rec ≥ N days old that lacks an outcome
at horizon N. Was a strict `created_at = today − N ± 2d` window
which silently dropped outcomes if the daily run skipped a day or
a horizon was added retroactively. Now also fetches OHLCV close at
`created_at + N days` (next trading day on weekends) instead of
"latest close per ticker" — only correct under the original strict
window. Memo: `shared/debugging/recommendation-outcomes-self-healing`.

**Known gaps**: (1) recommendation engine doesn't populate
`price_at_rec` from OHLCV at issue time — 41 existing recs were
backfilled out-of-band; engine fix is a Sprint 9 follow-up. (2)
`benchmark_return_pct` is hardcoded `0.0` in the executor — wire
to a real index (Nifty India / S&P US) so `excess ≡ return` stops
being a TODO. The Performance tab's "Avg excess" tile carries an
amber heads-up callout about this in its tooltip.

---

## Chat Agent Architecture

6 sub-agents: stock_analyst, portfolio, forecaster, research,
sentiment, recommendation. Routed by 2-tier intent classifier
(keyword → LLM fallback).

Key flow: guardrail → router → supervisor → sub-agent (tool loop)
→ synthesis → response.

Context: PG-persisted ConversationContext (cross-session resume).
Memory: pgvector semantic retrieval (nomic-embed-text 768-dim).

LLM Cascade: Groq pools (llama-3.3-70b, qwen3-32b) →
(gpt-oss-120b, gpt-oss-20b) → scout-17b → Ollama → Anthropic.

---

## Key Modules

| Module | Files | Purpose |
|--------|-------|---------|
| `backend/agents/` | 30+ | LangGraph graph, 8 configs, 11 nodes, context |
| `backend/tools/` | 35 | Stock tools: forecast, analysis, sentiment, portfolio, recs |
| `backend/tools/_forecast_regime.py` | 1 | Volatility regime classification (low/medium/high/extreme) |
| `backend/tools/_forecast_features.py` | 1 | Tier 1/2 feature computation (macro, technical, sentiment) |
| `backend/tools/_sentiment_finbert.py` | 1 | FinBERT batch sentiment scorer (torch CPU, transformers) |
| `backend/tools/_sentiment_sources.py` | 1 | Headline fetchers with 10s per-source `_run_with_timeout` guard |
| `backend/tools/_sentiment_scorer.py` | 1 | `score_headlines_with_source()` returns `(score, finbert|llm|none)` |
| `backend/jobs/recommendation_engine.py` | 1 | Monthly-per-scope IST quota, consolidator entry point |
| `backend/market_utils.py` | 1 | `detect_market`, `safe_str`, `safe_sector` (NaN-truthy safe) |
| `auth/dependencies.py` | 1 | `superuser_only`, `require_role()`, `pro_or_superuser` guards |
| `auth/repo/user_writes.py` | 1 | Tier→role auto-sync pinch point + post-commit audit |
| `backend/llm_byo.py` | 1 | BYOContext + ContextVar + resolve_byo_for_chat + Redis monthly counter |
| `backend/crypto/byo_secrets.py` | 1 | Fernet encrypt/decrypt/mask for user-supplied provider keys |
| `auth/repo/byo_repo.py` | 1 | BYO key CRUD + provider/prefix validators + chat counter bump |
| `auth/endpoints/byo_routes.py` | 1 | Self-scoped `/v1/users/me/llm-keys` + `/byo-settings` endpoints |
| `backend/db/models/user_llm_key.py` | 1 | `user_llm_keys` ORM (encrypted_key BYTEA, unique per provider) |
| `backend/db/models/sentiment_dormant.py` | 1 | Per-ticker dormancy state — capped expo cooldown 2/4/8/16/30d, 5% probe |
| `backend/jobs/executor.py::execute_iceberg_maintenance` | 1 | Daily pipeline step 6 — backup (fail-closed) then compact 4 hot tables |
| `backend/market_routes.py` | 1 | Yahoo Sensex `^BSESN` stale-feed detection + Google Finance fallback |
| `backend/insights/screen_parser.py` | 1 | ScreenQL: tokenizer, parser, SQL gen, 39-field catalog (incl. 3 PEG variants), `display_columns` param for user-pickable result columns |
| `backend/insights_routes.py` | 1 | Screener endpoint: batched DuckDB reads (piotroski / forecast_runs / quarterly_results / company_info) populate 41-field `ScreenerRow`; `_compute_peg*` helpers for T/YF/Q variants |
| `backend/maintenance/` | 3 | Backup (rsync), compaction, retention, dead table cleanup |
| `backend/jobs/` | 8 | Executor registry, pipeline chaining, batch refresh (bulk OHLCV), recs |
| `backend/pipeline/` | 21 | CLI: download, seed, bulk-download, analytics, forecast, screen |
| `backend/db/models/` | 18 | SQLAlchemy ORM (PG tables) |
| `stocks/repository.py` | 1 (5.2K lines) | Iceberg CRUD + DuckDB reads + PG bridge |
| `frontend/hooks/` | 19 | SWR data fetching for all pages |
| `frontend/components/` | 30+ | Admin, charts, insights, widgets, modals |
| `frontend/lib/downloadCsv.ts` | 1 | CSV export utility (escape, blob, browser download) |
| `frontend/components/common/DownloadCsvButton.tsx` | 1 | Shared CSV button (icon + loading state) — used by all exports |
| `frontend/lib/useColumnSelection.ts` | 1 | localStorage-backed column selection hook — tolerant to catalog evolution, two-phase SSR/client hydration |
| `frontend/components/insights/ColumnSelector.tsx` | 1 | Grouped-by-category column picker popover (search, per-category toggle, locked keys, reset) — used on Screener + ScreenQL |
| `frontend/providers/PortfolioActionsProvider.tsx` | 1 | Layout-level Add/Edit/Delete/**Transactions** modals via `usePortfolioActions()` |
| `frontend/components/widgets/PortfolioTransactionsModal.tsx` | 1 | Eye-icon modal — date-sorted txns + per-row edit + summary footer |
| `frontend/components/widgets/PLTrendWidget.tsx::StaleTickerChip` | 1 | Amber chip — "N holdings using previous close" w/ tooltip |
| `frontend/components/widgets/NewsWidget.tsx::UnanalyzedChip` | 1 | Amber chip — "N holdings unanalyzed" (sentiment market_fallback proxy) |
| `frontend/components/admin/MyAccountTab.tsx` | 1 | Pro scoped admin tab (profile + password, no role/tier) |
| `frontend/components/admin/MyLLMUsageTab.tsx` | 1 | BYO allowance + provider cards + usage/model split + sparkline |
| `frontend/components/admin/ConfigureProviderKeyModal.tsx` | 1 | Paste Groq/Anthropic key, show/hide, prefix validation |
| `frontend/components/admin/SentimentDetailsModal.tsx` | 1 | Source tiles + paginated filterable ticker table |
| `frontend/components/recommendations/RecActionButton.tsx` | 1 | +Buy / Edit / Acted ✓ pills on rec cards |
| `e2e/utils/selectors.ts` | 1 | Centralised data-testid constants (217 lines) |
| `e2e/playwright.config.ts` | 1 | 6 projects, 1 worker local / 2 CI, video off local |
| `e2e/setup/auth.setup.ts` | 1 | Login fixture — parses Set-Cookie + rewrites domain to frontend host so storageState carries the HttpOnly cookies the proxy.ts edge gate requires (`d081827`) |
| `frontend/components/widgets/HeroSection.tsx` | 1 | Dashboard hero — greeting + portfolio value/PL render from props always; do NOT gate on `watchlist.loading` (`b1c816e`) |
| `frontend/app/(authenticated)/admin/page.tsx::AdminPageSkeleton` | 1 | Static SSR fallback (h1 + min-h-[600px]) for `<Suspense>` over `useSearchParams` — mirrors AdminPageInner outer wrapper exactly to hold CLS ≤ 0.02 |
| `frontend/app/(authenticated)/analytics/insights/page.tsx::InsightsPageSkeleton` | 1 | Same pattern at min-h-[400px] — SSR shell that ships LCP candidate text before client hydration |

---

## Scheduler & Jobs

9 job types: `data_refresh`, `compute_analytics`, `run_sentiment`,
`run_forecasts`, `run_piotroski`, `recommendations`,
`recommendation_outcomes` (now 7/30/60/90d horizons + self-healing
window-match, see Recommendation Engine §), `recommendation_cleanup`
(daily 03:00 IST, 14-month retention purge, FK CASCADE; new 2026-04-29),
`iceberg_maintenance`. All accept `force=False`. Market ticker runs
independently (30s poll, not scheduled).

Freshness gates: daily (OHLCV, analytics, sentiment), weekly
(forecasts), monthly (CV accuracy auto-refresh via 30-day TTL).

**Daily pipeline (6 steps, ~12 min)**: India `08:00 IST` + USA
`08:15 IST`, Tue–Sat. Container TZ=`Asia/Kolkata` (was UTC; cron
was firing 5.5h late). `scheduler_catchup_enabled=False` default
(opt-in via env). Sequence: `data_refresh → compute_analytics →
run_sentiment → run_piotroski → recommendation_outcomes →
iceberg_maintenance` (step 6 = backup-then-compact, fail-closed).

Sentiment dormancy: `sentiment_dormant` PG table excludes ~60% of
universe from per-ticker headline fetches (5% probe re-test).
Hot-classifier filter `IN ('finbert','llm')`. Top-50 learning
batch joins `company_info.market_cap` (was alphabetical).

Bulk OHLCV: yf.download() batches of 100 (99.8% success, 58s).
Chat-discovered tickers auto-inserted into stock_master for
pipeline pickup.

---

## Key Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| FastAPI | 0.135 | REST API |
| Next.js | 16.1 | Frontend |
| LangChain | 1.2 | Agent framework |
| Prophet | 1.3 + CmdStanPy 1.3 | Forecasting |
| SQLAlchemy | 2.0 async | ORM (asyncpg) |
| PyIceberg | 0.11 | Table management |
| DuckDB | 1.2 | Iceberg read engine |
| torch (CPU) | latest | FinBERT inference (Docker: CPU wheel) |
| transformers | latest | FinBERT model (ProsusAI/finbert) |
| SWR | 2.3 | Frontend data hooks |
| lightweight-charts | 5.1 | TradingView |

---

## File Counts

Backend Python: **218 modules** | Frontend TS/TSX: **128** (added
`RecommendationsPanel.tsx`, `RecommendationPerformanceTab.tsx`,
`common/InfoTooltip.tsx`, `lib/renderTooltip.ts` on 2026-04-29 — net
delta also reflects pruning) | Tests: pytest **538 cases** (incl. 11
new in `test_recommendation_performance.py`), vitest **66 cases**
(was 61, +5 in `RecommendationsPanel.test.tsx`), e2e **51 specs** |
Docs: **65 pages** (added `recommendation-performance.md`) | Scripts: **33** |
Alembic migrations: **13** (`f8e7d6c5b4a3_add_byo_foundation` latest)

E2E pass rate (analytics-chromium full sweep, post-`096edc5`):
**111 / 147 tests pass**, 34 fail (pre-existing tech debt:
marketplace tests after Sprint 7 route deprecation; `insights.spec.ts`
references old Plotly tabs since Sprint 6 ECharts migration;
`insights-recommendations.spec.ts` references "recommendations" tab
moved to `/analytics/analysis`; modal/timing flakes in portfolio-crud
+ theme-consistency). Sprint 9 follow-up: an E2E spec for the new
Performance sub-tab, pending auth-fixture wiring per
`shared/conventions/playwright-cookie-fixture`.

## Lighthouse Performance Snapshots

Stored at `.lighthouseci/`:
- `pw-lh-summary-baseline-2026-04-25.json` — pre-LCP-follow-on baseline
- `pw-lh-summary-iteration2-final.json` — first iter after gate fixes
- `pw-lh-summary-iteration3.json` — Suspense-fallback-skeleton sized
- `pw-lh-summary-iteration4-final.json` — sectors/quarterly/piotroski
  CLS reverts. **34 routes, mean LCP 2786 ms (-33.7% from baseline).**
- `pw-lh-summary.json` — current (= iter4).

Diff with `python3 /tmp/compare_lcp.py` (script saved per-session;
re-create from `shared/debugging/loading-gate-lcp-anti-pattern`
walkthrough if missing).

---

## Quick Start

```bash
cp .env.example .env && ./run.sh start
docker compose exec backend python scripts/seed_demo_data.py
# http://localhost:3000 → admin@demo.com / Admin123!
```

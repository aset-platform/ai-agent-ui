# CHANGELOG

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.10.0] — 2026-04-18: Monthly Recommendations + Acted-On + Sentiment Hardening + Pro Role (Sprint 7)

### Added

- **Recommendation monthly-per-scope quota** (ASETPLTFRM-318): one run per `(user, scope, IST calendar month)`. All three entry points (widget, chat, scheduler) share a single `get_or_create_monthly_run()` consolidator. `scope="all"` silently expands to `india` + `us`. Superuser-only `POST /v1/admin/recommendations/force-refresh` creates `admin_test` rows that stay hidden from user-facing views; `POST /v1/admin/recommendation-runs/{id}/promote` atomically swaps a TEST run into the active slot. New `RunTypeBadge` variants (ADMIN fuchsia, TEST amber). Widget Generate button disables with reset-date tooltip when cached.
- **Recommendation acted-on auto-detection** (ASETPLTFRM-319): `POST/PUT/DELETE /users/me/portfolio` now fire `update_recommendation_status(user, ticker, actions, "acted_on")` so matching recs flip to Acted ✓ without manual input. New `RecActionButton` pills (+ Buy / Edit / Acted ✓) on every recommendation row across Portfolio Widget, slideover, and Analysis → Recommendations. New `PortfolioActionsProvider` at the authenticated-layout level mounts Add/Edit/Delete modals once; `usePortfolioActions()` hook replaces the old route-redirect UX.
- **Recommendation stats scope filter** (ASETPLTFRM-319): `/stats?scope=india|us|all` returns scope-aware adoption rate + hit rates; `get_recommendation_history` now emits real `acted_on_count` per run (was hardcoded 0).
- **Sentiment Data Health details modal** (ASETPLTFRM-320): `GET /v1/admin/data-health/sentiment-details?scope=all|india|us` (superuser, 60s Redis cache) + `SentimentDetailsModal.tsx` with source-category tiles (finbert / llm / market_fallback / none), filterable and paginated ticker table, CSV download, scope tabs.
- **Accurate sentiment provenance** (ASETPLTFRM-320): new `score_headlines_with_source()` returns `(score, source)`; `sentiment_scores.source` column carries one of `finbert`, `llm`, `market_fallback`, `none`. Log line format `src=finbert, force=upsert`.
- **Sentiment force-upsert** (ASETPLTFRM-320): `refresh_ticker_sentiment(..., force=True)` bypasses the per-ticker idempotency check and overrides today's row via the existing upsert path. Scheduler-level `force=true` now actually reaches per-ticker.
- **Pro user role** (unticketed, shipped same session): third role between `general` and `superuser`. Tier-driven auto-sync (`subscription_tier ∈ {pro, premium}` → `role=pro`, **superuser sticky**) fires `ROLE_PROMOTED` / `ROLE_DEMOTED` audit events. Pro users see Insights + a 3-tab scoped Admin view (My Account, My Audit Log, My LLM Usage) — superuser still sees all 7 tabs. `/admin/audit-log`, `/admin/metrics`, `/admin/usage-stats` switched to `pro_or_superuser` guard with `?scope=self|all`. New `MyAccountTab.tsx`; `UserModal` role dropdown now offers General / Pro / Superuser.
- **Shared helpers**: `safe_str()` + `safe_sector()` in `backend/market_utils.py` (NaN-truthy guard); `DownloadCsvButton` shared component in `frontend/components/common/`.
- **One-shot cleanup script**: `scripts/truncate_recommendations.py` for the monthly-quota rollout.

### Changed

- **Sentiment batch pipeline** (ASETPLTFRM-320): learning set capped at top-50 by market cap (767 → 50 per run) — tail drops into market-fallback. Runtime 802 → ~85 tickers, ~30s. FinBERT-mode skips the unused `FallbackLLM` constructor (no more 802× "Groq 5 tiers" log lines per run).
- **Widget + Admin CSV buttons** (ASETPLTFRM-322): unified around the shared `DownloadCsvButton` matching the Screener pattern — icon + "CSV" label placed next to pagination.
- **Asset Performance widget** (ASETPLTFRM-322): fixed height (9 bars ~292px) with overflow-y scroll; dropped the top-7/bottom-7 truncation so all holdings render.
- **Scheduler UI** (ASETPLTFRM-322): counter label flips to `users` for `job_type="recommendations"` (was always `tickers`); "Last Run" stat card shows `N processed` (generic); Force Run menu item greyed-out with "Off" pill only on recommendation jobs.
- **Modal z-index** (ASETPLTFRM-319, -322): `AddStockModal`, `EditStockModal`, `ConfirmDialog` raised to `z-[70]` so they render above `RecommendationSlideOver` (`z-[60]`).

### Fixed

- **Sentiment 1599/802 double-count** (ASETPLTFRM-320): DuckDB metadata cache stale-read caused Step-5 gap-fill to see 0 new rows and overwrite genuine LLM/FinBERT scores with market-fallback. Fix: `invalidate_metadata("stocks.sentiment_scores")` before the re-query.
- **Sentiment deadlocked pool** (ASETPLTFRM-320): unthrottled `yf.Ticker().news` hung 15 concurrent Yahoo sockets indefinitely. Fix: `_run_with_timeout(fn, timeout=10)` wrapper in `_sentiment_sources.py` applied to all three fetchers + market-headlines feedparser.
- **Sentiment force flag ignored** (ASETPLTFRM-320): `refresh_ticker_sentiment` early-returned on already-scored-today regardless of scheduler force. Fix: new `force` param propagated from executor through `gap_filler.refresh_sentiment`.
- **expire_old_recommendations cross-scope wipe** (ASETPLTFRM-318): an incoming US run was deleting India recs because the helper expired every prior run for the user. Now scoped by `(user_id, scope)`.
- **Recommendation stats counter hardcoded 0** (ASETPLTFRM-319): `acted_on_count` now computed via `SUM(CAST(acted_on_date IS NOT NULL AS Integer))`; `total_acted_on` from a dedicated count query, not `total_outcomes`.
- **NaN-truthy sector leak** (ASETPLTFRM-321): `row.get("sector") or "Other"` kept ETF NaN values in recommendation prompts ("NaN (41.8%)"). Fixed across 10 files: 6 write paths sanitize before Iceberg insert, 4 read paths use `safe_sector(..., fallback="ETF/Other")`.
- **Admin force-refresh UUID-only** (ASETPLTFRM-318): endpoint now accepts email or UUID, resolves email → `auth.users.user_id` before the pipeline.
- **Widget cached state persistence** (ASETPLTFRM-318): response now carries `cached`, `reset_at`, `scope`; Generate button disables + shows `Next available <date>` when the month's run already exists.

### Security / RBAC

- New dependency `require_role(*allowed)` factory in `auth/dependencies.py`; `pro_or_superuser` alias on self-scoped admin endpoints.
- Pro callers passing `scope="all"` get 403; `scope="self"` always allowed for `pro` and `superuser`.
- `UserUpdateRequest.role` + `UserCreateRequest.role` Pydantic Literals extended to `general | pro | superuser` — invalid values rejected with 422.
- Audit event vocabulary extended: `ROLE_PROMOTED`, `ROLE_DEMOTED` (system-driven on subscription change). `PATCH /auth/me` now writes `USER_UPDATED` so pros see self-edits in My Audit Log.

---

## [0.9.0] — 2026-04-17: ScreenQL + Iceberg Maintenance + Bulk OHLCV (Sprint 7)

### Added

- **ScreenQL universal screener** (ASETPLTFRM-314): text-based stock query language with 36-field catalog across 6 Iceberg tables, recursive descent parser, CTE-based DuckDB SQL, 6 preset templates, autocomplete, dynamic columns, currency symbols, market filtering
- **Centralized CSV download** (ASETPLTFRM-313): `downloadCsv.ts` utility wired into 10 tabs (7 Insights + Users + Audit Log + Transactions)
- **Iceberg maintenance system** (ASETPLTFRM-315): backup (rsync + catalog.db + 2-rotation), compaction (overwrite), retention (11yr purge), orphan cleanup, post-pipeline snapshot expiry
- **Backup Health panel** (ASETPLTFRM-316): readonly admin panel with health badge, backup list, expandable folder browser, Redis-cached API endpoints
- **Bulk OHLCV download** (ASETPLTFRM-317): `yf.download()` batches of 100 replacing per-ticker `.history()` — 804→9 HTTP calls, 44%→0.2% failures, 280s→58s
- **DuckDB `query_iceberg_multi()`**: cross-table JOIN support for ScreenQL queries
- **Scheduler delete confirmation modals** (ASETPLTFRM-312): ConfirmDialog for jobs and pipelines

### Fixed

- **Piotroski blank company names**: stock_master PG fallback in Piotroski + ScreenQL endpoints
- **OHLCV freshness gate**: `>= today` (was `>= yesterday`) — evening runs now fetch closing data
- **OHLCV upsert**: scoped delete + re-append for today's rows — stale intraday candles corrected
- **Event loop blocking**: `asyncio.to_thread()` for fix-ohlcv backfill_nan + backfill_missing
- **Portfolio allocation ETF sector**: detects BEES/ETF pattern → "ETF" label (was NaN crash)
- **ForecastTarget nullable fields**: `float | None` for target_price/pct_change/bounds (fixes 500 for new users)
- **KpiTooltip clipping**: viewport clamping prevents right-edge overflow
- **Data health + backup health slow loads**: Redis caching (60s/120s)

### Changed

- **Transactions tab**: refactored from custom HTML table to InsightsTable with pagination + sorting
- **Iceberg warehouse**: 41 GB → 14 GB — dropped 3 dead tables (scheduler_runs 25GB, technical_indicators 2.3GB, scheduled_jobs)
- **Compacted 7 tables**: company_info 4055→1 file (830 rows!), sentiment_scores 6673→809 files
- **CLAUDE.md**: Hard Rule #20 (never delete Iceberg metadata), Iceberg Maintenance gotchas section

---

## [0.8.0] — 2026-04-16: E2E Overhaul + Model Pinning (Sprint 7)

### Added

- **E2E test coverage overhaul** (ASETPLTFRM-308): 43 new tests across 6 new test files — dashboard widgets, Piotroski/Recommendations/Admin tabs, visual regression baselines, CSV download + pagination
- **CSV download on Insights tables**: `downloadCsv.ts` utility, download button on 7 tabs (screener, targets, dividends, risk, sectors, quarterly, piotroski)
- **Per-request model pinning** (ASETPLTFRM-305): round-robin locks model after first invoke per request, `pin_reset()` before each ReAct loop
- **Non-overlapping portfolio periods** (ASETPLTFRM-307): `_period_to_days()` helper, `bfill()` fix for 4152% return bug
- **Scheduler delete confirmation modals**: ConfirmDialog for job + pipeline deletion (replaces immediate delete / browser confirm)

### Fixed

- **E2E Tier 1** (ASETPLTFRM-309): ChatPage page object rewrite, dark-mode/navigation/websocket tests aligned to current sidebar + chat panel UI
- **E2E Tier 2** (ASETPLTFRM-310): Added 5 testids to frontend modals, fixed 34 billing/payment/subscription/portfolio/profile/session tests
- **Piotroski blank company names** (ASETPLTFRM-312): stock_master PG fallback in read path, warning logs at write time
- **E2E CPU usage**: reduced from >1000% to ~30% — 1 worker locally, video off, maxFailures=10, Chromium flags
- **19 visual regression baselines** regenerated for current UI
- **Stale selectors**: admin summary card (compressions→tokens), UserModal testids, insights statement type option, billing text matching

### Changed

- **Playwright config**: 1 worker locally (was 3), video off locally, maxFailures=10, `--disable-gpu` Chromium flag
- **Portfolio CRUD tests**: moved to analytics-chromium project (general user auth)
- **Kimi K2 → Qwen3-32B**: Groq model replacement across 22 files

---

## [0.7.0] — 2026-04-13: Chat Agent Hardening + Recommendations (Sprint 6)

### Added

- **Smart Funnel Recommendation Engine** (ASETPLTFRM-298): 3-stage pipeline (DuckDB pre-filter → gap analysis → LLM reasoning), 3 PG tables, 6th LangGraph sub-agent, 4 chat tools, 5 API endpoints, scheduler jobs, CLI command
- **Conversation Context PG Persistence** (ASETPLTFRM-303): new `conversation_contexts` table, cross-session resume via `get_latest_for_user()`, async NullPool save
- **Historical Portfolio Tools** (ASETPLTFRM-296): `get_portfolio_history` (daily value series with period/date range), `get_portfolio_comparison` (side-by-side period metrics + top movers)
- **stock_master auto-insert**: chat-discovered tickers auto-added to `stock_master` for pipeline scheduler pickup
- **Stock analyst news fallback**: deterministic `get_ticker_news` + `get_analyst_recommendations` call if LLM skips STEP 3
- **Observability**: `obs_collector` added to 7 FallbackLLM instances that were missing it (synthesis, classifier, summary, fact_extractor, sentiment, gap_filler)

### Fixed

- **Recommendation routing**: added "recommendation" (singular) to intent keyword map — fixes tie with "portfolio"
- **Recommendation hallucination**: skip LLM presentation pass for `skip_synthesis` agents — return raw tool output directly
- **Action-tier consistency**: "accumulate" only for held tickers; auto-correct to "buy" for non-held via validation + post-processing
- **Synthesis tool hallucination** (ASETPLTFRM-297): changed `[Tool result for X]:` prefix to `Data from X:` — prevents gpt-oss models hallucinating tool calls during synthesis
- **Iceberg freshness**: company_info 7 days (was same-day), analysis_summary 7 days, dividends 90-day cache before yfinance

### Changed

- **DuckDB migration complete**: all 16 remaining PyIceberg reads in `stocks/repository.py` migrated to DuckDB-first with PyIceberg fallback (internal helpers, portfolio, chat sessions, llm_usage, data gaps, insert dedup checks)
- **Recommendation engine**: Stage 3 LLM prompt now includes explicit ACTION DEFINITIONS (buy vs accumulate vs reduce)
- Pipeline orchestration: India + USA daily pipelines with DAG visualization
- Forecast pipeline: batch OHLCV 167s→0.87s, bulk writes 11.5min→2s

---

## [0.6.0] — 2026-04-08: Stock Data Pipeline — Nifty 500 (Sprint 5, Epic ASETPLTFRM-267)

### Added

- Stock Data Pipeline module (`backend/pipeline/`, 17 files) with 12 CLI commands
- 4 new PostgreSQL tables: `stock_master`, `stock_tags`, `ingestion_cursor`, `ingestion_skipped` (Alembic migration)
- 499 Nifty 500 stocks seeded from NSE with auto-tags (nifty50, nifty100, nifty500, largecap, midcap)
- Data sources: `NseSource` (jugaad-data), `YfinanceSource` (batch download), `RacingSource` (fastest-wins)
- Pipeline jobs: `ohlcv`, `fundamentals`, `fill_gaps`, `seed_universe` with crash-safe cursor tracking
- CLI commands: `download`, `seed`, `bulk`, `bulk-download`, `fundamentals`, `daily`, `fill-gaps`, `status`, `skipped`, `retry`, `correct`, `reset`
- Scripts: `download_nifty500.py`, `bulk_download_ohlcv.py`, `backfill_company_names.py`
- Shared `market_utils.py` for unified market detection (replaces 20+ ad-hoc checks)
- Frontend: analytics sparklines, merged ticker dropdowns (500+), Insights Screener for superusers, Stop button for scheduler jobs
- Forecast summary `?ticker=` param for unlinked tickers
- `docs/backend/stock-pipeline.md` usage guide

### Fixed

- `cache_warmup` poisoning from inconsistent ticker formats (registry disabled in Docker)
- Ticker standardization: all Indian stocks use `.NS` format across registry, Iceberg, scheduler, and frontend

### Changed

- Scheduler: `yf_map` resolution for `.NS` tickers, 519 India tickers visible
- Docker: `.pyiceberg.yaml` mounted, OHLCV price/sparkline enrichment on registry endpoint
- Analytics cards redesigned with sparkline, change%, action buttons (refresh, link, analysis, forecast)
- Analysis/Compare dropdowns merge registry + user tickers
- Dashboard: `indiaTickerSet` for market filtering

---

## [0.5.0] — 2026-04-01: Memory-Augmented Chat + Round-Robin + Observability

### Added
- Memory-augmented chat with pgvector semantic retrieval (ASETPLTFRM-266)
- Round-robin model pool cascade for load-balanced daily budgets (ASETPLTFRM-264)
- Synthesis pass in sub-agents — final response via gpt-oss-120b tier
- LLM Observability: 5-card summary, TPD/RPD bars, daily budget card (ASETPLTFRM-265)
- `suggest_sector_stocks` tool for sector-based stock discovery
- `GET /v1/admin/daily-budget` endpoint
- TokenBudget singleton + Iceberg TPD/RPD seeding on restart
- Frontend: session resume button, memory indicator, `startFromSession()`
- Docker: pgvector/pgvector:pg16 image, `ollama-profile embedding`
- UserMemory ORM model + Alembic migration with IVFFlat index

### Fixed
- Forecast accuracy NaN on first Prophet forecast (ASETPLTFRM-261)
- Auto-link ticker thread-local visibility in executor threads (ASETPLTFRM-262)
- bind_tools model_lookup stale after binding (pool routing without tools)
- UserMemory MetaData duplicate table error (extend_existing)
- Frontend Docker lightningcss Turbopack native module resolution

### Changed
- groq_model_tiers: added qwen/qwen3-32b + openai/gpt-oss-20b (~2.3M combined TPD)
- Docker postgres: postgres:16-alpine → pgvector/pgvector:pg16
- Frontend dev: native host (Docker profile "native-frontend")
- Est. queries: per-model sum instead of global average
- ObservabilityCollector: seeds per-model token counts from Iceberg on restart

---

## [Unreleased] — feature/sprint4

### Fixed — 2026-03-31: Stale Prices, Intent Routing, Anti-Hallucination (ASETPLTFRM-257, 259, 260)

**Stale data fix (ASETPLTFRM-257)**

- Removed file-based cache from `_analysis_shared.py` and
  `_forecast_shared.py`; added `_is_ohlcv_stale()` + yfinance
  auto-fetch fallback to `_load_ohlcv()`.
- Iceberg freshness gate now compares analysis_date vs latest OHLCV date.
- Forecast NaN accuracy guard (`math.isnan` check).
- Currency defaults to INR for `.NS`/`.BO` tickers (was USD).

**Intent-aware routing (ASETPLTFRM-257)**

- Extracted `best_intent()` / `score_intents()` from `router_node.py`.
- Guardrail follow-up: keyword check before LLM classifier; only
  reuse agent on same intent.
- `_merge_tickers()` + `_build_clarification()` for ambiguous switches.

**Anti-hallucination (ASETPLTFRM-257)**

- Query cache skips responses without tool_events.
- Hallucination guardrail rejects data-heavy responses with zero
  tool calls.
- Stock analyst: mandatory `get_ticker_news` +
  `get_analyst_recommendations` in Step 3.
- Tool call ID sanitization for Anthropic cascade
  (`_sanitize_tool_ids` in `llm_fallback.py`).

### Added — 2026-03-31: Interactive Stock Discovery (ASETPLTFRM-259)

- `suggest_sector_stocks` tool with Iceberg scan + popular fallback
  (8 sectors, ~40 stocks).
- `get_stocks_by_sector()` on `StockRepository`.
- DISCOVERY PIPELINE section in stock_analyst + portfolio agent prompts.
- Actions extraction (`<!--actions:[]-->`) in synthesis node;
  `response_actions` in graph state + WS `final` event.
- Frontend `ActionButtons` component + `sendDirect` hook.

### Changed — 2026-03-31: Token Optimization (ASETPLTFRM-260)

- Fixed iteration counter passthrough from sub_agents ReAct loop to
  `FallbackLLM` (compression was never triggered).
- Tool result truncation reduced: 2000 → 800 chars default,
  progressive 500 → 300.
- Summary-based context injection: raw history (~3K tokens) replaced
  with `ConversationContext.summary` (~100 tokens) for sub-agents.
- Intent switch sends system prompt + user query only (no prior
  agent history).

### Infrastructure — 2026-03-31

- IST timestamps in backend logs (`logging_config.py`).
- Removed `/app/.next` anonymous volume from
  `docker-compose.override.yml` (Turbopack cache corruption fix).
- "sector"/"sectors" added to `_STOCK_KEYWORDS` in `router.py`.
- `MAX_ITERATIONS` increased from 15 to 25.
- 18 new routing tests; 718-719 total passing, 2 pre-existing failures.

### Added — 2026-03-29: Hybrid DB Migration (ASETPLTFRM-225, Epic 24 SP)

**New components**

- `backend/db/engine.py` — SQLAlchemy 2.0 async engine with asyncpg
  driver; `session_factory` used across all PG repositories.
- `backend/db/models.py` — 5 ORM models: `User`, `UserTicker`,
  `PaymentTransaction`, `StockRegistry`, `ScheduledJob`.
  FK cascade, composite PK, JSONB columns, covering indexes.
- `backend/db/migrations/` — Alembic async migration environment;
  initial schema migration applied to Docker PostgreSQL.
- `backend/db/user_repository.py` — `UserRepository` facade
  replacing `IcebergUserRepository` for all OLTP auth operations.
- `backend/db/pg_stocks.py` — async upsert functions for
  `stocks.registry` and `stocks.scheduled_jobs`.
- `backend/db/duckdb_engine.py` — DuckDB query layer foundation
  for running analytical queries directly against Iceberg parquet.
- `scripts/migrate_iceberg_to_pg.py` — one-time migration script
  that moves 5 tables from Iceberg → PostgreSQL.

**Migrated to PostgreSQL (OLTP)**

- `auth.users`, `auth.user_tickers`, `auth.payment_transactions`
- `stocks.registry`, `stocks.scheduled_jobs`

**Stays on Iceberg (OLAP — 14 tables)**

- All analytics and append-only tables: `ohlcv`, `company_info`,
  `dividends`, `technical_indicators`, `analysis_summary`,
  `forecast_runs`, `forecasts`, `quarterly_results`, `llm_pricing`,
  `llm_usage`, `scheduler_runs`, `audit_log`, `usage_history`,
  `portfolio_transactions`

**Auth async conversion**

- 37 functions across 11 files converted to `async def`.
- All auth endpoints, OAuth handlers, and callers updated.
- `IcebergUserRepository` retained as façade; internally delegates
  to `UserRepository` (SQLAlchemy) for OLTP tables.

**Health check**

- `GET /v1/health` now includes `postgresql` connectivity status.

**Tests**

- 30 new tests added (all passing).
- 652/666 existing tests passing; 14 failures are pre-existing
  and unrelated to the migration.

---

## [0.5.0] — 2026-03-29: Ollama + Containerization (Sprint 4, 43 SP)

### Added

- Ollama local LLM as Tier 0 in `FallbackLLM` cascade
  (`backend/ollama_manager.py`, `backend/llm_fallback.py`)
- `ollama-profile` CLI for switching between Qwen (coding) and
  GPT-OSS 20B (reasoning) profiles
- Docker containerization: `Dockerfile.backend`,
  `Dockerfile.frontend`, `docker-compose.yml`,
  `docker-compose.override.yml`, `.env.example`
- Chat UX improvements: auto-scroll, input focus, markdown
  formatting, tool calls header
- Admin REST endpoints: `GET/POST /v1/admin/ollama/{status,load,unload}`

### Fixed

- Billing redirect session loss (SameSite cookie on payment return)
- Payment success handler no longer blocks on token refresh
- Forecast chart null price crash on crosshair hover

---

## [0.4.0] — 2026-03-28: Scheduler Overhaul (Sprint 4)

### Added

- Scheduler catch-up on startup (ASETPLTFRM-216)
- Scheduler timezone fix — removed erroneous IST→UTC conversion
  (ASETPLTFRM-217)
- Scheduler edit jobs UI (ASETPLTFRM-218)
- Day-of-month scheduling support (ASETPLTFRM-219)
- Admin Transactions bug fix (ASETPLTFRM-220)
- Auto-create Iceberg tables on startup (ASETPLTFRM-221)

---

## [0.3.0] — 2026-03-16: Dashboard Overhaul + Dash→Next.js Migration

### Added

- Native Next.js portfolio dashboard (TradingView lightweight-charts
  + react-plotly.js); Dash iframe removed from main routes
- Dual payment gateways: Razorpay (INR) + Stripe (USD)
- Per-ticker refresh, Redis cache layer, subscription billing
- Full RBAC + OAuth PKCE auth flows

---

## [0.2.0] — 2026-03-09: Agentic Framework + LangGraph

### Added

- LangGraph supervisor with Portfolio, Stock Analyst, Forecaster,
  and Research sub-agents
- N-tier Groq → Anthropic LLM cascade with token budget
- LangSmith observability integration

---

## [0.1.0] — Initial release

- FastAPI backend with basic LangChain agentic loop
- Next.js frontend with chat panel
- Apache Iceberg data layer for all stock + auth data
- JWT authentication with Redis deny-list

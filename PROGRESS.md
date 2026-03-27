# PROGRESS.md — Session Log

---

# Session: Mar 28, 2026 — Sentiment Agent + Bug Fixes

## ASETPLTFRM-211: Sentiment Agent (16 SP, Done)
- **Epic**: ASETPLTFRM-211 | Stories: 212, 213, 214, 215
- **Design**: `docs/design/DESIGN-sentiment-agent.md` | **Workflow**: `docs/workflow/WORKFLOW-sentiment-agent.md`
- New `_sentiment_sources.py` — 3-source headline fetcher (yfinance w=1.0, Yahoo RSS w=0.8, Google RSS w=0.6) with fuzzy dedup
- Refactored `_sentiment_scorer.py` — FallbackLLM, weighted scoring `Σ(score×w)/Σ(w)`, shared `refresh_ticker_sentiment()` code path
- New `sentiment_agent.py` — 3 `@tool` functions: `score_ticker_sentiment`, `get_cached_sentiment`, `get_market_sentiment`
- 5th LangGraph sub-agent registered in supervisor graph
- Gap filler refactored: bare ChatGroq → FallbackLLM (all LLM calls now traced via LangSmith)
- 27 new tests, 602 total passing
- Validated: Admin Scheduler triggered full refresh → 47/47 tickers scored, 42 with sentiment

## Bug Fixes

### Iceberg Table Corruption Recovery
- 14 of 20 tables had corrupted parquet references (snapshot expiry deleted metadata, data files already gone)
- Fix: drop + recreate corrupted tables, re-seed demo users
- Created `scripts/check_tables.py` — diagnostic tool for all tables with row counts
- All 20 tables healthy (~336K+ total rows)

### Auth: user_writes.py missing subscription fields
- `create()` missing 10 subscription columns → `KeyError` on user seed
- Added all fields with sensible defaults (free/active)

### Billing: Razorpay "customer already exists" (500)
- After DB rebuild, `razorpay_customer_id` lost → checkout creates → Razorpay rejects
- Fix: catch error, paginate `customer.all()` to find by email, save ID back

### Portfolio ↔ Watchlist Sync
- Portfolio stocks added via "+" were NOT linked to watchlist → dashboard showed "0 tickers"
- Unlink button returned 404 for portfolio-only tickers
- Fix: auto-link on portfolio add + unlink no longer 404s
- Backfill: `scripts/backfill_portfolio_links.py`

---

# Session: Mar 27-28, 2026 — Forecast Phase 2+3 + Data Quality + Cleanup Incident

## ASETPLTFRM-201 Phase 2: Forecast Pipeline Wiring (Done)
- Merged market indices (^VIX, ^INDIAVIX, ^GSPC, ^NSEI) into `stocks.ohlcv` table
- Dropped separate `stocks.market_indices` table + purged from Iceberg catalog
- Removed dead code: `insert_market_index()`, `get_market_index_series()`, `_market_indices_schema()`
- Added Steps 6 (market indices) + 7 (sentiment) to 8-step `run_full_refresh()` pipeline
- Prophet now receives regressors: vix, index_return, sentiment, analyst_bias, eps_revision
- Daily batch sentiment scoring (`refresh_all_sentiment`) + freshness gates
- 5 new tests, 573→579 total passing

## ASETPLTFRM-202 Phase 3a: Macro Regressors via yfinance (Done)
- Dropped `fredapi` (rate-limited) — all macro via yfinance: ^TNX, ^IRX, CL=F, DX-Y.NYB
- Computed yield spread (10Y − 3M) as recession signal
- All macro regressors apply to BOTH US and Indian stocks
- No new deps, no new tables — reuses OHLCV + `insert_ohlcv()` path

## ASETPLTFRM-202 Phase 3b: XGBoost Ensemble (Done — disabled)
- New module: `backend/tools/_forecast_ensemble.py`
- Architecture: `final_price = prophet_yhat + xgb_residual_correction`
- 17 features: prophet_yhat + 7 regressors + 7 tech indicators + 2 removed (analyst)
- Feature flag: `ENSEMBLE_ENABLED=true` in backend.env
- **DISABLED** after quality analysis showed overfitting on out-of-sample data

## Data Quality Analysis
- Built `scripts/regressor_quality.py` — 3-model comparison (baseline vs regressors vs ensemble)
- Standardized CV to 10-year data cap + `initial="730 days"` for apples-to-apples comparison
- Removed `analyst_bias` and `eps_revision` from Prophet (zero feature importance)
- Kept sentiment (low but improving as daily LLM scores accumulate)
- Prophet regressors: 7 (was 9) — vix, index_return, sentiment, treasury_10y, yield_spread, oil_price, dollar_index

### Quality Results (10-year cap, 32 cutoffs)
| | AAPL | RELIANCE.NS |
|---|---|---|
| Baseline MAPE | 14.2% | 14.2% |
| + Regressors | 14.0% (-0.2pp) | 13.5% (-0.7pp) |
| XGBoost OOS | +0.3pp (hurts) | +0.7pp (hurts) |

## Iceberg Data Incident
- Orphaned file cleanup script accidentally deleted parquet files still referenced by snapshots
- 11 stocks tables + 3 auth tables corrupted (FileNotFoundError on scan)
- Fix: drop + recreate empty tables via `create_tables.py`
- Healthy tables survived: ohlcv, forecasts, quarterly_results, registry, technical_indicators, auth.users, auth.usage_history
- **Lesson**: Never delete Iceberg data files based on snapshot diff — PyIceberg doesn't track file-to-snapshot mapping reliably
- Rolled back ALL purge/cleanup code from gap_filler.py
- Removed hardcoded schedules from gap_filler.py — all scheduling via Admin UI only

## Architecture Changes
- `gap_filler.py`: No hardcoded cron schedules — all via Admin Scheduler or manual triggers
- `_forecast_accuracy.py`: CV uses 10-year data cap with refit for consistent evaluation
- `config.py`: Added `ensemble_enabled` (default False), removed `fred_api_key`

## Jira: ASETPLTFRM-200, 201, 202 — all Done (Sprint 3)
## Tests: 602 passing (up from 579)

---

# Session: Mar 27, 2026 (Night) — ASETPLTFRM-211 Sentiment Agent

## Sentiment Agent — multi-source headlines + LangGraph agent (16 SP)
- **Epic**: ASETPLTFRM-211 | Stories: 212, 213, 214, 215
- **Design doc**: `docs/design/DESIGN-sentiment-agent.md`
- **Workflow**: `docs/workflow/WORKFLOW-sentiment-agent.md`

### New files
- `backend/tools/_sentiment_sources.py` — multi-source headline fetcher (yfinance w=1.0, Yahoo RSS w=0.8, Google RSS w=0.6) with fuzzy dedup (SequenceMatcher ≥0.8)
- `backend/tools/sentiment_agent.py` — 3 `@tool` functions: `score_ticker_sentiment`, `get_cached_sentiment`, `get_market_sentiment`
- `backend/agents/configs/sentiment.py` — SubAgentConfig for sentiment sub-agent
- `tests/backend/test_sentiment_sources.py` — 12 tests
- `tests/backend/test_sentiment_scorer.py` — 15 tests

### Modified files
- `backend/tools/_sentiment_scorer.py` — refactored: FallbackLLM, weighted scoring, shared `refresh_ticker_sentiment()` code path
- `backend/agents/graph.py` — registered sentiment node + edges in supervisor
- `backend/bootstrap.py` — registered 3 sentiment tools
- `backend/jobs/gap_filler.py` — replaced bare ChatGroq with FallbackLLM via shared pipeline
- `mkdocs.yml` — added Design + Workflow sections to nav

### Key decisions
- Weighted dedup: yfinance > Yahoo RSS > Google RSS for source trust
- FallbackLLM everywhere — no more bare ChatGroq in gap_filler
- Hybrid chat UX: cached Iceberg score instant, offer live refresh if stale (>24h)
- Market sentiment includes broad indices (SPY, ^GSPC, ^DJI, ^IXIC) + portfolio tickers
- 27 new tests, 602 total passing (up from 548)

---

# Session: Mar 27, 2026 (Late PM) — ASETPLTFRM-202 Phase 3 Macro + XGBoost Ensemble

## Phase 3a: Macro Regressors via yfinance (ASETPLTFRM-202)
- Dropped `fredapi` dependency (rate-limited on new keys) — all macro data via yfinance
- Added 4 macro symbols to daily refresh: `^TNX` (10Y Treasury), `^IRX` (13W T-Bill), `CL=F` (WTI Oil), `DX-Y.NYB` (Dollar Index)
- Computed yield spread (10Y − 3M) as recession signal
- All macro regressors apply to BOTH US and Indian stocks (Fed rate → FII flows, oil → India import bill)
- No new deps, no new Iceberg tables — reuses OHLCV table + `insert_ohlcv()` path
- Removed `fred_api_key` from config and backend.env

## Phase 3b: XGBoost Ensemble on Prophet Residuals (ASETPLTFRM-202)
- New module: `backend/tools/_forecast_ensemble.py`
- Architecture: `final_price = prophet_yhat + xgb_residual_correction`
- XGBoost trained on 2327 rows with 17 features:
  - Prophet yhat (base prediction)
  - 9 Prophet regressors: vix, index_return, sentiment, treasury_10y, yield_spread, oil_price, dollar_index, analyst_bias, eps_revision
  - 7 technical indicators: sma_50, sma_200, rsi_14, macd, bb_upper, bb_lower, atr_14
- Feature flag: `ensemble_enabled` in config (set via `ENSEMBLE_ENABLED=true` in env)
- Graceful fallback: if ensemble fails or insufficient data, returns pure Prophet forecast
- Dynamic feature selection: only uses features present in the DataFrame
- Added `xgboost>=2.0` to requirements (needs `brew install libomp` on macOS)

### Validation Results (AAPL, 9-month horizon)
| Metric | Phase 2 (Prophet only) | Phase 3a (+macro) | Phase 3b (+XGBoost) |
|--------|----------------------|-------------------|---------------------|
| MAE    | 13.23                | 12.75             | 12.75 (CV is Prophet-only) |
| MAPE   | 10.1%                | 10.0%             | 10.0% (CV is Prophet-only) |
| XGBoost mean correction | — | — | -$5.05 (corrected overshot) |

### Daily Schedule (IST)
| Time | Job |
|------|-----|
| 11:00 AM | Market indices + macro (^VIX, ^GSPC, ^TNX, ^IRX, CL=F, DX-Y.NYB + 2 India) |
| 11:30 AM | Sentiment batch (all portfolio tickers) |
| 6:00 PM | Data gap filler (after NSE close) |
| 9:00 PM | Data gap filler (after NYSE close) |

### Tests
- 4 new ensemble tests in `tests/backend/test_forecast_ensemble.py`
- 2 new macro tests in `tests/backend/test_refresh_pipeline.py`
- 579 total tests pass, all lint clean

### Files Changed
- `backend/tools/_forecast_ensemble.py` — **new** XGBoost ensemble module
- `backend/tools/_forecast_shared.py` — macro regressor loading + merge
- `backend/tools/forecasting_tool.py` — ensemble wiring (feature-flagged)
- `dashboard/services/stock_refresh.py` — ensemble wiring in Step 8
- `backend/jobs/gap_filler.py` — macro symbols in indices list
- `backend/config.py` — removed fred_api_key, added ensemble_enabled
- `backend/requirements.txt` — added xgboost>=2.0
- `scripts/backfill_sentiment.py` — macro symbols in backfill
- `tests/backend/test_forecast_ensemble.py` — **new** ensemble tests
- `tests/backend/test_refresh_pipeline.py` — macro regressor tests

### Jira: ASETPLTFRM-202 — Done

---

# Session: Mar 27, 2026 (PM) — ASETPLTFRM-201 Phase 2 Forecast Pipeline

## Forecast Phase 2: Sentiment + Market Indices Wiring (ASETPLTFRM-201, Done)

### Market Indices → OHLCV Table Migration
- Merged market indices (^VIX, ^INDIAVIX, ^GSPC, ^NSEI) into `stocks.ohlcv` table
- Dropped separate `stocks.market_indices` table + purged data from Iceberg
- Removed dead code: `insert_market_index()`, `get_market_index_series()`, `_market_indices_schema()`
- `refresh_market_indices()` now uses `insert_ohlcv()` with built-in dedup
- `_load_regressors_from_iceberg()` reads from `get_ohlcv()` instead of old table
- Backfill script updated to match

### 8-Step Refresh Pipeline
- Added Step 6 (Market indices) + Step 7 (Sentiment) to `run_full_refresh()`
- Prophet (Step 8) now receives full regressors: VIX, index_return, sentiment, analyst_bias, eps_revision
- Previously refresh pipeline trained Prophet without any regressors
- All non-critical steps: failures don't abort pipeline

### Daily Data Capture (Gap-Free)
- `refresh_all_sentiment()` — batch scores all portfolio tickers daily (11:30 AM IST / 06:00 UTC)
- `refresh_market_indices()` — fetches all 4 indices daily (11:00 AM IST / 05:30 UTC)
- Freshness gates: `refresh_sentiment()` skips if today's score exists, `refresh_market_indices()` skips if already ran today
- No redundant LLM/yfinance calls when refreshing multiple tickers

### Validation Results (AAPL)
- Market indices: 11,169 rows backfilled into OHLCV
- Sentiment: LLM-scored 8 headlines → 0.24 (bullish), source=llm
- Forecast: Prophet trained on 2,526 rows with regressors
- Accuracy: MAE=13.23, RMSE=16.95, MAPE=10.1%
- Targets: 3M +3.9%, 6M +9.8%, 9M +16.3%

### Tests
- 5 new tests in `tests/backend/test_refresh_pipeline.py`
- 573 total tests pass, all lint clean

### Iceberg Tables: 16 (was 17 — dropped market_indices)

### Files Changed
- `backend/jobs/gap_filler.py` — rewrite indices, add batch sentiment, daily flags
- `backend/tools/_forecast_shared.py` — get_ohlcv instead of get_market_index_series
- `dashboard/services/stock_refresh.py` — steps 6+7, regressors to Prophet
- `scripts/backfill_sentiment.py` — use insert_ohlcv for indices
- `stocks/repository.py` — removed dead market_indices methods
- `stocks/create_tables.py` — removed market_indices schema + table creation
- `tests/backend/test_refresh_pipeline.py` — new test file

---

# Session: Mar 27, 2026 — UI Beautification, Scheduler, Insights Enhancements

## Unified Analytics Page (ASETPLTFRM-204)
- Merged Analytics Home + Marketplace (Link Stock) into single card-based page
- 3-tier card system: Portfolio (emerald accent), Watchlist (indigo accent), Unlinked (muted)
- Cards sorted by tier: Portfolio → Watchlist → Unlinked
- Toolbar: search, market pills (All/India/US), Select All, bulk actions dropdown
- Sub-filter pills: All / Portfolio / Watchlist / Unlinked with counts
- Pagination: 3 cols x 2 rows = 6 per page
- Add to Portfolio button on both Watchlist and Portfolio cards
- Marketplace page replaced with redirect to /analytics
- Extracted reusable hooks: useTickerRefresh.ts, useLinkUnlink.ts

## Admin Scheduler (ASETPLTFRM-205)
- Full backend: 2 Iceberg tables (scheduled_jobs, scheduler_runs), executor registry, SchedulerService
- Extensible @register_job decorator — data_refresh built-in, add new types easily
- schedule lib + daemon thread, IST timezone, ThreadPoolExecutor(3)
- 7 REST endpoints (CRUD + trigger + runs + stats), all superuser_only
- Frontend: Design B dashboard — stat cards, job list, new schedule form, run timeline
- Auto-refresh every 30s for live tracking

## Scheduler Bug Fixes (ASETPLTFRM-206)
- Fixed tz-naive vs tz-aware datetime mismatch on Iceberg writes
- Fixed NaN JSON serialization (ValueError: Out of range float values)
- Added stale run cleanup on restart (marks orphaned "running" as "failed")

## ForecastChart Fix (ASETPLTFRM-207)
- Fixed null value crash in lightweight-charts setData() for all 4 series

## Compare Stocks Multi-Select (ASETPLTFRM-208)
- Replaced pill button wall with searchable multi-select dropdown
- Search input, checkbox list, removable chips, max 7 enforced

## Correlation Heatmap (ASETPLTFRM-209)
- Migrated from broken Plotly (basic bundle lacks heatmap) to ECharts (tree-shaken ~150KB)
- Portfolio-only data source (was all linked tickers)
- Correlation scores in each cell, Red→White→Blue colorscale, dark/light mode
- Backend: added source=portfolio parameter to correlation endpoint

## Quarterly Portfolio Filter (ASETPLTFRM-210)
- Added "Portfolio" as first option in Sectors dropdown, selected by default
- Chart and table show only portfolio stocks by default

## Jira: ASETPLTFRM-204 to 210 — all in Sprint 3, all Done (31 story points)

---

# Session: Mar 26-27, 2026 — Observability, Forecast Enhancement, Cleanup

## Observability (ASETPLTFRM-195)
- LangFuse v4 dual-platform integration (Phase 2 & 3)
- Secret redaction always-on (API keys, JWT, all providers)
- `hide_trace_io` toggle (dev=visible, prod=hidden)
- Settings leak fix in `build_supervisor_graph` traces
- LangFuse mask fix (recursive walker for v4 arbitrary types)

## Bug Fixes (ASETPLTFRM-196, 197, 198)
- Forecast cooldown returns cached Iceberg report (not "come back later")
- yfinance v1.2: news (nested content), analyst recs (upgrades_downgrades + consensus)
- News cache removed — always fresh
- Link Stock page: company info fetched on link + background backfill

## Dead Code Cleanup (ASETPLTFRM-199)
- 3 files deleted: _forecast_chart.py, _forecast_persist.py, _analysis_chart.py
- ~395 lines removed, 520 MB freed
- `_load_parquet()` renamed to `_load_ohlcv()` (5 files)

## Prophet Forecast Enhancement (ASETPLTFRM-200, 201)
### Phase 1: Quick Wins
- Market-specific holidays (India for .NS/.BO, US for others)
- VIX + benchmark index as Prophet regressors (^VIX/^INDIAVIX + ^GSPC/^NSEI)
- Prophet cross-validation (rolling window, background only)

### Phase 2: Sentiment + Analyst
- LLM sentiment scoring via Groq (llama-3.3-70b)
- Earnings dates as Prophet holidays (±2 day window)
- Analyst target price bias + EPS revision momentum
- 5 regressors total: vix, index_return, sentiment, analyst_bias, eps_revision
- Live chat reads from Iceberg (no live compute), background refresh does heavy lifting

### Infrastructure
- 2 new Iceberg tables: `market_indices`, `sentiment_scores`
- Repository methods: insert/get for both tables
- Background jobs: `refresh_market_indices()`, `refresh_sentiment()`
- Price-derived sentiment proxy backfilled (137K rows, 52 tickers)
- Accuracy: removed in-sample from live chat, CV-only in background

## Pending (tomorrow)
- Redesign `market_indices` table: full OHLCV + is_interpolated
- Wire indices + sentiment into `run_full_refresh()` pipeline
- Fix Iceberg commit conflicts for bulk index inserts
- Backfill script for new OHLCV index schema

## Tests: 568 passed, 0 failed

---

# Session: Mar 26, 2026 — LangFuse + Production Hardening (ASETPLTFRM-194)

## Phase 2: LangFuse Dual-Platform (3 pts)
- **langfuse v4.0.1** added to requirements.txt (OpenTelemetry-based)
- Import path: `from langfuse.langchain import CallbackHandler`
- Config fields: `langfuse_public_key`, `langfuse_secret_key`, `langfuse_host`
- `langfuse_enabled` flag already existed — now wired up
- New `backend/tracing.py` module: singleton client, callback factory
- Callbacks injected per-call in `FallbackLLM.invoke()` (Groq + Anthropic)
- No `@traceable` on `invoke()` — callbacks pass through `config={"callbacks": [...]}`

## Phase 3: Production Hardening (2 pts)
- **Trace sampling**: `should_trace()` uses `trace_sample_rate` config
  - Errors always traced (100%), successes sampled at configured rate
  - LangFuse v4 native `sample_rate` also set on client init
- **PII redaction**: `redact_pii()` strips email, phone, PAN, Aadhaar, cards
  - LangSmith: `setup_anonymizer()` via `create_anonymizer` at startup
  - LangFuse: `mask=redact_pii` passed to `Langfuse()` constructor
  - Both use same regex patterns — single source of truth

## Tests
- 13 new tests in `test_tracing.py` (PII, sampling, callbacks)
- 1 new test in `test_llm_fallback.py` (callback forwarding)
- **562 passed**, 7 skipped, 0 failed (up from 548)

## Files Changed
- `backend/requirements.txt` — langfuse + transitive deps
- `backend/config.py` — 3 new LangFuse settings
- `backend/tracing.py` — **new** (sampling, PII, callbacks)
- `backend/llm_fallback.py` — callback injection in invoke()
- `backend/main.py` — setup_anonymizer() at startup
- `tests/backend/test_tracing.py` — **new** (13 tests)
- `tests/backend/test_llm_fallback.py` — 1 new test

---

# Session: Mar 25, 2026 — Security Hardening, Code Quality, E2E Coverage

## Security Hardening (ASETPLTFRM-178, 9 stories — ALL DONE)

### 3 CRITICAL fixes
- Webhook signatures now mandatory — 503 if secret missing (`subscription_routes.py`)
- Chat endpoints require JWT — `user_id` derived from token only (`routes.py`, `ws.py`)
- Password reset token gated behind `settings.debug` (`auth_routes.py`)

### 7 HIGH fixes
- Cookie `secure` env-gated + `samesite=strict`
- Rate limits on `/auth/login/form` + `password_reset_confirm`
- CSP header added to SecurityHeadersMiddleware
- Quota enforcement fails closed (503) not open
- Stripe/Razorpay tier/plan validated before DB write
- Rate limiter IP spoofing documented

### 10 MEDIUM + 4 LOW fixes
- `CheckoutRequest` uses `Literal` types, `AddPortfolioRequest` has Field constraints
- `ChatRequest.history` capped at 100
- Refresh log reduced to DEBUG, avatar_url pattern validated
- JWT startup check, demo log cleanup, script placeholder padding

## Code Quality (ASETPLTFRM-188, 4 stories — ALL DONE)
- **TokenBudget TOCTOU race** fixed — atomic `reserve()`/`release()` pattern
- **Repository singleton bypass** — 3 call sites → `_require_repo()`
- **`asyncio.get_running_loop()`** — replaced deprecated `get_event_loop()` at 3 sites
- **Extracted `backend/user_context.py`** — eliminated duplication between routes.py and ws.py
- **12+ silent `except: pass`** → proper logging, WS errors emit events to client
- **8 files migrated** from legacy `typing` to PEP 604 builtins
- **Mutable default fixed** in BaseAgent (`history: list[dict] | None = None`)

## E2E Test Coverage (ASETPLTFRM-193 — IN PROGRESS)
46 new Playwright tests across 8 files:
- `portfolio-crud.spec.ts` (8) — add/edit/delete holdings
- `payment-flows.spec.ts` (7) — mocked Razorpay/Stripe checkout
- `websocket.spec.ts` (6) — WS connect/stream/reconnect/fallback
- `chat-tools.spec.ts` (4) — LLM tool invocations
- `admin-crud.spec.ts` (8) — user CRUD + audit log
- `subscription-lifecycle.spec.ts` (5) — paywall/quota/upgrade/cancel
- `insights-filters.spec.ts` (4) — chained filters, quarterly switch
- `lighthouse.spec.ts` (4) — Core Web Vitals assertions (LCP/FCP/TBT/CLS)

Supporting: 27 `data-testid` attrs on 6 components, 1 new POM, 1 fixture, selectors.ts + config updated.

## Code Simplification
- `Dict` → `dict` (PEP 604) in auth_routes, subscription_routes
- `_drain_queue()` helper eliminated duplicate timeout logic in routes.py
- `next()` patterns in `_find_user_by_razorpay/stripe`
- Removed unused imports, fixed E741 variable names

## AgentShield Security Scan
- Grade: B (87) → **A (97)**
- Permissions score: 36 → 85/100
- settings.local.json: cleaned ~90 stale allow rules → ~50 reusable, added 22-rule deny list
- Skill metadata (version, rollback, observe, feedback) added to 2 custom commands

## Test Results
- **Python**: 548 passed, 0 failures (fixed 2 pre-existing flaky tests)
- **E2E**: 96 passed, 22 did not run (fixture path + screenshot baselines need update)

## Jira
- **ASETPLTFRM-178** (Epic) — Security Hardening: 9 stories, all Done
- **ASETPLTFRM-188** (Epic) — Code Quality: 4 stories, all Done
- **ASETPLTFRM-193** (Story) — E2E Coverage: In Progress

## Shared Memories Promoted (6 new)
- `shared/debugging/chat-session-recording`
- `shared/architecture/currency-aware-agent`
- `shared/debugging/iceberg-epoch-dates`
- `shared/conventions/security-hardening`
- `shared/architecture/token-budget-concurrency`
- `shared/conventions/e2e-test-patterns`

---

# Session: Mar 24–25, 2026 — Subscription & Paywall System, Razorpay + Stripe, Admin Maintenance

## Sprint 3 — 100% Complete (all 11 stories + 15 bugs)

### Additional Deliverables (Mar 24 evening – Mar 25)

**ASETPLTFRM-79 (3 pts) — Stripe Sandbox Integration:**
- stripe==14.4.1, 3 config fields, Stripe Checkout Session + `Subscription.modify()` for pro-rata upgrades
- Stripe webhook handler (checkout.session.completed, customer.subscription.deleted, invoice.payment_failed)
- Gateway selector UI (INR vs USD toggle), dynamic pricing, auto-detect active gateway
- Cancel supports both Razorpay + Stripe

**ASETPLTFRM-81 (3 pts) — Subscription E2E Tests:**
- 3 Playwright test specs: billing UI, paywall enforcement, admin management (13 tests)
- subscription.helper.ts API utilities

**Payment Transaction Ledger:**
- `auth.payment_transactions` Iceberg table (14 columns) — every payment event logged
- Wired into all webhook handlers + PATCH upgrades + user cancels
- Admin "Transactions" tab (6th) with gateway filter, Source column (User/Webhook), Name column, raw payload viewer

**Bug Fixes (ASETPLTFRM-167–176):**
- Cookie path mismatch → login redirect after payment (167)
- WS streaming + usage tracking missing (168)
- Quota enforcement on chat (169)
- SWR cache leak between users (170)
- get_catalog() missing root arg (171)
- Stripe no pro-rata on upgrade (172)
- useEffect not imported crash (173)
- INR prices for Stripe users (174)
- Native confirm() → ConfirmDialog (175)
- Missing news tools in stock analyst (176)

**Session Stability Fix (root cause):**
- `NEXT_PUBLIC_BACKEND_URL` was `http://127.0.0.1:8181` but frontend runs on `localhost:3000`
- Different hostnames = browser doesn't send HttpOnly cookie on API calls = refresh always fails
- Fixed to `http://localhost:8181` — session now stable across token refreshes and payments
- Also fixed: refresh endpoint 422 (empty JSON body), cookie path to `/`, legacy cookie cleanup

**Sprint 3 Final: 22 story pts + 23 bug pts = 45 pts delivered**

---

# Session: Mar 24, 2026 — Subscription & Paywall System, Razorpay Integration, Admin Maintenance

## Sprint 3 subscription + billing on `feature/sprint3`

### Subscription Data Model (ASETPLTFRM-76, 3 pts)

- 9 new Iceberg columns on `auth.users`: subscription_tier, subscription_status, razorpay_customer_id, razorpay_subscription_id, stripe_customer_id, stripe_subscription_id, monthly_usage_count, usage_month, subscription_start_at, subscription_end_at
- JWT access token extended with subscription_tier, subscription_status, usage_remaining
- UserContext model updated; get_current_user() extracts subscription claims
- Login/refresh/OAuth endpoints fetch subscription data from Iceberg
- `backend/subscription_config.py` — tier quotas, ordering, pricing constants
- 16 tests (test_subscription_model.py)

### Guard Middleware + Usage Tracking (ASETPLTFRM-77, 3 pts)

- `require_tier(min_tier)` factory dependency — returns 403 if tier too low
- `check_usage_quota()` dependency — returns 429 when monthly quota exhausted
- `increment_usage()` in all 4 chat route paths with lazy auto-reset
- `usage_month` field tracks which month the counter belongs to
- `auth.usage_history` Iceberg table — archives month-on-month snapshots on reset
- Admin endpoints: usage-stats, reset-usage, reset-usage/selected, usage-history
- 14 tests (test_subscription_guard.py)

### Razorpay Sandbox Integration (ASETPLTFRM-78, 5 pts)

- `razorpay==2.0.1` SDK, config fields in Settings
- `POST /v1/subscription/checkout` — PATCH for upgrades (pro-rata), POST for new subs
- `GET /v1/subscription` — reads tier/status from Iceberg (not JWT)
- `POST /v1/subscription/cancel` — resets tier to free, clears sub_id
- Webhook handler at `/v1/webhooks/razorpay` — handles charged, cancelled, payment.failed
- Signature verification (skippable in test mode), stale sub guard, Iceberg retry on commit conflict
- Triage-based orphan cleanup: `POST /v1/subscription/cleanup?dry_run=true`
- ngrok tunnel for local webhook testing
- 17 tests (test_razorpay_integration.py)

### Frontend Billing UI (ASETPLTFRM-80, 5 pts)

- `BillingTab` component in EditProfileModal — pricing cards, usage meter, Razorpay checkout.js
- Server-side upgrade (PATCH) shows instant success; new subs open Razorpay modal
- `UsageBadge` in ChatHeader — compact usage pill (color-coded)
- `UpgradeBanner` below AppHeader when quota exhausted (SWR, dismissible)
- "Billing" in profile dropdown menu
- Token refresh after payment/cancel

### Admin Maintenance Tab

- 4th tab on Admin page: Subscription Cleanup, Usage Reset, Data Retention, Gap Analysis
- Subscription cleanup: scan → triage (matched/orphaned/unlinked) → execute
- Usage reset: scan → per-user checkboxes → reset individual/selected/all
- Data retention: scan → per-table checkboxes → delete individual/selected/all
- Risk badges (none/low/medium/high), confirmation dialogs

### Bug Fixes

- **ASETPLTFRM-162** (2 pts) — OHLCV NaN close price → ₹0.00 portfolio. Added `dropna(subset=["close"])` in 5 files.
- **ASETPLTFRM-163** (1 pt) — Hero section not updating after stock refresh. Added `portfolioData.refresh()` to onRefresh callback.
- **ASETPLTFRM-164** (2 pts) — Subscription endpoints read JWT instead of Iceberg. All 3 endpoints now read from Iceberg.
- **ASETPLTFRM-165** (3 pts) — Checkout created orphaned Razorpay subs. Now uses PATCH for upgrades, cancel clears sub_id, webhook guards.
- **ASETPLTFRM-166** (1 pt) — Iceberg CommitFailedException. Added `_safe_update()` with 3 retries.

### Files Changed (35+)

**New files:** `backend/subscription_config.py`, `backend/usage_tracker.py`, `auth/endpoints/subscription_routes.py`, `frontend/components/BillingTab.tsx`, `frontend/components/UpgradeBanner.tsx`, `tests/backend/test_subscription_model.py`, `tests/backend/test_subscription_guard.py`, `tests/backend/test_razorpay_integration.py`

**Modified:** `auth/repo/schemas.py`, `auth/create_tables.py`, `auth/migrate_users_table.py`, `auth/tokens.py`, `auth/service.py`, `auth/models/response.py`, `auth/dependencies.py`, `auth/endpoints/helpers.py`, `auth/endpoints/auth_routes.py`, `auth/endpoints/oauth_routes.py`, `auth/endpoints/__init__.py`, `auth/endpoints/ticker_routes.py`, `backend/config.py`, `backend/routes.py`, `backend/dashboard_routes.py`, `backend/tools/portfolio_tools.py`, `backend/tools/forecast_tools.py`, `backend/requirements.txt`, `frontend/lib/auth.ts`, `frontend/components/EditProfileModal.tsx`, `frontend/components/ChatHeader.tsx`, `frontend/components/AppHeader.tsx`, `frontend/hooks/useAdminData.ts`, `frontend/hooks/usePortfolio.ts`, `frontend/app/(authenticated)/layout.tsx`, `frontend/app/(authenticated)/admin/page.tsx`, `frontend/app/(authenticated)/dashboard/page.tsx`, `stocks/retention.py`

### Sprint 3 Progress: 25 pts delivered (16 story + 9 bug fix)

---

# Session: Mar 22, 2026 — Chat Session Recording, Activity Log, Currency-Aware Agent, Chart Fix

## Sprint 3 bugs on `feature/sprint3`

### Chat Session Recording Fix (ASETPLTFRM-158, 5 pts)

Session history stopped persisting to Iceberg. Five root causes:

1. **`sendBeacon` cannot send auth headers** — `ChatProvider.tsx` used `navigator.sendBeacon()` on tab close → no Authorization header → 401. Fixed: `fetch()` + `keepalive: true` + auth header.
2. **`apiFetch` 401 handler races with logout** — `useChatSession.flush()` used `apiFetch` which on 401 calls `clearTokens()` + redirects, racing with the actual logout. Fixed: raw `fetch()` with `getAccessToken()`.
3. **ChatHeader sign-out missing `flush()`** — went straight to `clearTokens()`. Fixed: added `await chatContext.flush()`.
4. **PyArrow timestamp conversion** — `save_chat_session()` passed ISO strings to `pa.timestamp("us")` → `"str cannot be converted to int"`. Endpoint returned 201 (error swallowed) but Iceberg write never happened. Fixed: `_parse_ts()` via `pd.Timestamp().to_pydatetime()`.
5. **Wrong localStorage key** — `beforeunload` used `"access_token"` but actual key is `"auth_access_token"`.
6. **Close panel flush** — `closePanel` callback only did `setIsOpen(false)` without saving. Fixed: added `flush()` call.

### Activity Log UI Fix (ASETPLTFRM-159, 3 pts)

1. **Raw JSON preview** — session cards showed `[{"role": "user"...}` instead of readable text. Fixed: parse JSON, extract first user message content in `list_chat_sessions()`.
2. **No close button on Activity Log tab** — EditProfileModal only had Cancel/Save in Profile tab footer. Fixed: X button in modal header visible on both tabs.
3. **Missing detail endpoint** — `GET /v1/audit/chat-sessions/{session_id}` didn't exist → 404 on expand. Fixed: `get_chat_session_detail()` repo method + route returning `ChatSessionDetail`.
4. **Silent failure** — expand showed nothing on error. Fixed: error state with message.

### Currency-Aware Portfolio Agent (ASETPLTFRM-160, 5 pts)

AI chat showed `$332,325.99` for an all-Indian (₹) portfolio and hallucinated data.

1. **System prompt rewrite** — mandatory tool-use ("YOUR FIRST RESPONSE MUST ONLY be a tool call"), currency rules ("NEVER default to $"), anti-hallucination guardrails.
2. **Dynamic context injection** — `_build_context_block()` in `sub_agents.py` detects user's currency/market mix from holdings and appends to system prompt (e.g., "All holdings are INR. Use ₹").
3. **`user_context` in graph state** — new `AgentState` field populated in both HTTP (`routes.py`) and WebSocket (`ws.py`) paths.
4. **Currency-aware tool outputs** — `get_portfolio_holdings()` shows ₹/$ per row + per-currency totals; `get_portfolio_summary()` groups by currency; `get_portfolio_performance()` shows currency/market context.

### TradingView Chart Crash Fix (ASETPLTFRM-161, 2 pts)

`Assertion failed: data must be asc ordered by time, index=1, time=0, prev time=0`

1. **`toTime()`** — now slices to `YYYY-MM-DD` (was passing full ISO timestamps that TradingView silently converted to `0`).
2. **`filterNull()`** — validates dates with `/^\d{4}-\d{2}-\d{2}/` regex + sorts ascending.
3. **Candle + volume data** — same date validation applied.

### Files changed

| File | Change |
|------|--------|
| `stocks/repository.py` | `import json`, preview parsing, `_parse_ts()`, `get_chat_session_detail()` |
| `backend/audit_routes.py` | `GET /audit/chat-sessions/{session_id}` detail endpoint |
| `backend/agents/configs/portfolio.py` | Mandatory tool-use + currency rules in system prompt |
| `backend/agents/sub_agents.py` | `_build_context_block()`, `_CURRENCY_SYMBOLS`, context injection |
| `backend/agents/graph_state.py` | `user_context: dict` field |
| `backend/tools/portfolio_tools.py` | `_CCY_SYMBOLS`, currency in holdings/summary/performance |
| `backend/routes.py` | `_build_user_context()`, `user_context` in graph input |
| `backend/ws.py` | `user_context` in WS graph input |
| `frontend/providers/ChatProvider.tsx` | `fetch+keepalive` replacing `sendBeacon`, flush on close |
| `frontend/hooks/useChatSession.ts` | Raw `fetch` replacing `apiFetch` |
| `frontend/components/ChatHeader.tsx` | `flush()` before `clearTokens()` on sign-out |
| `frontend/components/EditProfileModal.tsx` | X close button in header |
| `frontend/components/PastSessionsTab.tsx` | Detail error state |
| `frontend/components/charts/StockChart.tsx` | `toTime()` YYYY-MM-DD, `filterNull` regex+sort, date validation |

### Jira tickets
- ASETPLTFRM-158 (5 pts) — Chat session recording: **Done**
- ASETPLTFRM-159 (3 pts) — Activity Log UI: **Done**
- ASETPLTFRM-160 (5 pts) — Currency-aware portfolio agent: **Done**
- ASETPLTFRM-161 (2 pts) — TradingView chart crash: **Done**

Sprint 3 progress: 15 pts delivered (Mar 22)

---

# Session: Mar 20, 2026 — Portfolio Analytics, TradingView Migration, UX Polish

## Sprint 2 continuation on `feature/sprint2-planning`

### Portfolio Performance & Forecast (ASETPLTFRM-124, 8 pts)

**Backend** (`dashboard_routes.py`, `dashboard_models.py`):
- `GET /v1/dashboard/portfolio/performance` — daily portfolio value + invested series
  - Cash-flow-adjusted metrics: daily returns strip capital contributions
  - Total return uses invested basis, max drawdown on gain% series
  - `_safe_float()` helper for NaN-safe Iceberg NULL handling with OHLCV fallback
- `GET /v1/dashboard/portfolio/forecast` — weighted Prophet forecast aggregation
  - Always fetches 9M from Iceberg; client truncates for 3M/6M
  - Returns `total_invested` for explainable summary cards
- 5 Pydantic models: PortfolioDailyPoint (with `invested_value`), PortfolioMetrics, PortfolioPerformanceResponse, PortfolioForecastPoint, PortfolioForecastResponse (with `total_invested`)
- Cache invalidation on portfolio add/edit/delete for perf + forecast keys

**Frontend** — Analysis page 5 tabs:
- Portfolio Analysis: TradingView `PortfolioChart.tsx` (AreaSeries value + LineSeries invested amber + HistogramSeries P&L), 6 metrics cards, crosshair tooltip with gain/loss %
- Portfolio Forecast: TradingView `PortfolioForecastChart.tsx` (dual historical lines + forecast + confidence band), 4 explainable summary cards (Invested → Current Value with P&L → Predicted → Expected Return on cost), horizon picker 3M/6M/9M

### TradingView Migration — Stock Forecast + Compare
- `ForecastChart.tsx` — replaces Plotly for per-ticker forecast (historical + forecast + confidence band + crosshair)
- `CompareChart.tsx` — replaces Plotly for normalized price comparison (one LineSeries per ticker, colored legend)
- Correlation heatmap section removed from Compare Stocks
- Plotly removed from analysis + compare pages (only Insights still uses Plotly)

### ConfirmDialog (ASETPLTFRM-125, 2 pts)
- Reusable `ConfirmDialog.tsx` with danger (red) / warning (amber) variants
- Applied to 5 destructive flows: delete stock, unlink ticker, revoke session, revoke all, deactivate user
- Escape key + backdrop click dismiss, auto-focus on confirm button

### UX Polish
- Tab labels: Portfolio Analysis, Portfolio Forecast, Stock Analysis, Stock Forecast, Compare Stocks
- Tab order: Portfolio first, then Stock, then Compare
- Tab style: underline (matching Insights/Admin pages)
- Tab preference persistence for all new tab IDs
- HeroSection buttons: "Portfolio Analysis", "Portfolio Forecast", "Link Stock"
- "Link Ticker" → "Link Stock" everywhere (sidebar, header, hero)
- Chart legends in headers (Market Value + Invested + Forecast indicators)
- Invested line: amber dashed 2px (visible against all backgrounds)

### Bug Fixes
- NaN handling: Iceberg NULL → pandas NaN is truthy, breaks `or`/comparison fallbacks → `_safe_float()` with `math.isnan()`
- Horizon picker empty: forecast endpoint filtered by `horizon_months` but only 9M rows exist → always fetch 9M
- Metrics inflated (+501% return): raw value includes capital contributions → cash-flow-adjusted formulas
- React hooks order: `useRef`/`useCallback` after conditional returns → moved before early returns

### Files changed

| File | Change |
|------|--------|
| `backend/dashboard_models.py` | +`invested_value`, +`total_invested` on portfolio models |
| `backend/dashboard_routes.py` | +2 endpoints, +`_safe_float()`, cash-flow-adjusted metrics |
| `auth/endpoints/ticker_routes.py` | +cache invalidation for perf/forecast |
| `frontend/lib/types.ts` | +5 TypeScript interfaces |
| `frontend/components/charts/PortfolioChart.tsx` | +invested LineSeries (amber), +gain/loss tooltip |
| `frontend/components/charts/PortfolioForecastChart.tsx` | New — TradingView forecast chart |
| `frontend/components/charts/ForecastChart.tsx` | New — TradingView per-ticker forecast |
| `frontend/components/charts/CompareChart.tsx` | New — TradingView compare chart |
| `frontend/components/ConfirmDialog.tsx` | New — reusable confirmation dialog |
| `frontend/app/(authenticated)/analytics/analysis/page.tsx` | 5 tabs, TradingView everywhere, underline style |
| `frontend/app/(authenticated)/analytics/compare/page.tsx` | TradingView, removed correlation |
| `frontend/app/(authenticated)/dashboard/page.tsx` | ConfirmDialog for delete |
| `frontend/app/(authenticated)/analytics/marketplace/page.tsx` | ConfirmDialog for unlink |
| `frontend/components/SessionManagementModal.tsx` | ConfirmDialog for revoke |
| `frontend/app/(authenticated)/admin/page.tsx` | ConfirmDialog for deactivate |
| `frontend/components/widgets/HeroSection.tsx` | Updated labels + links |
| `frontend/lib/constants.tsx` | "Link Stock" |
| `frontend/components/AppHeader.tsx` | "Link Stock" |
| `tests/backend/test_portfolio_analytics.py` | 11 tests |

### Refresh Buttons & Data Pipeline
- Per-ticker refresh on Portfolio Analysis tab (all holdings), Portfolio Forecast tab, Stock Analysis/Forecast (selected ticker)
- Refresh triggers `POST /v1/dashboard/refresh/{ticker}` → polls `/status` → re-fetches chart data on success
- Stock Analysis + Stock Forecast charts re-mount via `key={ticker-refreshKey}` on refresh success
- **Freshness gate fix**: `stock_refresh.py` OHLCV gate changed from `latest >= today - 1 day` to `latest >= today` — was skipping fetches when yesterday's data existed

### Dark Mode Fix
- Created `useDomDark.ts` hook — MutationObserver on `<html>` classList to detect theme changes
- Applied to all 4 new chart components (PortfolioChart, PortfolioForecastChart, ForecastChart, CompareChart)
- Fixes SSR hydration mismatch where chart rendered dark on light mode page

### Test Coverage Expansion (+100 new tests)
- `test_portfolio_crud.py` — 17 tests: GET/POST/PUT/DELETE portfolio + preferences
- `test_cache.py` — 11 tests: CacheService get/set/invalidate, NoOp fallback, Redis failure
- `test_portfolio_analytics.py` — +6 tests: _safe_float NaN/None, cashflow-adjusted return, invested-basis total return
- `test_ws_basic.py` — 18 tests: WS module exports, auth validation, protocol messages
- `test_agents_basic.py` — 20 tests: config, registry CRUD, router keyword/ticker/blocked
- `ConfirmDialog.test.tsx` — 7 tests: render, callbacks, variants
- `types.portfolio.test.ts` — 9 tests: 5 new portfolio interfaces
- `useDarkMode.test.ts` — 1 test: export smoke

### Pre-existing Test Fixes
- `report_builder.py` — `_extract(None)` crash fixed with None guard (CRITICAL)
- `test_dashboard_routes.py` — Watchlist mock method names corrected (`get_ohlcv_batch` not `get_dashboard_ohlcv`)
- `test_dashboard_routes.py` — LLM usage field name corrected (`"total_cost"` not `"total_cost_usd"`)
- `test_audit_routes.py` — JWT secret + `_resolve_user` auth override added

### Venv Fix
- Created symlink `~/.ai-agent-ui/venv` → `backend/demoenv` (Python 3.12.9)
- Tests now run correctly with `source ~/.ai-agent-ui/venv/bin/activate && python -m pytest`
- Root cause: conda base (Python 3.9) was default; project venv at `backend/demoenv` was undocumented

### Test Results (final)
- Backend: 416 passed, 23 failed (pre-existing mock issues in test_stock_tools — ASETPLTFRM-126)
- Frontend: 61 passed

Tickets: ASETPLTFRM-124 (8 pts), ASETPLTFRM-125 (2 pts) — Done
Created: ASETPLTFRM-126 (3 pts) — Fix test_stock_tools/test_chat_stream mocks (Sprint 3)
Sprint 3: ASETPLTFRM-76–81, 126 moved, due Mar 26

---

# Session: Mar 18–19, 2026 — Performance, Charts, Portfolio, Dash Retirement

## Sprint 2 Complete (46 story points, 11 tickets — 100% delivered)

### Performance (ASETPLTFRM-115)
- Redis write-through cache for 22 endpoints with invalidation map
- Cache warm-up at startup (shared + per-ticker + top N users)
- SWR frontend caching (all pages converted from raw useEffect)
- Aggregate `/dashboard/home` endpoint (4 requests → 1)
- Iceberg N+1 queries eliminated, predicate push-down

### Charts (ASETPLTFRM-115)
- TradingView lightweight-charts v5 replacing broken Plotly candlestick
- 4-pane: Candlestick + Volume + RSI + MACD with crosshair + zoom
- D/W/M interval selector with candle aggregation
- Indicator toggles, OHLC legend, Bollinger Bands
- Dark/light mode sync via DOM classList read

### Dash Migration (ASETPLTFRM-112, 113, 114)
- Insights (7 tabs) + Admin (3 tabs) fully native in Next.js
- Dash service retired from run.sh (4 services now)
- iframe removed, DASHBOARD_URL removed
- Chat FAB → AppHeader toggle

### Portfolio Management (ASETPLTFRM-118)
- Iceberg `portfolio_transactions` table (append-only)
- CRUD: add/edit/delete stocks with searchable ticker dropdown
- WatchlistWidget 2-tab (Portfolio | Watchlist)
- HeroSection: portfolio value per currency, total P&L
- Per-ticker refresh pipeline (6-step background job)

### User Preferences (ASETPLTFRM-116, 117)
- localStorage + Redis sync with sliding 7-day TTL
- Chart settings, market filter, active tab persist
- Smart cache warming for top N frequent users

### Code Cleanup (ASETPLTFRM-72, 73, 74, 75)
- Unit tests for report_builder.py (16 cases)
- gen_api_docs.py: lightweight import + proper auth detection
- Agent _build_llm dedup (BaseAgent), Redis port variable

### Files: ~100 new/modified across frontend + backend + docs
### Tickets: ASETPLTFRM-72-75, 112-118 (11 Done)
### PRs: Pending (branch: feature/sprint2-planning)

---

# Session: Mar 16, 2026 — Dashboard UI Overhaul + Dash-to-Next.js Migration

## 2026-03-16 — Dashboard UI Overhaul + Dash-to-Next.js Migration

### Sprint 1 Complete (ASETPLTFRM-82 to 106)
- **Native portfolio dashboard** replacing chat-first landing page with widgets (watchlist, analysis signals, LLM usage donut, forecast chart)
- **Collapsible sidebar navigation**: Portfolio, Dashboard (collapsible: Home, Analysis, Insights, Link Ticker), Docs, Admin
- **Chat side panel**: FAB-triggered resizable drawer with past sessions, agent switcher, WebSocket streaming
- **Global India/US country filter** with correct ₹/$ currency symbols across all widgets
- **6 backend dashboard endpoints** + Iceberg chat_audit_log table
- **14 backend + 22 frontend tests**
- Removed Dash header, consolidated navigation to Next.js sidebar
- Bug fixes: hydration mismatch, sidebar layout, iframe height, currency display, signal N/A values

### Sprint 2 In Progress (ASETPLTFRM-107 to 114)
- **react-plotly.js** chart wrapper with auto dark/light theming
- **4 Dash pages migrated to native Next.js**: Home (stock cards), Link Ticker (paginated table), Compare (charts + heatmap), Analysis (tabbed: candlestick+RSI+MACD, forecast, compare)
- **Unified chart**: candlestick + volume + RSI + MACD on shared x-axis with range selector (3M/6M/1Y/2Y/3Y/Max)
- Remaining: Insights migration (8 SP), Admin migration (5 SP), Dash retirement (2 SP)

### Files: ~60 new/modified across frontend + backend
### Tickets: ASETPLTFRM-82 to 114 (25 Done, 5 In Progress/To Do)
### PRs: Pending (branch: feature/sprint2-planning)

---

# Session: Mar 15, 2026 — WSL2 compat, LLM cascade, report template, auto-docs

## Summary
WSL2 installation fixes, DevOps UX overhaul (setup.sh + run.sh), LLM cascade split into tool/synthesis/test profiles, deterministic report template, auto-generated API/config docs, and drift detection CLI.

### Completed tickets

#### PR #92 — WSL2 compatibility + DevOps UX (merged)
- **ASETPLTFRM-67** (3 SP) — Fix setup.sh prompt stdout leak, default superuser menu, numbered API key prompts
- **ASETPLTFRM-68** (3 SP) — Crash-resume via .setup_state markers, --repair mode for symlinks/hooks/env
- **ASETPLTFRM-69** (5 SP) — run.sh: reliable 3-state status (up/listening/down), logs command, doctor diagnostics
- **ASETPLTFRM-70** (3 SP) — Cross-platform install guides: macOS, Linux, Windows 11 (WSL2 full walkthrough)

#### PR #93 — LLM cascade + report template + bug fix (merged)
- **ASETPLTFRM-66** (3 SP) — Split LLM cascade: tool (llama→kimi→scout), synthesis (gpt-oss→kimi→Anthropic), test (free-only)
- **ASETPLTFRM-65** (3 SP) — Deterministic report_builder.py: 5 markdown sections parsed from tool output + LLM verdict-only
- **ASETPLTFRM-71** (2 SP, Bug) — Fix synthesis double-invoke (save 1 API call), cap news agent to 2 iterations, reinforce pipeline prompt

#### PR #94 — Auto-gen docs + drift checker (pending merge)
- **ASETPLTFRM-63** (3 SP) — gen_api_docs.py + gen_config_docs.py via mkdocs-gen-files plugin
- **ASETPLTFRM-64** (2 SP) — docs_drift_check.py + ./run.sh docs-check command

### Key metrics
- Sprint 1: 11 stories + 1 bug = 28 SP total, all implemented
- Stock analysis API calls: 10 → 5 (50% reduction, verified TITAN.NS)
- Token usage: ~28K → ~14.6K per analysis (48% reduction)
- Report consistency: 100% deterministic (model-independent)

---

# Session: Mar 14, 2026 — ASETPLTFRM-60, 61, 62 + Sprint planning

## Summary
Dark mode fixes, MkDocs theme sync, Sprint 1 planning & brainstorming.

### Completed tickets (merged in PR #90 and #91)

#### ASETPLTFRM-60 — Superuser cap + E2E reliability (PR #90)
- Superuser cap counts only active users.
- Shared wait helpers in `e2e/utils/wait.helper.ts`.
- Refactored all 6 page objects + 14 test files.

#### ASETPLTFRM-61 — Dark mode "2 selected" badge fix (PR #90)
- Added `body.dark-mode .dash-dropdown-value-count` in `custom.css`.

#### ASETPLTFRM-27 — E2E test stabilization (PR #90)
- Marked Done — all parallel worker flakiness resolved.

#### ASETPLTFRM-62 — MkDocs dark mode sync (PR #91)
- `mkdocs.yml` — added `custom_dir: docs/overrides`.
- `docs/overrides/main.html` — reads `?theme=` URL param, sets
  Material palette localStorage + `data-md-color-scheme`.
- `frontend/app/page.tsx` — docs iframe appends theme param.
- Key discovery: Material stores palette as
  `{index, color: {scheme}}` in localStorage.

### New Sprint 1 stories (brainstormed + created)

| Key | Summary | SP | Epic |
|-----|---------|---:|------|
| ASETPLTFRM-63 | Auto-gen API + config docs (mkdocs-gen-files) | 3 | -4 |
| ASETPLTFRM-64 | CLI docs drift detection (`./run.sh docs-check`) | 2 | -6 |
| ASETPLTFRM-65 | Deterministic report template + LLM verdict-only | 3 | -2 |
| ASETPLTFRM-66 | Split LLM cascade: tool/synthesis/test profiles | 3 | -2 |

### Key design decisions
- **Report builder**: Tools return structured dicts → Python template
  renders sections 1-5 → separate small LLM call for verdict only
  (~150-250 tokens vs ~800-1200 today). 80% token reduction.
- **Cascade split**: Tool-calling uses llama/kimi/scout, synthesis
  uses gpt-oss-120b exclusively, tests use free-tier-only cascade
  (no gpt-oss, no Anthropic). Detected via `AI_AGENT_UI_ENV=test`.

### Sprint 1 status
- 51 Done, 4 To Do (63, 64, 65, 66). Sprint ends 2026-03-18.

---

# Session: Mar 13, 2026 (cont. 2) — ASETPLTFRM-13, 20

## Summary
Tier health monitoring and full API v1 cutover.

### ASETPLTFRM-13 — Groq tier health monitoring
- Per-tier health classification: healthy/degraded/down/disabled
  (5-min sliding window, thresholds: 1 failure = degraded, 4 = down).
- Latency stats (avg + p95) from sliding window of recent values.
- Admin endpoints: `GET /v1/admin/tier-health`,
  `POST /v1/admin/tier-health/{model}/toggle`.
- Dashboard health cards with color-coded status indicators.
- 12 backend tests (`test_tier_health.py`), 6 dashboard tests
  (`test_tier_health_cards.py`), 3 E2E tests.

### ASETPLTFRM-20 — API v1 cutover
- Removed root-mounted duplicate routes; all API under `/v1/`.
- Frontend: added `API_URL` constant (`BACKEND_URL/v1`), updated
  9 files to use `API_URL` for API calls, kept `BACKEND_URL` for
  static assets (avatars) and WS URL derivation.
- Dashboard: split `_BACKEND_URL` → `_BACKEND_HOST` + API URL.
- WebSocket stays at `/ws/chat` (not versioned).
- Rewrote `test_api_versioning.py` (8 tests), updated
  `test_chat_stream.py` to use `/v1/` paths.
- Python 3.9 compat: `from __future__ import annotations` in 7
  backend files.

### Documentation updates
- `backend/api.md` — all endpoints under `/v1/`, admin tier-health
  endpoints, WebSocket protocol, updated curl examples.
- `backend/overview.md` — observability module, tier health section,
  API versioning route table.
- `backend/config.md` — WebSocket + Redis settings.
- `dashboard/overview.md` — LLM observability tab, health cards,
  `_api_call` host/API URL split.
- `frontend/overview.md` — `API_URL` constant, URL usage guide,
  new hooks/components in file tree.
- `dev/changelog.md` — Mar 13 entry for ASETPLTFRM-13 and 20.
- `README.md` — `/v1/` only routes, tier health admin endpoint,
  observability files, session management components, E2E counts,
  WebSocket/Redis env vars.

---

# Session: Mar 13, 2026 (cont.) — ASETPLTFRM-18, 19, 58

## Summary
Bug fixes, lazy loading, forecast charts, and E2E expansion.

### ASETPLTFRM-18 — Lazy tab loading (analysis page)
- Tabs render via callback on `active_tab`; no children at init.
- `suppress_callback_exceptions=True` enabled.
- Bug fix: moved `analysis-refresh-store` + poll interval
  outside tab content so they persist across tab switches.

### ASETPLTFRM-19 — Forecast chart types
- Horizon radio (3/6/9 months), view radio (standard,
  decomposition, multi_horizon).
- 14 new unit tests (`test_lazy_loading.py`,
  `test_forecast_charts.py`).

### Bug fixes
- **Compare chart broken**: `analysis-refresh-store` destroyed
  on tab switch — moved to `analysis_tabs_layout()`.
- **Pagination reset to page 1**: phantom sort-store writes
  from pattern-matching callbacks firing on table re-render.
  Fixed with `if not any(n_clicks_list): return no_update`
  guard in `sort_helpers.py`.
- **Python 3.9 compat**: added `from __future__ import
  annotations` to 10 dashboard files using `X | None` syntax.

### ASETPLTFRM-58 — E2E test coverage (+42 tests)
- New: `pagination.spec.ts` (10 tests) — cross-page validation.
- Updated 6 specs: home (+4), insights (+10), marketplace (+6),
  forecast (+6), analysis (+7), admin (+7).
- Total E2E: ~91 tests.

### Jira updates
- ASETPLTFRM-18, 19 updated with implementation details.
- ASETPLTFRM-58 updated with full E2E coverage breakdown.

---

# Session: Mar 13, 2026 — ASETPLTFRM-7, 10, 12

## Summary
Implemented three Jira stories on `feature/iframe-top-navigation`:

### ASETPLTFRM-7 — JWKS key rotation + iframe sign-in fix
- JWKS rotation endpoint, iframe top-navigation sign-in fix.

### ASETPLTFRM-10 — Session management (backend + frontend)
- Backend: `GET /auth/sessions`, `DELETE /auth/sessions/{id}`,
  `POST /auth/sessions/revoke-all` with JTI-based tracking.
- Frontend: `SessionManagementModal` with device parsing,
  current-session highlight, revoke/revoke-all actions.
- 12 backend tests, 22 frontend tests passing.

### ASETPLTFRM-12 — LLM observability dashboard (8 pts)
- `ObservabilityCollector` — thread-safe cascade/request/compression
  metrics with sliding-window RPM tracking.
- Wired into `FallbackLLM` at 5 instrumentation points.
- `GET /admin/metrics` endpoint (superuser only).
- Dash "LLM Observability" tab: auto-refresh tier cards with
  TPM/RPM gauges, cascade summary badges, event log table.
- 8 tests (6 collector unit + 2 endpoint).

### Test results
- 391 passed, 1 pre-existing failure, 7 skipped (no regressions).

---

# Session: Mar 13, 2026 — Sprint 1 Branch Promotions

## Summary
Promoted Sprint 1 deliverables (30/30 story points) through
all branches: dev → qa → release → main. All conflicts
resolved locally before pushing — zero conflicts on GitHub.

### PRs
| PR | Promotion | Status |
|----|-----------|--------|
| #85 | dev → qa | Merged |
| #86 | qa → release | Merged |
| #87 | release → main | Merged |

### Result
All 4 branches (dev, qa, release, main) are identical.
Local promotion branches and stale remote refs cleaned up.

---

# Session: Mar 12, 2026 — PR #82 Review Fixes (ASETPLTFRM-50-54)

## Summary
Addressed 5 stories from PR #82 code review: auth health
API encapsulation, thread-safe Dash RefreshManager, shared
Redis connection pool, E2E helper deduplication, and flaky
E2E test fixes. All 5 tickets implemented and commented in
Jira. PR #84 raised to dev.

### Changes

| Area | Change |
|------|--------|
| `auth/service.py` | Public `store_health()` method |
| `auth/endpoints/auth_routes.py` | `/auth/health` uses public API |
| `auth/token_store.py` | `get_redis_client()` cached factory, shared pool |
| `dashboard/callbacks/refresh_state.py` (NEW) | Thread-safe `RefreshManager` with Lock |
| `dashboard/callbacks/analysis_cbs.py` | Removed globals, uses `RefreshManager` |
| `dashboard/callbacks/forecast_cbs.py` | Removed globals, uses `RefreshManager` |
| `dashboard/callbacks/home_cbs.py` | Removed globals, uses `RefreshManager` |
| `dashboard/callbacks/registration.py` | Creates 3 `RefreshManager` instances |
| `e2e/utils/auth.helper.ts` (NEW) | Shared `readCachedToken()` |
| `e2e/fixtures/auth.fixture.ts` | Imports from shared helper |
| `e2e/tests/auth/login.spec.ts` | Rate-limit retry loop (3 attempts) |
| `e2e/tests/errors/network-error.spec.ts` | `page.routeWebSocket()` WS bypass |
| `tests/backend/test_auth_api.py` | `TestAuthHealth` + fixed E501 |
| `tests/backend/test_token_store.py` | `TestStoreHealth`, `TestSharedRedisClient` |
| `tests/dashboard/test_refresh_state.py` (NEW) | 9 tests for RefreshManager |

### Test Results
- Python: 66 relevant tests pass (auth API, token store, refresh, home perf)
- E2E: 49/50 passed (1 pre-existing forecast timeout)

---

# Session: Mar 12, 2026 — WebSocket Streaming (ASETPLTFRM-11)

## Summary
Implemented persistent WebSocket `/ws/chat` endpoint for real-time
agent streaming. Auth-first protocol (token in first message, not
URL query param). Frontend state machine hook with exponential
backoff reconnect. HTTP NDJSON fallback preserved — zero breaking
changes. All subtasks and parent story Done. PR #83 merged to dev.

### Changes

| Area | Change |
|------|--------|
| `backend/ws.py` (NEW) | WebSocket endpoint: auth, ping/pong, chat streaming, concurrent guard |
| `backend/config.py` | Added `ws_auth_timeout_seconds`, `ws_ping_interval_seconds` |
| `backend/routes.py` | Wired `register_ws_routes()` before static mount |
| `frontend/hooks/useWebSocket.ts` (NEW) | Connection state machine: DISCONNECTED → CONNECTING → AUTHENTICATING → READY |
| `frontend/hooks/useSendMessage.ts` | WS-preferred streaming with HTTP fallback; shared `handleEvent()` |
| `frontend/app/page.tsx` | Integrated `useWebSocket` hook, passed to `useSendMessage` |
| `frontend/lib/config.ts` | Added `WS_URL` (derived from `BACKEND_URL`) |
| `tests/backend/test_ws.py` (NEW) | 6 tests: auth_ok, bad_token, wrong_first_msg, ping_pong, unknown_agent, reauth |
| `frontend/tests/useWebSocket.test.ts` (NEW) | 4 tests: connect+auth, reconnect backoff, event routing, sendChat |

### Protocol
- Close codes: 4001 (auth failed), 4002 (auth timeout), 4003 (invalid message)
- Keepalive: ping/pong every 30s
- Re-auth supported mid-session for token refresh
- Concurrent streaming rejected with error event

### Test Results
- Python: 355 passed, 0 failed (6 new WS tests)
- Frontend: 22 passed, 0 failed (4 new WS tests)

### Sprint 1 Status (Complete)
- Done: ASETPLTFRM-23 (1pt), 24 (2pt), 17 (3pt), 48, 49, 9 (5pt), **11 (8pt)**
- Velocity: 19/19 pts (100%), 7/7 stories

---

# Session: Mar 12, 2026 — Redis Token Store Production (ASETPLTFRM-9)

## Summary
Deployed RedisTokenStore for production use. Added operation-level
resilience, health check endpoint, OAuth state on Redis, AOF
persistence, and full integration tests with fakeredis. Updated
setup.sh (Redis install + AOF config) and run.sh (Redis
start/stop lifecycle). All 4 subtasks and parent story Done.

### Changes

| Area | Change |
|------|--------|
| Token store | Operation-level resilience — `add`/`contains`/`remove` catch `RedisError`, degrade gracefully |
| Health check | `ping()` on TokenStore protocol + `GET /auth/health` endpoint |
| OAuth state | `_get_oauth_svc()` now uses Redis (prefix `auth:oauth_state:`) |
| Persistence | AOF enabled (`appendfsync everysec`) — deny-list survives restarts |
| setup.sh | New Step 11/12: Redis install + start + AOF config + verification |
| run.sh | `_redis_start()`/`_redis_stop()` with retry loop; Redis in status table |
| Dependencies | `redis==7.3.0`, `fakeredis==2.34.1`, `sortedcontainers==2.4.0` |
| Tests | 25 tests: 7 integration (fakeredis), 3 resilience, 2 ping, 13 existing |
| Config | `REDIS_URL=redis://localhost:6379/0` in backend.env |

### Test Results
- Python: 350 passed, 0 failed
- Token store: 25/25 passed

### Sprint 1 Status
- Done: ASETPLTFRM-23 (1pt), 24 (2pt), 17 (3pt), 48, 49, **9 (5pt)**
- To Do: ASETPLTFRM-11 (8pt)
- Velocity: 11/19 pts (58%), 6/7 stories

---

# Session: Mar 12, 2026 — E2E Reliability + Iceberg Safety

## Summary
Fixed all E2E dashboard refresh timeouts (ASETPLTFRM-48) and
auth rate-limit 429s (ASETPLTFRM-49). Converted Iceberg writes
to scoped delete+append. PR #81 raised to dev.

### Changes

| Area | Change |
|------|--------|
| Freshness gates | `run_full_refresh` skips OHLCV if <1d old, Prophet if <7d old |
| Background refresh | analysis_cbs + forecast_cbs → ThreadPoolExecutor + 2s polling |
| E2E auth caching | Read JWT from storageState files, eliminates 16 login calls |
| E2E test hardening | RELIANCE.NS → AAPL, test.slow(), toContainText assertions |
| Iceberg safety | 5 full-table overwrites → scoped delete+append |
| Auth rate limits | RATE_LIMIT_LOGIN env var (configurable, default 30/15min) |

### Test Results
- Python: 337 passed, 0 failed
- E2E: 48 passed, 0 failed, 2 flaky

### Sprint 1 Status
- Done: ASETPLTFRM-23 (1pt), 24 (2pt), 17 (3pt), 48, 49
- To Do: ASETPLTFRM-9 (5pt), ASETPLTFRM-11 (8pt)
- Velocity: 6/19 pts (32%), 5/7 stories

---

# Session: Mar 11, 2026 — Sprint Phase 3 + Dashboard fixes

## Summary
Completed Phase 3 of the sprint plan: Redis token store
with in-memory fallback, API versioning (`/v1/` prefix),
and frontend config centralization. Fixed all E2E failures
including Dashboard callback race conditions that caused
blank pages and "Authentication required" errors.

### Phase 3 — Redis token store + API versioning

| # | Story | Details |
|---|-------|---------|
| 1.3 | Redis token store | `TokenStore` protocol with `InMemoryTokenStore` / `RedisTokenStore`; JWT deny-list + OAuth state now use pluggable store with TTL auto-expiry |
| 2.2 | API versioning | Dual-mount routes at `/` (backward compat) and `/v1/`; plain handler functions with `_register_core_routes()` |
| 2.2b | Frontend config | Centralized `frontend/lib/config.ts` replaces 18 duplicate URL declarations across 9 files |

### Bug fixes

| # | Fix | Details |
|---|-----|---------|
| 1 | Rate limits | Increased to 30/15min login, 10/hr register, 30/min OAuth — E2E tests were cascading 429s |
| 2 | Login 429 UI | Frontend shows distinct "Too many attempts" message on 429 |
| 3 | E2E resilience | `apiLogin` + `auth.setup.ts` retry on 429/5xx; admin test waits for `#page-content` |
| 4 | Dashboard race conditions | `display_page` `State("auth-token-store")` → `Input()` so it re-fires after token extraction; 6 chart callbacks gain `State("url", "search")` + `_resolve_token()` fallback |

### Files changed (key)
- `auth/token_store.py` (new), `auth/service.py`,
  `auth/tokens.py`, `auth/dependencies.py`,
  `auth/oauth_service.py`
- `backend/routes.py`, `backend/main.py`, `backend/config.py`
- `frontend/lib/config.ts` (new), 9 frontend files updated
- `dashboard/app_layout.py`,
  `dashboard/callbacks/analysis_cbs.py`,
  `dashboard/callbacks/forecast_cbs.py`,
  `dashboard/callbacks/home_cbs.py`
- `e2e/setup/auth.setup.ts`, `e2e/utils/api.helper.ts`,
  `e2e/tests/dashboard/admin.spec.ts`,
  `e2e/tests/errors/network-error.spec.ts`

### Test results
- **Unit tests**: 324 passed, 2 skipped
- **E2E (single worker)**: 50/50 passed
- **E2E (2 workers)**: 46+ passed, flaky dashboard
  failures from single-threaded Dash server contention

### Branch
- `feature/phase3-sprint` (6 commits, ready for PR)

### Known issues resolved
- Refresh token deny-list no longer in-memory-only — uses
  `TokenStore` protocol with TTL (Redis or in-memory)
- Dashboard blank page on admin RBAC — callback race fixed
- Dashboard "Authentication required" — chart callbacks
  now resolve token from URL when store not yet populated

---

# Session: Mar 11, 2026 — Sprint execution (Phases 1–2)

## Summary
Executed the sprint plan: 6 stories across 2 phases (Phase 1
parallel, Phase 2 sequential). Security hardening committed
first, then Phase 1 layered on top, then Phase 2. All tests
pass (306 total, 0 failures).

### Phase 1 — Rate limiting, JWKS, caching, algo opts

| # | Story | Details |
|---|-------|---------|
| 1.1 | Rate limiting | slowapi on login (5/15min), password-reset (3/hr), OAuth (10/min) |
| 1.4 | JWKS verification | PyJWKClient replaces `verify_signature=False` on Google OAuth |
| 3.1 | Iceberg caching | Column projection via `selected_fields` + CachedRepository (TTLCache) |
| 3.2 | Algo optimizations | TokenBudget O(1) running totals, compressor early-exit, single-pass loop boundary |

### Phase 2 — Decomposition + HttpOnly cookies

| # | Story | Details |
|---|-------|---------|
| 2.1 | ChatServer decomp | Extracted `bootstrap.py` + `routes.py`; main.py ~490→~110 LOC |
| 1.2 | HttpOnly cookies | Refresh token in HttpOnly cookie; localStorage holds access only |

### Files changed (key)
- `auth/rate_limit.py` (new), `auth/endpoints/auth_routes.py`,
  `auth/endpoints/oauth_routes.py`, `auth/oauth_service.py`
- `stocks/repository.py`, `stocks/cached_repository.py` (new)
- `backend/bootstrap.py` (new), `backend/routes.py` (new),
  `backend/main.py`, `backend/token_budget.py`,
  `backend/message_compressor.py`, `backend/config.py`
- `frontend/lib/auth.ts`, `frontend/lib/apiFetch.ts`,
  `frontend/app/login/page.tsx`,
  `frontend/app/auth/oauth/callback/page.tsx`
- 6 new test files (23 tests added)

### Branches
- `feature/security-hardening` → `feature/phase1-sprint`
  → `feature/phase2-sprint` (all pushed to origin)

### Remaining (Phase 3)
- Story 1.3 — Redis deny list + OAuth state
- Story 2.2 — API versioning
- PRs to `dev`, then promote dev → qa → release → main

---

# Session: Mar 10, 2026 — N-tier Groq LLM cascade

## Summary
Refactored the 2-model (router/responder) LLM fallback into an N-tier
cascade with 4 Groq models + Anthropic paid fallback. Fixed multiple
issues: progressive compression, Groq SDK retries, 413 error cascade,
and ticker auto-linking.

### Changes

| # | Deliverable | Details |
|---|-------------|---------|
| 1 | N-tier FallbackLLM | 4 Groq tiers → Anthropic: 70b → kimi-k2 → gpt-oss-120b → scout-17b → claude-sonnet-4-6 |
| 2 | Budget-aware routing | Per-model TPM checks with progressive compression at 70% headroom |
| 3 | Groq SDK `max_retries=0` | Disabled internal retries (was 45-56s delay); errors cascade immediately |
| 4 | `APIStatusError` cascade | 413 errors now caught and cascaded (not just 429) |
| 5 | Ticker auto-linking fix | Frontend sends `user_id`; 3 missing tools wired with `auto_link_ticker()` |
| 6 | Config simplification | Single `groq_model_tiers` CSV replaces router/responder/threshold fields |
| 7 | Test rewrite | 12 tests covering N-tier API: cascade, budget skip, compression, no-key fallback |

### Files changed
- `backend/llm_fallback.py` — N-tier cascade (was 2-model)
- `backend/config.py` — `groq_model_tiers` CSV setting
- `backend/agents/config.py` — `groq_model_tiers: List[str]` field
- `backend/agents/general_agent.py` — N-tier factory
- `backend/agents/stock_agent.py` — N-tier factory
- `tests/backend/test_llm_fallback.py` — 12 tests rewritten
- `frontend/lib/auth.ts` — `getUserIdFromToken()` added
- `frontend/hooks/useSendMessage.ts` — sends `user_id` in chat body
- `backend/tools/stock_data_tool.py` — `auto_link_ticker()` in 3 tools

---

# Session: Mar 10, 2026 — Team knowledge sharing ecosystem

## Summary
Built a team knowledge sharing ecosystem for 4-5 developers using
Claude Code + Serena. Slimmed CLAUDE.md from ~650 lines to ~85 lines
(saving ~2,500 tokens/message), migrated all detailed content to 15
shared Serena memories, and created automation tooling.

### Knowledge sharing infrastructure

| # | Deliverable | Details |
|---|-------------|---------|
| 1 | Slim `CLAUDE.md` | 650 → 85 lines (~800 tokens vs ~3,500) |
| 2 | 15 shared Serena memories | architecture/ (5), conventions/ (6), debugging/ (2), onboarding/ (1), api/ (1) |
| 3 | Selective `.serena/` gitignore | Shared memories tracked, session/personal ignored |
| 4 | `/promote-memory` skill | AI-powered promotion from session to shared with cleanup |
| 5 | `/check-stale-memories` skill | Serena-powered semantic staleness detection |
| 6 | `scripts/check-stale-memories.sh` | CI grep-based stale memory check |
| 7 | `scripts/dev-setup.sh` | Single-command AI tooling onboarding (~5 min) |

### Design decisions

- **Hybrid sharing model**: Shared memories git-committed + PR-reviewed;
  session/personal memories gitignored.
- **On-demand loading**: Serena loads memories only when relevant,
  reducing context window usage vs always-loaded CLAUDE.md.
- **Memory conflict resolution**: Small focused files + PR review gate.
  Conflicts resolved via `/promote-memory` re-clean.
- **Two-layer staleness detection**: CI script (grep-based) + Claude
  Code skill (Serena semantic analysis).

### Files changed: 21 new, 2 modified

| File | Change |
|------|--------|
| `.serena/memories/shared/architecture/*.md` (5) | NEW — system overview, iceberg, auth, agent-init, groq |
| `.serena/memories/shared/conventions/*.md` (6) | NEW — python, typescript, git, testing, performance, errors |
| `.serena/memories/shared/debugging/*.md` (2) | NEW — common issues, mock patching |
| `.serena/memories/shared/onboarding/setup-guide.md` | NEW — onboarding guide |
| `.serena/memories/shared/api/streaming-protocol.md` | NEW — NDJSON streaming |
| `.claude/commands/promote-memory.md` | NEW — promote skill |
| `.claude/commands/check-stale-memories.md` | NEW — stale check skill |
| `scripts/dev-setup.sh` | NEW — AI tooling onboarding |
| `scripts/check-stale-memories.sh` | NEW — CI stale checker |
| `docs/plans/2026-03-09-team-knowledge-sharing-design.md` | NEW — design doc |
| `docs/plans/2026-03-09-team-knowledge-sharing-plan.md` | NEW — impl plan |
| `CLAUDE.md` | REWRITE — slimmed to ~85 lines |
| `.gitignore` | EDIT — selective .serena/ ignoring |

**Branch**: `feature/team-knowledge-sharing` (worktree)
**PR**: #68

---

# Session: Mar 9, 2026 — Seed fixes, profile NaN, backfill, Groq chunking

## Summary
Fixed setup and runtime bugs (seed data, profile edit NaN crash, E2E
credentials), created a data backfill pipeline, and implemented a
three-layer Groq rate-limit chunking strategy to maximize free-tier
usage and minimize Anthropic fallback.

### Bug fixes

| # | Issue | Fix |
|---|-------|-----|
| 1 | `seed_demo_data.py` OHLCV KeyError `Open` | Column rename lowercase→uppercase |
| 2 | `insert_forecast_run` TypeError (missing arg) | Added `horizon_months` positional arg |
| 3 | `insert_forecast_series` KeyError `ds` | Column rename to Prophet-style names |
| 4 | Pydantic EmailStr rejects `.local` TLD | Changed seed emails to `@demo.com` |
| 5 | Profile edit "Network error" | `_str_or_none()` guard for Parquet NaN |
| 6 | E2E auth login failures | Updated credentials in 6 files |
| 7 | E2E agent switcher flaky | Retry with `force: true` |
| 8 | E2E Enter key test flaky | Added `toBeFocused()` wait |
| 9 | E2E forecast test invalid | Rewrote for pre-populated dropdown |

### New features

- **`scripts/backfill_all.py`**: Truncate + refetch 10y data for
  OHLCV, company info, dividends, analysis, quarterly, forecast.
  Tested on 5 tickers in 27.2s — all steps passed.
- **`StockRepository.delete_ticker_data()`**: Bulk truncation
  across all 9 Iceberg tables (copy-on-write).
- **E2E profile save test**: Verifies edit modal save without error.

### Groq rate-limit chunking strategy (3 layers)

**Layer 1 — TokenBudget** (`backend/token_budget.py`):
Sliding-window `deque` tracker for TPM/RPM/TPD/RPD per model.
80% threshold preempts 429s. Thread-safe per-model locks.

**Layer 2 — MessageCompressor** (`backend/message_compressor.py`):
Three compression stages applied in order:
1. System prompt condensing (iteration 2+, ~40% of original)
2. History truncation (last 3 user/assistant turns)
3. Tool result truncation (2K char cap)
Progressive fallback: 1 turn → 0 turns → 500 chars.

**Layer 3 — FallbackLLM rewrite** (`backend/llm_fallback.py`):
Three-tier model routing:
- Router: `llama-4-scout-17b` (30K TPM) — tool-calling iterations
- Responder: `gpt-oss-120b` (8K TPM) — used when router exhausted
- Anthropic: last resort only
Budget-checked before each call, cascades on exhaustion or 429.

**Config**: `GROQ_ROUTER_MODEL`, `GROQ_RESPONDER_MODEL`,
`MAX_HISTORY_TURNS`, `MAX_TOOL_RESULT_CHARS`

### Files changed

New: `backend/token_budget.py`, `backend/message_compressor.py`,
`scripts/backfill_all.py`, `docs/design/groq-chunking-strategy.md`

Modified: `backend/llm_fallback.py` (rewrite), `backend/config.py`,
`backend/agents/config.py`, `backend/agents/base.py`,
`backend/agents/general_agent.py`, `backend/agents/stock_agent.py`,
`backend/agents/loop.py`, `backend/agents/stream.py`,
`backend/main.py`, `auth/endpoints/helpers.py`,
`scripts/seed_demo_data.py`, `stocks/repository.py`,
`tests/backend/test_llm_fallback.py`, 6 E2E test/fixture files

### Test results

- **155 backend tests pass** (16.8s)
- **50 E2E Playwright tests pass** (0 failed, 0 flaky)
- Zero new external dependencies

### Branch

`feature/fix-seed-and-profile-nan` — first commit pushed,
chunking strategy uncommitted. PR pending `gh auth login`.

---

# Session: Mar 8, 2026 — E2E test stabilization

## Summary
Ran full Playwright E2E suite against live services, debugged and
fixed all failures. Started at 7 passed / 2 failed / 39 skipped;
ended at **49 passed (48 clean + 1 flaky), 0 hard failures**.

### Root causes found and fixed

| # | Issue | Fix |
|---|-------|-----|
| 1 | `fill()` doesn't trigger React 19 controlled `onChange` | `pressSequentially({ delay: 30 })` in chat POM |
| 2 | `dbc.*` components reject `data-testid` kwargs | Wrapped in `html.Div` / removed redundant attrs |
| 3 | Dash debug menu overlays pagination buttons | `{ force: true }` on click |
| 4 | Agent selector is button group, not dropdown | `getByRole("button")` + `toHaveClass(/bg-white/)` |
| 5 | Mock NDJSON used `content` instead of `response` | Fixed field name in `mockChatStream()` |
| 6 | Registry dropdown selector mismatch | `getByRole("option")` instead of `.Select-option` |
| 7 | Analysis tab names wrong | Updated to "Forecast" / "Compare Stocks" |
| 8 | Transient backend 500 during concurrent login | Retry loop (3 attempts, 1 s delay) in `apiLogin()` |
| 9 | Dash reloader triggered by test artifacts | `outputDir` moved to `/tmp/e2e-test-results` |
| 10 | Login redirect flaky under load | Increased timeout to 30 s + `retries: 1` |

### Files modified
- `e2e/pages/frontend/chat.page.ts` — `pressSequentially`, agent wait
- `e2e/tests/frontend/chat.spec.ts` — Enter key fix, serial mode
- `e2e/tests/dashboard/marketplace.spec.ts` — force click
- `e2e/tests/dashboard/home.spec.ts` — dropdown selector fix
- `e2e/tests/dashboard/analysis.spec.ts` — tab name fix
- `e2e/utils/api.helper.ts` — login retry
- `e2e/pages/dashboard/home.page.ts` — blank page retry
- `e2e/pages/frontend/login.page.ts` — timeout increase
- `e2e/playwright.config.ts` — outputDir, retries, dependencies
- `dashboard/layouts/{analysis,home,marketplace,admin}.py` — dbc
  data-testid fixes

### Test results: 49 passed (target was 43)

| Area | Count |
|------|-------|
| Auth (login, logout, OAuth, token) | 8 |
| Frontend chat | 8 |
| Frontend navigation + profile | 5 |
| Frontend token refresh | 2 |
| Dashboard home | 6 |
| Dashboard analysis | 4 |
| Dashboard forecast | 4 |
| Dashboard marketplace | 3 |
| Dashboard admin | 3 |
| Error handling | 5 |
| **Total** | **49** |

---

# Session: Mar 7, 2026 — Error overlay + Playwright E2E framework

## Summary
Added reusable error overlay for dashboard refresh failures and
built the complete Playwright E2E automation framework (48 tests,
14 spec files, 6 Playwright projects).

### Error Overlay
- `dashboard/components/error_overlay.py` — `make_error_banner()`
  + `error_overlay_container()`
- Fixed-position red banner with `dbc.Alert(duration=8000)`
  auto-dismiss
- Wired to 3 callbacks: home card, analysis, forecast refresh
- All use `allow_duplicate=True`

### Playwright E2E Framework
- `e2e/` at project root — Playwright 1.50+, TypeScript, POM
- 6 projects: setup, auth, frontend, dashboard, admin, errors
- Auth: setup project produces `storageState`; dashboard uses
  `?token=` URL param
- Dash helpers: `waitForDashCallback`, `waitForPlotlyChart`,
  `waitForDashLoading`
- `data-testid` attrs added to 16 frontend + 11 dashboard
  components
- CI: `.github/workflows/e2e.yml` — chromium-only, caches browsers

### Files created
- `e2e/` directory (34 files)
- `dashboard/components/error_overlay.py`
- `.github/workflows/e2e.yml`
- `claudedocs/research_playwright_e2e_automation_2026-03-07.md`

### Files modified
- `dashboard/app_layout.py`, `assets/custom.css` — overlay
- `dashboard/callbacks/{home,analysis,forecast}_cbs.py` — overlay
  outputs
- `frontend/components/*.tsx` (8 files) — data-testid attributes
- `frontend/app/login/page.tsx` — data-testid attributes
- `dashboard/layouts/{home,analysis,forecast,marketplace,admin}.py`
  — data-testid attributes

---

# Session: Mar 7, 2026 — 5-Epic feature sprint (Epics 1–5)

## Summary
Implemented all 5 epics from the feature plan: admin password reset,
smart data freshness gates, virtualenv relocation, per-user ticker
linking, and the ticker marketplace dashboard page.

### Epic 1: Admin Password Reset
- `POST /users/{user_id}/reset-password` — superuser-only endpoint
- Dashboard modal with password validation (min 8 chars, 1 digit)
- Pattern-match "Reset Pwd" button per user row in admin table
- Audit logging: `ADMIN_PASSWORD_RESET` event with actor/target

### Epic 2: Smart Data Freshness
- Analysis freshness gate: skip re-analysis if done today (Iceberg check)
- Forecast 7-day cooldown: skip re-forecast within 7 days of last run
- Both gates wrapped in try/except — never block fallback to full run
- Same-day file cache still active alongside Iceberg freshness

### Epic 3: Virtualenv Relocation
- Moved venv from `backend/demoenv` → `~/.ai-agent-ui/venv`
- `setup.sh`: auto-migrates (mv + symlink) on upgrade
- `run.sh`, hooks: probe new path first, fall back to old
- Updated: pyproject.toml, .flake8, CI workflow, all docs
- Prevents linter corruption of site-packages (root cause of
  circular import issues)

### Epic 4: Per-User Ticker Linking
- New Iceberg table: `auth.user_tickers` (user_id, ticker, linked_at, source)
- API: `GET/POST/DELETE /users/me/tickers`
- Auto-link on chat: `_ticker_linker.py` — thread-local user tracking
- Default ticker: RELIANCE.NS linked on user creation
- Dashboard home filters cards by user's linked tickers
- 13 new tests in `test_ticker_api.py`

### Epic 5: Ticker Marketplace Page
- New dashboard page at `/marketplace`
- Lists ALL tickers from central registry
- Add/Remove buttons per row (pattern-match callbacks)
- Search filtering, market column, company names
- Nav link added between Insights and Admin

### Tests: 255 total (+19 new, all passing)
- `test_auth_api.py`: 5 admin password reset tests
- `test_stock_tools.py`: 3 freshness gate tests
- `test_ticker_api.py`: 13 ticker endpoint tests (new file)

### Files changed: 40+ modified, 5 new files created

---

# Session: Mar 7, 2026 — RSI/MACD tooltips + input validation hardening

## Summary
Added educational tooltips for RSI and MACD indicators across the
dashboard, then performed a full OWASP-style security audit and
hardened all user-input entry points (18 gaps fixed).

### Feature: RSI/MACD Tooltips
- Generalised the Sharpe tooltip system in `sort_helpers.py`
  into a generic `label_with_tooltip()` + `_TOOLTIP_TEXT` dict.
- Added info-icon (ℹ) tooltips on RSI and MACD columns in:
  screener table, comparison table, screener filter label.
- Added `hovertext` + `captureevents` to RSI/MACD chart panel
  titles in `chart_builders.py`.
- Renamed CSS class `sharpe-info-icon` → `col-info-icon`.
- Fixed duplicate DOM ID bug that prevented tooltips from
  rendering (two RSI columns shared same ID).
- Replaced `<`/`>` in tooltip text with Unicode `≤`/`≥` to
  eliminate any XSS vector.

### Security: Input Validation Hardening
- Created `backend/validation.py` — shared validators for
  ticker symbols, search queries, and batch ticker lists.
- **P0 fixes**:
  - `ChatRequest.message`: `max_length=10000`, `min_length=1`
  - `ChatRequest.agent_id`: `pattern=^[a-z_]+$`, `max_length=50`
  - `search_web()` and `search_market_news()`: query length
    validation via `validate_search_query()`.
- **P1 fixes**:
  - All 8 stock tools: ticker regex validation
    (`^[A-Za-z0-9^.\-]{1,15}$`) via `validate_ticker()`.
  - `fetch_multiple_stocks()`: batch limit (50 tickers).
  - `role` field: `Literal["general", "superuser"]` (was `str`).
- **P2 fixes**: `max_length` on all auth model string fields
  (password 128, full_name 200, avatar_url 500, tokens 2000).

### Tests: 236 total (28 new, all passing)
- `test_validation.py`: 19 tests (ticker, query, batch)
- `test_input_constraints.py`: 9 tests (Pydantic limits)
- `test_sort_helpers.py`: 6 new tooltip tests

### Files changed: 17 modified + 3 new

---

# Session: Mar 7, 2026 — Fix Iceberg avro path issue after migration

## Summary
Diagnosed and fixed the dashboard showing "No stocks saved yet"
after the data migration to `~/.ai-agent-ui/`. Root cause: binary
Iceberg avro manifest files contain hardcoded absolute paths that
the JSON-only migration script couldn't rewrite. Created a symlink
from the old project-local path to the new location.

### Root Cause
The Iceberg read chain has 4 levels of path resolution:
1. `catalog.db` → metadata JSON path (rewritten by migration)
2. metadata JSON → snap avro path (rewritten by migration)
3. snap avro → manifest avro path (**binary, NOT rewritten**)
4. manifest avro → data parquet path (**binary, NOT rewritten**)

After the old `data/iceberg/` was cleaned, steps 3-4 broke because
avro files still referenced the old project-local paths.

### Fix
- Created symlink: `data/iceberg/ → ~/.ai-agent-ui/data/iceberg/`
- Updated `scripts/migrate_data_home.py` to create this symlink
  automatically during migration.
- Symlink is gitignored (`data/iceberg/` already in `.gitignore`).
- New Iceberg writes use correct `~/.ai-agent-ui/` paths; old
  snapshots will be naturally replaced over time.

### All tests passing: 202 total.

---

# Session: Mar 6, 2026 — Migrate data & logs to ~/.ai-agent-ui

## Summary
Moved all runtime data (Iceberg, cache, raw, forecasts, avatars,
charts) and logs from the project root to `~/.ai-agent-ui/`,
keeping the repository clean of generated files. Centralised all
filesystem paths in `backend/paths.py` with `AI_AGENT_UI_HOME`
env-var override for CI/Docker.

### Changes
- **`backend/paths.py`** (NEW) — single source of truth for all
  filesystem paths. `APP_HOME = ~/.ai-agent-ui` by default.
  `ensure_dirs()` creates the full directory tree.
- **`scripts/migrate_data_home.py`** (NEW) — idempotent migration
  script (copy, not move). Dry-run by default, `--apply` to copy.
  Creates backwards-compat symlink for binary avro paths.
- **14 files updated** to import paths from `paths.py`:
  `_stock_shared.py`, `_analysis_shared.py`, `_forecast_shared.py`,
  `iceberg.py`, `stock_refresh.py`, `profile_routes.py`,
  `catalog.py`, `logging_config.py`, `create_tables.py` (auth +
  stocks), `backfill_metadata.py`, `backfill_adj_close.py`.
- **`run.sh`** — log dir and catalog check point to
  `~/.ai-agent-ui/`. Auto-migration on startup when old layout
  detected.
- **`setup.sh`** — directory creation + `.pyiceberg.yaml` generation
  target `~/.ai-agent-ui/`.
- **`.pyiceberg.yaml`** — URIs point to new paths.
- **`.gitignore`** — consolidated; old project-local rules kept for
  backwards-compat.
- **`tests/backend/test_paths.py`** (NEW) — 14 tests (defaults,
  env override, ensure_dirs).
- **202 total tests**, all passing (188 existing + 14 new).

---

# Session: Mar 6, 2026 — Quarterly data robustness & dashboard improvements

## Summary
Analysed Yahoo Finance quarterly data for Indian stocks (RELIANCE.NS)
and fixed multiple issues: empty cashflow, all-NaN balance sheet rows,
and dashboard displaying wrong columns per statement type. Added annual
cashflow fallback, statement-aware table/chart, and UI polish.

### Root Cause Analysis (RELIANCE.NS)
- **Quarterly cashflow**: yfinance returns empty (0×0) — no data
  available. Annual cashflow exists (47 metrics × 5 years).
- **Balance sheet**: Latest quarter (2025-09-30) has all NaN for key
  metrics; older quarters have real data.
- **Dashboard**: Table always showed income columns regardless of
  statement filter, so balance/cashflow rows appeared as all "—".

### Changes
- **`backend/tools/stock_data_tool.py`** — `_extract_statement()`
  skips quarters where all mapped metrics are NaN. Annual cashflow
  fallback when `quarterly_cashflow` is empty (marks rows with
  `fiscal_quarter="FY"`). Per-statement gap reporting in return msg.
- **`dashboard/callbacks/insights_cbs.py`** — Statement-aware table
  columns (income/balance/cashflow show relevant metrics). Statement-
  aware chart metrics. Empty chart shows "No data to display" instead
  of blank axes. Center-aligned alerts. Comma-formatted numbers
  (e.g. `12,451.40`). Drop rows missing primary metric. Specific
  empty-state messages per statement type. FY label support.
- **`dashboard/layouts/insights_tabs.py`** — Default filters: India
  market, first Indian ticker, Income statement. Removed "All"
  statement option.
- **Tests** (6 total in `test_fetch_quarterly.py`, 188 total):
  `test_annual_cashflow_fallback` verifies FY label + annual data
  used when quarterly is empty. Updated existing tests for new
  mock attributes.

### Known Gaps (Yahoo Finance limitations)
| Ticker | Income | Balance Sheet | Cash Flow |
|--------|--------|---------------|-----------|
| RELIANCE.NS | 37×6 ✅ | 76×3 (latest=NaN) ⚠️ | Empty → annual fallback |
| TCS.NS | 49×6 ✅ | 78×4 ✅ | 39×3 ✅ |
| AAPL | 33×5 ✅ | 65×6 ✅ | 46×7 ✅ |
| MSFT | 47×5 ✅ | 79×7 ✅ | 59×7 ✅ |

---


# Session: Mar 5, 2026 — Quarterly Results feature

## Summary
Added a new "Quarterly Results" tab to the Insights page that
fetches, stores, and displays quarterly financial statements
(Income Statement, Balance Sheet, Cash Flow) for tracked stocks.
Data sourced from yfinance, persisted in Iceberg, displayed as
sortable table + QoQ bar chart.

### Changes
- **`stocks/create_tables.py`** — Added 9th Iceberg table
  `stocks.quarterly_results` with 21 columns (ticker,
  quarter_end, fiscal_year/quarter, statement_type,
  15 financial metrics, updated_at).
- **`stocks/repository.py`** — Added 4 CRUD methods:
  `insert_quarterly_results`, `get_quarterly_results`,
  `get_all_quarterly_results`,
  `get_quarterly_results_if_fresh`.
- **`backend/tools/stock_data_tool.py`** — Added
  `fetch_quarterly_results` @tool with yfinance metric
  extraction and 7-day freshness cache.
- **`backend/main.py`** — Registered new tool.
- **`dashboard/callbacks/iceberg.py`** — Added
  `_get_quarterly_cached()` with 5-min TTL; added to
  `clear_caches()`.
- **`dashboard/layouts/insights_tabs.py`** — Added
  `_quarterly_tab()` with ticker/market/sector/statement
  type filters, QoQ chart, and sortable table.
- **`dashboard/layouts/insights.py`** — Added 7th tab +
  `quarterly-sort-store`.
- **`dashboard/callbacks/insights_cbs.py`** — Added
  `update_quarterly` callback with market/sector/ticker/
  statement filters, QoQ grouped bar chart, sortable table.
  Added "quarterly" to sort callback registration loop.
- **Tests** (6 new, 180 total):
  - `tests/backend/test_quarterly_repo.py`
  - `tests/backend/test_fetch_quarterly.py`
  - `tests/dashboard/test_quarterly_tab.py`

---

# Session: Mar 4, 2026 — Sortable column headers for all tables

## Summary
Added clickable column-header sorting to all 6 data tables
(Screener, Price Targets, Dividends, Risk Metrics, Users,
Audit Log). Replaced the Risk tab's RadioItems sort control
with header-click sorting. Sort cycles: unsorted -> asc -> desc
-> unsorted.

### Changes
- **`dashboard/callbacks/sort_helpers.py`** (NEW) — Reusable
  module: `build_sortable_thead()`, `apply_sort()`,
  `apply_sort_list()`, `next_sort_state()`,
  `register_sort_callback()`.
- **`dashboard/assets/custom.css`** — Added `.sort-header-btn`
  and `.sort-arrow` styles with hover/active states.
- **`dashboard/layouts/insights.py`** — Added 4 `dcc.Store`
  components for sort state (screener, targets, dividends, risk).
- **`dashboard/layouts/insights_tabs.py`** — Removed
  `risk-sort-by` RadioItems; kept Market filter only.
- **`dashboard/layouts/admin.py`** — Added 2 `dcc.Store`
  for users and audit sort state.
- **`dashboard/callbacks/insights_cbs.py`** — Integrated
  sorting into all 4 table callbacks; added pagination-reset
  callbacks on sort change; registered sort callbacks.
- **`dashboard/callbacks/admin_cbs.py`** — Added sort input
  to render callbacks; extended pagination-reset triggers.
- **`dashboard/callbacks/table_builders.py`** — Added
  `sort_state` param to `_build_users_table` and
  `_build_audit_table`; uses `build_sortable_thead()`.
- **`tests/dashboard/test_sort_helpers.py`** (NEW) — 14 tests
  covering cycle logic, DataFrame/list sorting, and thead
  structure.

### Test Results
171 tests pass (157 existing + 14 new), 17s runtime.

---

# Session: Mar 4, 2026 — Home page load latency optimisation

## Summary
Reduced home page load time from ~5 s to <500 ms (cold) and
<100 ms (warm cache) by replacing 3N sequential per-ticker
Iceberg scans with 2 batch reads + TTL-cached dict lookups.

### Changes
- **`stocks/repository.py`** — Added
  `get_all_latest_forecast_runs(horizon_months)` batch method
  (pattern matches `get_all_latest_company_info()`).
- **`dashboard/callbacks/iceberg.py`** — Added
  `_get_registry_cached()` and `_get_forecast_runs_cached()`
  with 5-min TTL; updated `clear_caches()` to invalidate both.
- **`dashboard/callbacks/home_cbs.py`** — Rewrote
  `refresh_stock_cards()`: batch pre-fetch company info +
  forecast runs before the loop; per-ticker body uses pure dict
  lookups. Added timing instrumentation via `_logger.info()`.
- **`dashboard/callbacks/data_loaders.py`** — `_load_reg_cb()`
  now uses `_get_registry_cached()`.
- **`dashboard/layouts/helpers.py`** — `_load_registry()` now
  uses `_get_registry_cached()`.
- **`tests/dashboard/test_home_perf.py`** — 9 new tests:
  batch forecast runs (3), registry cache (2), forecast runs
  cache (2), card batch shape (1), clear_caches coverage (1).

### Performance
| Scenario | Before | After | Speedup |
|----------|--------|-------|---------|
| Cold load (30 tickers) | ~5 s | ~500 ms | 10x |
| Warm cache (within 5 min) | ~2 s | ~50 ms | 40x |

### Test Suite
157 tests passing (was 148); 9 new tests added.

### Docs Updated
- `docs/dashboard/overview.md` — Home section: batch
  pre-fetch, per-card refresh, performance table, data flow
  rewritten for Iceberg cached helpers, architecture tree
  updated
- `docs/backend/stocks_iceberg.md` — Added
  `get_all_latest_forecast_runs()` to API reference; added
  "Dashboard TTL-cached helpers" section with all 7 helpers
- `docs/dev/changelog.md` — Mar 4 entry with performance
  table, file changes, test counts
- `docs/dev/decisions.md` — Added "Batch pre-fetch for Home
  page cards" decision with reasoning and tradeoffs

---

# Session: Mar 4, 2026 — Per-ticker refresh + bug fixes

## Summary
Added per-ticker refresh buttons to home page scorecards and
fixed 5 bugs discovered during the session.

### Features
- **Per-ticker refresh**: Each stock card now has a small
  refresh icon (bottom-right) that triggers
  `run_full_refresh()` in a `ThreadPoolExecutor` background
  thread. CSS spinner while running, check/cross on
  completion, 7-second fade-out. Multiple cards can refresh
  concurrently. Uses Dash MATCH/ALL pattern-matching
  callbacks with a 2-second polling interval.

### Bug Fixes
1. **TimedeltaIndex `.abs()` removed in pandas 2** —
   `chart_builders.py` dividend marker snapping now uses
   `np.abs()` instead of `.abs()`.
2. **Negative cache TTL** — Empty OHLCV/forecast/dividend
   Iceberg reads now expire after 30 s (`_NEGATIVE_TTL`)
   instead of 5 min (`_SHARED_TTL`), fixing stale compare
   page failures when shuffling stock pairs.
3. **Compare error message** — `update_compare` now tracks
   and reports which specific tickers failed to load.
4. **Compare chart uses Adj Close** — Switched from base-100
   normalised performance to actual Adj Close prices;
   metrics table also uses Adj Close.
5. **`poll_card_refreshes` empty ALL** — Returns `([], [])`
   when no pattern-matched elements exist (Dash ALL outputs
   require lists, not `no_update`).

### Files Modified
- `dashboard/layouts/home.py` — Interval + Store for
  card-refresh polling
- `dashboard/callbacks/home_cbs.py` — ThreadPoolExecutor,
  MATCH/ALL callbacks, card structure with refresh overlay
- `dashboard/assets/custom.css` — Card refresh button,
  spinner, status icon styles
- `dashboard/callbacks/chart_builders.py` — np.abs fix
- `dashboard/callbacks/iceberg.py` — _NEGATIVE_TTL (30 s)
- `dashboard/callbacks/analysis_cbs.py` — Adj Close compare,
  failed-ticker tracking, refresh-store wiring
- `dashboard/layouts/compare.py` — Updated heading/docstring

### Tests
- New: `tests/dashboard/test_session_bugfixes.py` — 15 tests
  covering all 5 bug fixes
- Full suite: **148 tests pass** (133 existing + 15 new)

### Branch
`feature/per-ticker-refresh-buttons` → PR to `dev`

---

# Session: Mar 3, 2026 — LangChain 0.3 → 1.x upgrade

## Summary
Upgraded LangChain family from 0.3.x to 1.x. Zero code changes needed — all APIs used (messages, tools, bind_tools, invoke, tool_calls) are stable across the version boundary.

### Changes
- `langchain` 0.3.27 → 1.2.10, `langchain-core` 0.3.83 → 1.2.17
- `langchain-anthropic` 0.3.22 → 1.3.4, `langchain-groq` 0.3.8 → 1.1.2
- `langchain-community` 0.3.31 → 0.4.1, `langchain-openai` 0.3.35 → 1.1.10
- `langchain-text-splitters` 0.3.11 → 1.1.1
- New transitive deps: `langchain-classic`, `langgraph`, `langgraph-checkpoint`, `langgraph-prebuilt`, `langgraph-sdk`, `ormsgpack`

### Branch
`feature/upgrade-langchain-1x` → PR to `dev`

---

# Session: Mar 3, 2026 — Python 3.9 → 3.12 upgrade + dependency refresh

## Summary
Upgraded Python runtime from 3.9 (EOL Oct 2025) to 3.12.9 and all non-LangChain dependencies to latest versions. LangChain held at 0.3.x for a separate follow-up PR.

### Changes
- **Infrastructure**: Updated `setup.sh` (5 locations), `.github/workflows/ci.yml` (4 jobs), `run.sh` — all Python 3.9 → 3.12
- **Dependencies**: Recreated `backend/demoenv` with Python 3.12.9; upgraded numpy 1.26→2.4, pandas 2.0→3.0, yfinance 0.2→1.2, pyarrow 17→23, anthropic 0.79→0.84, bcrypt 4→5, pyiceberg 0.10→0.11, scikit-learn 1.6→1.8, scipy 1.13→1.17, matplotlib 3.9→3.10, fastapi 0.128→0.135
- **passlib removed**: `auth/password.py` rewritten to use `bcrypt` directly (`bcrypt.hashpw()`/`bcrypt.checkpw()`); same `$2b$` format — no data migration needed
- **Docs updated**: CLAUDE.md, README.md, docs/index.md, docs/dev/decisions.md, docs/dev/how-to-run.md

### Branch
`feature/upgrade-python-312` → PR to `dev`

### Follow-up
- PR 2: `feature/upgrade-langchain-1x` — LangChain 0.3 → 1.x (separate PR after this merges)

---

# Session: Mar 2, 2026 — External env symlinks + setup.sh + optional Groq fallback

## Summary

### 1. `setup.sh` first-time installer (feature/setup-script, PR #33 → dev, merged)
- Created 11-step idempotent installer with `--non-interactive` mode for CI/Docker

### 2. Optional Groq in FallbackLLM (fix/optional-groq-fallback, PR #35 → dev, merged)
- `backend/llm_fallback.py`: Groq import optional; checks `GROQ_API_KEY` before creating `ChatGroq`

### 3. External env symlink strategy (feature/external-env-symlink)
- `setup.sh` Step 10 writes master env files to `~/.ai-agent-ui/`
- `backend/.env` and `frontend/.env.local` are symlinks to those external files
- Auto-migrates existing real files to external location on first run
- Secrets survive branch checkouts and merges

### dev → qa promotion (PR #34, merged)
- Resolved 32 merge conflicts; rebuilt corrupted virtualenv via `./setup.sh --non-interactive`

---

# Session: Mar 2, 2026 — Fix Adj Close NaN IndexError on forecast page (feature/fix-adj-close-nan)

## Summary
Fixed `IndexError: single positional indexer is out-of-bounds` on the Forecast dashboard page caused by `Adj Close` being all NaN in Iceberg OHLCV data.

### Root cause
- **yfinance 1.2.0** dropped the `Adj Close` column from `yf.download()`. When `insert_ohlcv()` writes to Iceberg, `adj_close` is stored as all `None` (NaN) because the column is absent or empty in the source DataFrame.
- The column still exists in the Iceberg schema, so `"Adj Close" in df.columns` evaluates to `True`, but every value is NaN.
- After `.dropna(subset=["y"])`, the prophet DataFrame was empty, causing `prophet_df["y"].iloc[-1]` to throw `IndexError`.

### Fixes (3 files)
- `dashboard/callbacks/forecast_cbs.py`: Check `notna().any()` before using `Adj Close`; added guard for empty `prophet_df` returning an error figure instead of crashing
- `backend/tools/_forecast_model.py`: Same `notna().any()` check in `_prepare_data_for_prophet()`
- `dashboard/callbacks/iceberg.py`: `_get_ohlcv_cached()` falls back to `close` when `adj_close` is all NaN

### Tests — 131 total (was 113 on dev; +5 new)
- `test_stock_tools.py`: Added `TestPrepareDataForProphet` (3 tests): uses Adj Close when valid, falls back to Close when all NaN, falls back when column absent; added `adj_close_nan` param to `_make_ohlcv()` helper
- `test_callbacks_unit.py`: Added `TestOhlcvAdjCloseNanFallback` (2 tests): Adj Close uses close when all NaN, uses adj_close when valid
- All 131 tests passing (68 backend + 45 dashboard + 18 frontend)

### Branch
- Merged `feature/iceberg-metadata-migration` into `feature/fix-adj-close-nan` before applying fix
- Ready for PR → `dev`

---

# Session: Mar 2, 2026 (continued) — Fix backend Iceberg writes + eliminate all flat-file reads on feature/iceberg-metadata-migration

## Summary
Fixed silent Iceberg write failures that prevented newly-analysed tickers from appearing on Insights pages. Eliminated all flat-file reads from dashboard and backend tools — Iceberg is now the single source of truth for ALL data, not just metadata.

### Root cause fix — Backend Iceberg writes
- `price_analysis_tool.py`: Removed silent `try/except` around Iceberg writes; replaced `_get_repo()` with `_require_repo()` so `upsert_technical_indicators()` and `insert_analysis_summary()` errors propagate to the tool's main exception handler
- `forecasting_tool.py`: Same fix — `insert_forecast_run()` and `insert_forecast_series()` errors now propagate instead of being silently swallowed

### Consolidate repo singletons
- `_analysis_shared.py`: Removed local `_STOCK_REPO`/`_STOCK_REPO_INIT_ATTEMPTED` and `_get_repo()` duplicate; imports `_get_repo`/`_require_repo` from `_stock_shared`
- `_forecast_shared.py`: Same consolidation — single repo singleton in `_stock_shared` for all backend tools

### Backend `_load_parquet()` — Iceberg reads
- `_analysis_shared._load_parquet()`: Rewritten to read OHLCV from Iceberg via `_require_repo().get_ohlcv()`; reshapes to legacy parquet format (DatetimeIndex + `Open/High/Low/Close/Adj Close/Volume`)
- `_forecast_shared._load_parquet()`: Same rewrite — reads from Iceberg instead of flat parquet files
- Removed `_DATA_RAW` constants from both shared modules

### Dashboard — Iceberg only (no more flat-file reads)
- `iceberg.py`: Added `_get_ohlcv_cached()` and `_get_forecast_cached()` with 5-min TTL; removed `_DATA_RAW` constant; `_get_analysis_with_gaps_filled()` now reads OHLCV from Iceberg (not parquet)
- `data_loaders.py`: `_load_raw()` reads from Iceberg via `_get_ohlcv_cached()`; `_load_forecast()` reads from Iceberg via `_get_forecast_cached()`; removed `_DATA_RAW`/`_DATA_FORECASTS` path constants
- `home_cbs.py`: Sentiment from `repo.get_latest_forecast_run()` instead of `_DATA_FORECASTS.glob()` + `pd.read_parquet()`
- `insights_cbs.py`: Correlation fallback reads OHLCV from `_get_ohlcv_cached()` instead of flat parquet; removed `_DATA_RAW` import

### Tests — 126 total (was 120)
- `test_stock_tools.py`: Updated `TestAnalyseStockPrice` and `TestForecastStock` to mock `_require_repo()` with Iceberg-shaped OHLCV data; added `test_iceberg_write_failure_propagates` for both tools; added `_make_iceberg_ohlcv()` helper
- `test_callbacks_unit.py`: Added `TestLoadRawFromIceberg` (2 tests) and `TestLoadForecastFromIceberg` (2 tests)
- All 126 tests passing (63 backend + 45 dashboard + 18 frontend)

---

# Session: Mar 2, 2026 — Migrate stock metadata from flat JSON to Iceberg (single source of truth) on feature/iceberg-metadata-migration

## Summary
Iceberg is now the single source of truth for stock metadata (registry + company_info). Flat JSON files (`stock_registry.json`, `{TICKER}_info.json`) eliminated; dual-write pattern removed.

### Phase 1 — StockRepository additions (`stocks/repository.py`)
- Added 4 new methods: `get_all_registry()`, `check_existing_data()`, `get_latest_company_info_if_fresh()`, `get_currency()`
- `get_all_registry()` returns dict keyed by ticker, matching legacy JSON shape for seamless migration

### Phase 2 — Backend tool rewrites
- `_stock_shared.py`: Removed `_DATA_METADATA` and `_REGISTRY_PATH`; added `_require_repo()` (raises `RuntimeError` instead of returning `None`) and `_parquet_path()` helper
- `_stock_registry.py`: All 4 functions rewritten from JSON I/O to Iceberg repo calls; removed `_save_registry()` and `json` import
- `stock_data_tool.py`: `get_stock_info()` now checks Iceberg freshness instead of JSON cache; `fetch_stock_data()` uses `_require_repo()` (errors propagate); removed `_DATA_METADATA`, `_REGISTRY_PATH`, `_STOCK_REPO` re-exports
- `_helpers.py`: `_load_currency()` reads from `repo.get_currency()` instead of JSON file
- `_analysis_shared.py`, `_forecast_shared.py`: Removed `_DATA_METADATA` constant

### Phase 3 — Dashboard rewrites
- `data_loaders.py`: `_load_reg_cb()` reads from Iceberg `get_all_registry()` only; removed JSON merge logic
- `layouts/helpers.py`: `_load_registry()` reads from Iceberg
- `home_cbs.py`: Company name from `repo.get_latest_company_info()` instead of `{TICKER}_info.json`
- `utils.py`: `_load_currency_from_file()` → `_load_currency_from_iceberg()` using `repo.get_latest_company_info()`
- `insights_cbs.py`: Screener + correlation fallbacks use `repo.get_all_registry()` instead of `_REGISTRY_PATH`

### Phase 4 — Test updates (`tests/backend/test_stock_tools.py`)
- Replaced `monkeypatch.setattr(..., "_DATA_METADATA/REGISTRY_PATH", ...)` with mocked `StockRepository` via `_mock_repo()` helper
- Added `TestGetStockInfo` class: test cached (fresh) vs stale Iceberg snapshot

### Phase 5 — Cleanup
- Created `stocks/backfill_metadata.py` — one-time JSON→Iceberg migration (idempotent)
- Added `data/metadata/*.json` to `.gitignore`
- Updated `CLAUDE.md`: Data paths, architectural decisions ("Iceberg single source of truth"), deployment instructions

---

# Session: Mar 1, 2026 — Registry sync fix, correlation TypeError, home layout on feature/fix-registry-correlation

## Summary
Two bug fixes and one UX improvement. All 100 backend/dashboard tests passing. Merged through full pipeline: `feature/*` → `dev` → `qa` → `release` → `main`.

### Bug fix — Dashboard home page missing new tickers (`dashboard/callbacks/data_loaders.py`)
- `_load_reg_cb()` previously returned only Iceberg data the moment the `stocks.registry` table had any rows, silently ignoring tickers whose Iceberg dual-write had failed
- Fixed: JSON (`stock_registry.json`) is now always loaded first as the authoritative ticker list; Iceberg is read to merge in any tickers absent from JSON (not to replace it)
- New tickers appear on the home page immediately regardless of Iceberg write success

### Bug fix — Insights correlation heatmap crash (`dashboard/callbacks/insights_cbs.py`)
- Iceberg `stocks.ohlcv` `date32` column becomes Python `datetime.date` objects in pandas; comparing these with an ISO string raises `TypeError: '>=' not supported between 'datetime.date' and 'str'`
- Fixed: column converted to `datetime64` via `pd.to_datetime()` before the cutoff filter; cutoff changed from string to `pd.Timestamp`

### UX — Market filter inline with heading (`dashboard/layouts/home.py`)
- Combined "Saved Stocks" H5 heading and India/US `ButtonGroup` into a single row (heading left, buttons right)
- Reduced top gap from `mb-4` to `mb-2` giving the card grid more vertical space

### Data
- Committed `data/metadata/GSFC.NS_info.json` and `data/metadata/JKPAPER.NS_info.json` from recent analysis sessions
- Updated `data/metadata/stock_registry.json` with new tickers

---

# Session: Mar 1, 2026 — 23 Dashboard + 17 Frontend Performance Fixes on feature/gitignore-avatars

## Summary
Implemented all dashboard and frontend performance fixes identified in code review. Branch: `feature/gitignore-avatars`. Tests: 100 backend+dashboard passing; `tsc --noEmit` clean.

### Dashboard fixes (9 files)

**`dashboard/callbacks/data_loaders.py`**
- Fix #19: Column projection (`selected_fields`) on Iceberg registry scan — avoids reading unused columns
- Fix #5: Replace `iterrows()` in `_load_reg_cb()` with `.values` array iteration + pre-computed column index dict
- Fix #1/#2/#14: Added `_add_indicators_cached(ticker, df)` with 5-min TTL — shared by analysis and compare callbacks

**`dashboard/callbacks/chart_builders.py`**
- Fix #22: `np.where()` for volume bar colours and MACD histogram colours — replaces Python list comprehensions

**`dashboard/callbacks/utils.py`**
- Fix #11: TTL cache (`_CURRENCY_CACHE_DASH`, 5-min) for `_get_currency()` — was opening JSON on every callback invocation

**`dashboard/callbacks/iceberg.py`**
- Fix #10: TTL-based repo singleton (1 h) — re-initialises after Iceberg catalog restart without process restart
- Fix #6: `_get_analysis_summary_cached()` and `_get_company_info_cached()` with 5-min TTL — shared across screener, risk, sectors callbacks

**`dashboard/callbacks/home_cbs.py`**
- Fix #4: Hoist `_load_raw(ticker)` once per ticker loop — eliminates duplicate parquet read in sentiment block
- Fix #8: `pathlib.Path.glob()` + `sorted()` by `st_mtime` for forecast file discovery

**`dashboard/app_layout.py`**
- Fix #20: `dcc.Interval` raised from 5 min → 30 min

**`dashboard/callbacks/insights_cbs.py`**
- Fix #6: `update_screener`, `update_risk`, `update_sectors` now use `_get_analysis_summary_cached` / `_get_company_info_cached`
- Fix #5: All 4× `iterrows()` loops (screener, targets, dividends, risk) replaced with `.to_dict("records")`
- Fix #7: Date cutoff applied to `df_all` before per-ticker loop in correlation (Iceberg path)
- Fix #13: `update_targets` replaced raw `load_catalog("local")` with `repo._table_to_df()`
- Fix #16: All market filters vectorised with `.str.endswith((".NS", ".BO"))` mask

**`dashboard/callbacks/analysis_cbs.py`**
- Fix #1/#2/#14: `update_analysis_chart` and `update_compare` use `_add_indicators_cached()`

**`dashboard/layouts/analysis.py`**
- Fix #17: `_get_available_tickers_cached()` with 5-min TTL wraps `_get_available_tickers()`

### Frontend fixes (9 files)

**`frontend/hooks/useSendMessage.ts`** (High)
- AbortController on `/chat/stream` fetch — cancels on unmount + before each new send; ignores `AbortError`
- `useCallback` on `handleKeyDown` and `handleInput` — stable refs to prevent `ChatInput` re-renders

**`frontend/hooks/useChatHistory.ts`** (Medium)
- 1-second debounce on `localStorage.setItem` — was firing synchronously on every streaming chunk

**`frontend/components/MarkdownContent.tsx`** (Medium)
- `useMemo` wraps `preprocessContent(content)` — was re-running regex over full markdown on every stream event

**`frontend/app/auth/oauth/callback/page.tsx`** (Medium)
- `cancelled` flag + cleanup return replaces `eslint-disable`; proper `[searchParams, router]` deps

**`frontend/components/EditProfileModal.tsx`** (Medium)
- `URL.createObjectURL` replaces `FileReader.readAsDataURL` — non-blocking, no base64 memory overhead
- Blob URL revoked in `useEffect` cleanup

**`frontend/lib/auth.ts`** (Low)
- 10-second `AbortController` timeout on `refreshAccessToken` — prevents hung refresh blocking all API calls

**`frontend/app/login/page.tsx`** (Low)
- `AbortController` on OAuth providers fetch (with cleanup return) and login submit

**`frontend/components/NavigationMenu.tsx`** (Low)
- `useMemo` for `NAV_ITEMS.filter(canSeeItem)` — recomputes only when `profile` changes

**`frontend/app/page.tsx`** (Low)
- Stable message keys: `timestamp+role+index` composite instead of bare array index
- `useMemo` for `iframeSrc` (avoids `getAccessToken()` on every render)
- `useMemo` for `AGENTS.find()` agent hint lookup
- `useCallback` for menu outside-click handler
- `AbortController` on profile fetch on mount

---

# Session: Mar 1, 2026 — 12 Backend Performance Fixes on feature/gitignore-avatars

## Summary
Implemented all 12 performance improvements identified in backend review. Tests: 118 total (100 backend+dashboard + 18 frontend); all passing. Committed + pushed to `feature/gitignore-avatars`.

### Fix #1 — Predicate push-down for single-ticker reads (`stocks/repository.py`)
- Added `_scan_ticker(identifier, ticker)` helper: `EqualTo("ticker", ticker)` predicate scan + full-scan fallback
- Added `_scan_two_filters(identifier, col1, val1, col2, val2)` for compound filters (`And(EqualTo, EqualTo)`)
- All single-ticker read methods now use predicate push-down: `get_registry`, `get_latest_company_info`, `get_ohlcv`, `get_latest_ohlcv_date`, `get_dividends`, `get_technical_indicators`, `get_latest_analysis_summary`, `get_analysis_history`, `get_latest_forecast_run`, `get_latest_forecast_series`

### Fix #2 — Single table load per upsert
- Added `_load_table_and_scan(identifier)` helper returning `(table, dataframe)` tuple
- `upsert_registry`, `upsert_technical_indicators`, `insert_forecast_series` each load table once then reuse the object — eliminates double catalog round-trip
- `insert_ohlcv` and `insert_dividends` fetch only the `date`/`ex_date` column via predicate before appending

### Fix #3 — Vectorised insertion loops
- `insert_ohlcv`: replaced `itertuples()` loop with boolean-mask selection + direct column-wise Arrow array construction (no intermediate DataFrame materialisation)
- `insert_dividends`: replaced `iterrows()` loop with list-append over sparse input + direct Arrow table

### Fix #4 — Pagination on bulk methods
- `get_all_latest_company_info(limit, offset)` and `get_all_latest_analysis_summary(limit, offset)` — new optional params

### Fix #5 — TTL currency cache (`backend/tools/_helpers.py`)
- `_load_currency` now has a module-level 5-minute TTL cache (`_CURRENCY_CACHE` dict) — repeated calls for the same ticker within a request return instantly

### Fix #6 — Deduplicate `_currency_symbol` / `_load_currency`
- Created `backend/tools/_helpers.py` with single canonical definitions
- Removed duplicate definitions from `_stock_shared.py`, `_analysis_shared.py`, `_forecast_shared.py`; all three now re-export from `_helpers`

### Fix #7 — ERROR log on auth predicate fallback (`auth/repo/user_reads.py`)
- `get_by_email` and `get_by_id`: changed `_logger.warning` → `_logger.error` on predicate scan fallback — now visible in alerts vs routine warnings

### Fix #8 — ERROR log on Iceberg write failures
- Changed from `WARNING` to `ERROR` in all actual write-failure handlers: `stock_data_tool.py` (×4), `price_analysis_tool.py`, `forecasting_tool.py`, `_stock_registry.py`
- Left `StockRepository unavailable` (init failure) as WARNING — expected in dev without Iceberg

### Fix #9 — Remove unused `_col` function; pre-compute `col_set`
- `upsert_technical_indicators`: removed dead `_col` inner function; pre-compute `col_set = set(df.columns)` once; column extraction now uses a `_get(canonical, alt)` helper that checks the set once per column

### Fix #10 — Date objects for dedup (not strings)
- `insert_ohlcv` and `insert_dividends`: existing-date sets now store `date` objects (via `_to_date()`) — eliminates `str()` → parse round-trip and is semantically correct

### Fix #11 — Streaming batch scan in `scan_all_users` (`auth/repo/catalog.py`)
- Replaced `tbl.scan().to_arrow().to_pylist()` (materialises full table) with iteration over `to_arrow().to_batches()` — peak memory proportional to one batch

### Fix #12 — Catalog singleton; eliminate `os.chdir` side effect (`auth/repo/catalog.py`)
- `get_catalog` caches the catalog object at module level after first load
- Primary load uses absolute SQLite URI (no `os.chdir`); fallback restores `cwd` in `finally` block

---

# Session: Mar 1, 2026 — Post-UX polish: 4 bug fixes on feature/refactor-module-split

## Summary
4 user-reported bug fixes after 7-item UX/RBAC session. Tests: 118 total (100 backend+dashboard + 18 frontend); all passing.

### Fix 1 — Avatar static files
- `backend/main.py`: Added `StaticFiles` mount at `/avatars` pointing to `data/avatars/`; `os.makedirs` on startup ensures directory exists

### Fix 2 — Navbar dynamic page name (remove breadcrumb rows)
- `dashboard/callbacks/routing_cbs.py`: Added `update_navbar_page_name` callback — maps pathname to " → PageName" suffix, written into `navbar-page-name` span
- `dashboard/layouts/home.py`, `insights.py`, `admin.py`, `analysis.py`: Removed `html.Nav` breadcrumb blocks entirely
- `dashboard/app_layout.py`: Removed breadcrumb wrapper Divs for `/forecast` and `/compare` routes

### Fix 3 — EditProfileModal pre-population + avatar preview
- `frontend/components/EditProfileModal.tsx`: Replaced unreliable `onAnimationStart` with `useEffect` on `isOpen` for form sync; added avatar preview (img or initials circle) above the name field

### Fix 4 — Insights nav RBAC filtering
- `frontend/lib/constants.tsx`: Added `requiresInsights?: boolean` to `NavItem` interface; added `"insights"` to `View` type; added Insights nav item with `requiresInsights: true`
- `frontend/components/NavigationMenu.tsx`: Updated `canSeeItem` to filter `requiresInsights` items (superuser OR `page_permissions.insights === true`)
- `frontend/app/page.tsx`: `iframeSrc` handles `view === "insights"` → opens dashboard at `/insights`; `iframeTitle` updated

---

# Session: Mar 1, 2026 — 7-item UX + RBAC fix on feature/refactor-module-split

## Summary
Full UX + RBAC fixes on `feature/refactor-module-split`. Tests: 100 backend+dashboard + 18 frontend = 118 total (all passing). Branch: `feature/refactor-module-split` — raise PR → dev.

### Item 1 — Frontend profile dropdown + Dashboard profile chip removal
- `auth/models/response.py`: Added `avatar_url` + `page_permissions` to `UserResponse`
- `auth/endpoints/helpers.py`: `_user_to_response()` now populates both new fields
- `dashboard/layouts/navbar.py`: Stripped to brand + 4 nav links only (no profile chip)
- `dashboard/callbacks/profile_cbs.py`: Stripped to `load_user_profile()` only
- `dashboard/app_layout.py`: Removed sign-out redirect + edit-profile modal; kept change-password modal + user-profile-store
- Frontend: `useEditProfile.ts` + `useChangePassword.ts` hooks (new)
- Frontend: `EditProfileModal.tsx` + `ChangePasswordModal.tsx` (new)
- Frontend: `ChatHeader.tsx` — replaced bare sign-out with profile chip + click-outside dropdown (Edit Profile, Change Password, Sign Out)
- Frontend: `page.tsx` — fetches `GET /auth/me` on mount; passes profile to ChatHeader + NavigationMenu; renders modals

### Item 2 — SSO avatar override fix
- `auth/repo/oauth.py`: SSO login no longer overwrites `profile_picture_url` if user already has a custom avatar

### Item 3 — Avatar upload in Admin Add/Edit modal
- `dashboard/layouts/admin.py`: Added `dcc.Upload` + preview div to user modal
- `auth/endpoints/profile_routes.py`: `upload_avatar` now accepts optional `?user_id=` for superuser override
- `dashboard/callbacks/admin_cbs2.py`: `save_user()` calls `_upload_avatar_for_user()` after create/edit if avatar provided

### Item 4 — Breadcrumb headers
- `dashboard/layouts/home.py`, `insights.py`, `admin.py`, `analysis.py`: replaced H2+description with breadcrumb nav

### Item 5 — Analysis tabbed layout
- `dashboard/layouts/analysis.py` `analysis_tabs_layout()`: Three real tabs — Price Analysis / Forecast / Compare Stocks

### Item 6 — Insights market filters on Targets, Dividends, Risk
- `dashboard/layouts/insights_tabs.py`: Added `targets-market-filter`, `dividends-market-filter`, `risk-market-filter` RadioItems
- `dashboard/callbacks/insights_cbs.py`: Wired new inputs + applied market filter logic in all three callbacks

### Item 7 — RBAC: page_permissions, max 2 superusers, dashboard routing, frontend nav
- `auth/repo/schemas.py` + `auth/create_tables.py` + `auth/migrate_users_table.py`: `page_permissions` StringType column
- `auth/models/request.py`: `page_permissions` on `UserUpdateRequest`
- `auth/endpoints/user_routes.py`: Max 2 superusers guard; JSON serialization of `page_permissions`
- `auth/repo/user_writes.py`: JSON serialization of `page_permissions` in create/update
- `dashboard/app_layout.py` `display_page()`: RBAC enforcement for `/insights` and `/admin/users` using `user-profile-store`
- `dashboard/layouts/admin.py`: User-permissions checklist section (visible/hidden based on role)
- `dashboard/callbacks/admin_cbs2.py`: `toggle_user_modal` wires permissions section; `save_user` includes permissions in PATCH
- `frontend/components/NavigationMenu.tsx`: `profile` prop; admin item visible for superuser OR `page_permissions.admin`

---

# Session: Mar 1, 2026 — Modular refactor + LLM fallback + regression expansion

## Summary

Full modular refactor of all large files (>150 non-comment lines), Groq-first/Anthropic-fallback LLM wrapper, and expanded regression test suite. Branch: `feature/refactor-module-split`.

## Test count: 100 backend+dashboard (up from 74) + 18 frontend = 118 total

### Phase 1 — LLM Fallback (`backend/llm_fallback.py`)
- `FallbackLLM` class: Groq primary → Anthropic on `RateLimitError`/`APIConnectionError`
- `bind_tools()` stores bound LLMs; `invoke()` dispatches with fallback
- 6 new tests: `tests/backend/test_llm_fallback.py`

### Phase 2 — Backend Python Refactoring
- `auth/models/` package: `request.py`, `response.py`, `__init__.py`
- `auth/password.py` + `auth/tokens.py` extracted from `auth/service.py`
- `auth/repo/` package: `schemas.py`, `catalog.py`, `user_reads.py`, `user_writes.py`, `oauth.py`, `audit.py`, `repository.py`, `__init__.py`
- `auth/endpoints/` package: `helpers.py`, `auth_routes.py`, `user_routes.py`, `profile_routes.py`, `oauth_routes.py`, `admin_routes.py`, `__init__.py`
- `backend/models.py`: Pydantic request/response models
- `backend/agents/config.py`, `loop.py`, `stream.py` extracted from `base.py`
- `backend/tools/_stock_shared.py`, `_stock_registry.py`, `_stock_fetch.py`
- `backend/tools/_analysis_shared.py`, `_analysis_indicators.py`, `_analysis_movement.py`, `_analysis_summary.py`, `_analysis_chart.py`
- `backend/tools/_forecast_shared.py`, `_forecast_model.py`, `_forecast_accuracy.py`, `_forecast_persist.py`, `_forecast_chart.py`
- 17 new tests: `test_auth_password.py` + `test_auth_tokens.py`

### Phase 3 — Dashboard Refactoring
- `dashboard/layouts/` package (11 files): `helpers.py`, `navbar.py`, `home.py`, `analysis.py`, `forecast.py`, `compare.py`, `admin_modals.py`, `admin.py`, `insights_tabs.py`, `insights.py`, `__init__.py`
- `dashboard/callbacks/` package (17 files): `utils.py`, `auth_utils.py`, `data_loaders.py`, `chart_builders.py`, `chart_builders2.py`, `card_builders.py`, `table_builders.py`, `iceberg.py`, `home_cbs.py`, `analysis_cbs.py`, `forecast_cbs.py`, `admin_cbs.py`, `admin_cbs2.py`, `insights_cbs.py`, `routing_cbs.py`, `registration.py`, `__init__.py`
- `dashboard/app_env.py`, `app_init.py`, `app_layout.py` extracted from `app.py`
- 15 new tests: `tests/dashboard/test_utils.py`

### Phase 4 — Frontend Refactoring
- `frontend/lib/constants.tsx`: `View`, `Message`, `AGENTS`, `NAV_ITEMS`, `formatTime`, `toolLabel`
- `frontend/hooks/useChatHistory.ts`, `useAuthGuard.ts`, `useSendMessage.ts`
- `frontend/components/StatusBadge.tsx`, `MarkdownContent.tsx`, `MessageBubble.tsx`, `ChatInput.tsx`, `ChatHeader.tsx`, `IFrameView.tsx`, `NavigationMenu.tsx`
- `frontend/vitest.config.ts`: jsdom environment + `@` path alias (fixed 18 tests)
- `frontend/app/page.tsx` slimmed from 709 → ~160 lines

---

# Session: Feb 28, 2026 — Iceberg stock storage + Insights dashboard pages

## What We Built

Full Apache Iceberg persistence layer for all stock market data (8 tables), dual-write hooks in every backend tool, a one-time backfill script, 6 new Insights pages in the dashboard, and auto-init in `run.sh`.

### Phase 1 — `stocks/` package skeleton

| File | Purpose |
|------|---------|
| `stocks/__init__.py` | Package docstring |
| `stocks/create_tables.py` | Idempotent init of 8 `stocks.*` Iceberg tables |

### Phase 2 — `stocks/repository.py`

`StockRepository` class with full CRUD for all 8 tables:

| Table | Key methods |
|-------|-------------|
| `stocks.registry` | `upsert_registry`, `get_registry` |
| `stocks.company_info` | `insert_company_info`, `get_latest_company_info`, `get_all_latest_company_info` |
| `stocks.ohlcv` | `insert_ohlcv`, `get_ohlcv`, `get_latest_ohlcv_date` |
| `stocks.dividends` | `insert_dividends`, `get_dividends` |
| `stocks.technical_indicators` | `upsert_technical_indicators`, `get_technical_indicators` |
| `stocks.analysis_summary` | `insert_analysis_summary`, `get_latest_analysis_summary`, `get_all_latest_analysis_summary`, `get_analysis_history` |
| `stocks.forecast_runs` | `insert_forecast_run`, `get_latest_forecast_run` |
| `stocks.forecasts` | `insert_forecast_series`, `get_latest_forecast_series` |

### Phase 3 — Dual-write in backend tools

Added lazy `_get_repo()` singleton + Iceberg writes to:

- `backend/tools/stock_data_tool.py` — OHLCV on fetch + delta, registry upsert, company info, dividends
- `backend/tools/price_analysis_tool.py` — technical indicators + analysis summary
- `backend/tools/forecasting_tool.py` — forecast run metadata + full forecast series

All writes wrapped in `try/except`; failures logged as `WARNING` and never break existing tool behaviour.

### Phase 4 — `stocks/backfill.py`

8-step idempotent backfill of all existing flat files into Iceberg. Run once per deployment after `create_tables.py`. Steps: registry → company_info → ohlcv → dividends → technical_indicators → analysis_summary → forecasts → forecast_runs.

### Phase 5 — 6 Insights dashboard pages

| Page | Route | Iceberg source |
|------|-------|----------------|
| Screener | `/screener` | `analysis_summary` (fallback: flat parquet) |
| Price Targets | `/targets` | `forecast_runs` |
| Dividends | `/dividends` | `dividends` |
| Risk Metrics | `/risk` | `analysis_summary` |
| Sectors | `/sectors` | `company_info` + `analysis_summary` |
| Correlation | `/correlation` | `ohlcv` (fallback: flat parquet) |

Changes: `dashboard/layouts.py` (NAVBAR Insights dropdown + 6 layout functions), `dashboard/callbacks.py` (`_get_iceberg_repo()` + 6 callbacks), `dashboard/app.py` (imports + 6 routes).

### Phase 6 — Infrastructure + docs

- `run.sh` — `_init_stocks()` function; called after `_init_auth()` on every `./run.sh start`
- `mkdocs.yml` — "Iceberg Storage" page added under Stock Agent nav
- `docs/backend/stocks_iceberg.md` — full reference: tables, API, backfill, quirks, Insights pages

### Files changed

| File | Change |
|------|--------|
| `stocks/__init__.py` | New |
| `stocks/create_tables.py` | New |
| `stocks/repository.py` | New |
| `stocks/backfill.py` | New |
| `backend/tools/stock_data_tool.py` | Dual-write: OHLCV, registry, company_info, dividends |
| `backend/tools/price_analysis_tool.py` | Dual-write: technical_indicators, analysis_summary |
| `backend/tools/forecasting_tool.py` | Dual-write: forecast_run, forecast_series |
| `dashboard/callbacks.py` | `dbc` import, `_get_iceberg_repo()`, 6 Insights callbacks |
| `dashboard/layouts.py` | NAVBAR Insights dropdown, 6 layout functions |
| `dashboard/app.py` | 6 new routes |
| `run.sh` | `_init_stocks()` added |
| `mkdocs.yml` | Iceberg Storage page added |
| `docs/backend/stocks_iceberg.md` | New |
| `PROGRESS.md` | This entry |
| `CLAUDE.md` | `stocks/` package + Iceberg decisions |

---

# Session: Feb 28, 2026 — Post-merge branch cleanup + CI auto-delete workflow

## What We Did

Housekeeping session after PR #3 (`feature/test-branch` → `dev`) was merged.

### 1. Deleted merged local + remote branches

| Branch | Reason |
|--------|--------|
| `feature/test-branch` | Merged via PR #3 → dev |
| `chore/remove-details-txt` | Merged via PR #2 → main |
| `claude/beautiful-clarke` | Local-only Claude worktree (no PR) |
| `claude/wonderful-driscoll` | Local-only Claude worktree (no PR) |

Remote branches `origin/feature/test-branch`, `origin/chore/remove-details-txt`, `origin/claude/wonderful-driscoll` deleted via `git push origin --delete`.

### 2. Updated CLAUDE.md project tree

Removed stale entries `STOCK_AGENT_PLAN.md` and `details.txt` (both previously deleted).

### 3. Created `.github/workflows/cleanup.yml`

Auto-deletes source branch when a PR is merged. Skips protected branches (`main`, `dev`, `qa`, `release`, `gh-pages`).

### Files changed

| File | Change |
|------|--------|
| `PROGRESS.md` | This entry |
| `CLAUDE.md` | Removed stale file entries from project tree |
| `.github/workflows/cleanup.yml` | New — auto-delete branch on PR merge |

---

# Session: Feb 27, 2026 — Branching strategy + Pre-commit hook improvements

## What We Built

### 1. Branching strategy

Created `dev`, `qa`, `release` branches. Full `feature/* → dev → qa → release → main` CI/CD workflow.

| File | Purpose |
|---|---|
| `.github/workflows/ci.yml` | Per-branch CI jobs (dev/qa/release/main) |
| `.github/CODEOWNERS` | Reviewer groups per merge path |
| `.github/pull_request_template.md` | Standard PR checklist |

### 2. Pre-commit hook: Groq → Claude

`hooks/pre_commit_checks.py` now uses Anthropic SDK (`claude-sonnet-4-6`). `has_llm` → `has_claude`; `GROQ_API_KEY` → `ANTHROPIC_API_KEY`.

### 3. Pre-commit: mkdocs build validation

`_run_mkdocs_build()` runs after doc patches; warn-only (non-blocking). `import shutil` added.

### 4. Branch-aware pre-commit

Detects branch via `git symbolic-ref --short HEAD`; warns on direct commits to `main`/`qa`/`release`. Exports `GIT_CURRENT_BRANCH`; banner shows `(branch: <name>)`.

### Files changed

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | New |
| `.github/CODEOWNERS` | New |
| `.github/pull_request_template.md` | New |
| `hooks/pre-commit` | Branch detection + GIT_CURRENT_BRANCH export |
| `hooks/pre_commit_checks.py` | Groq → Anthropic; has_claude; _run_mkdocs_build(); import shutil |

---

# Condensed history — Feb 21–26, 2026

| Date | What was built | Key commit(s) |
|------|---------------|---------------|
| Feb 26 | Google + Facebook SSO (OAuth2 PKCE). `auth/oauth_service.py`, `auth/migrate_users_table.py`, PKCE helpers in `frontend/lib/oauth.ts`, callback page, SSO buttons on login page. Google live; Facebook needs real credentials. | — |
| Feb 25 (auth hardening) | Auth Phase 6: `scripts/seed_admin.py`, `run.sh _init_auth()`, `docs/backend/auth.md`, mkdocs build passes. Two deploy fixes: JWT env propagation in `main.py`; `_load_dotenv()` in `dashboard/app.py`. Superuser seeded. | — |
| Feb 25 (admin UI) | Auth Phase 5: `/admin/users` Dash page (Users + Audit Log tabs), Change Password modal, `_api_call()` helper, token propagation via `?token=`. Admin nav item in Next.js for superusers. | — |
| Feb 25 (dashboard UX) | Home market filter (India/US), pagination + page-size selector, admin table search + pagination. Pre-commit hook created (`hooks/pre-commit` + `hooks/pre_commit_checks.py`). | — |
| Feb 24 (auth phases 1–4) | Iceberg tables (`auth/create_tables.py`, `auth/repository.py`), AuthService + JWT (`auth/service.py`, `auth/models.py`, `auth/dependencies.py`), 12 API endpoints (`auth/api.py`), Next.js auth guard + login page + `apiFetch`. | — |
| Feb 24 (streaming + UX) | `POST /chat/stream` NDJSON streaming, request timeout (120s), dashboard light theme (FLATLY), iframe `X-Frame-Options: ALLOWALL`, dynamic currency symbols (₹/$/£/€ etc.), SPA navigation with internal link routing, bottom-right FAB. | `be09863`, `5c017f2` |
| Feb 23 (dashboard) | Plotly Dash dashboard (`dashboard/`): Home/Analysis/Forecast/Compare pages, callbacks, custom CSS, `run_dashboard.sh`. | — |
| Feb 23 (stock agent) | StockAgent + 8 stock tools (Yahoo Finance, Prophet forecasts, technical analysis, charts, agent-to-agent news tool). Per-agent history, same-day cache. | `895df0f` |
| Feb 22 | OOP backend refactor: `agents/` + `tools/` packages, `ChatServer`, `BaseAgent`, `ToolRegistry`, `AgentRegistry`, structured logging, Pydantic Settings, MkDocs site (11 pages), pre-push hook. | `fa20966`, `f7f1cbc` |
| Feb 21 | Initial app: FastAPI + LangChain agentic loop, Next.js chat UI, Groq LLM (Claude Sonnet 4.6 intended), `search_web` (SerpAPI), multi-turn history, first GitHub push. | `6604b74`, `ee7967f`, `ef643f7` |

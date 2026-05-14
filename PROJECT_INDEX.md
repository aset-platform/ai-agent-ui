# Project Index — AI Agent UI

> AI-agent codebase map. Last refreshed **2026-05-14**.
> Rules + patterns: `CLAUDE.md`. Memory index: `.serena/memories/shared/`.
> Session log: `PROGRESS.md`.

## State (2026-05-14)

`dev` tip: **`fd8993a`**. Sprint 10 active (Abhay). Recent merges:

- **PR #221** (squash `f140fd6`, 2026-05-14) — Intraday backtest correctness (period_end_mtm, MIS daily square-off, intraday timestamps, entry cutoff) + Strategy promotion workflow (draft→paper→live audit + gates + bypass) + Strategies UX overhaul + dry-run no-Kite-creds + walk-forward progress indicator + `disposable_pg_session` helper. (ASETPLTFRM-400)
- **PR #222** (open, branch `feature/intraday-retention-monthly`) — `intraday_bars_retention` switched to monthly partition-aligned cutoff with `scheduler_runs`-detected first-run-of-month gate. ~70 hr/yr reclaimed.
- **PR #220** (squash `bd7dc52`) — Slice 1: historical 15m bars + Nifty 500 keeper.
- **PR #219** (squash `888810d`) — MIS / Intraday Strategy Support (ASETPLTFRM-386).

Pre-Sprint-10 epics merged: Algo Trading v1/v2/v3, Order Safety (367), Live Decouple (374), Pipeline Quality Framework (380), Advanced Analytics (Sprint 9).

## Stack

| Service | Port | Entry | Stack |
|---|---|---|---|
| Backend | 8181 | `backend/main.py` | Python 3.12, FastAPI, LangChain 1.x, SQLAlchemy 2.0 async |
| Frontend | 3000 | `frontend/app/page.tsx` | Next.js 16, React 19 |
| PostgreSQL | 5432 | Docker | pgvector/pg16 (19 OLTP tables) |
| Redis | 6379 | Docker | Redis 7 |
| Docs | 8000 | Docker | MkDocs Material 9 |

Counts: **629** Python · **289** frontend ts/tsx · **295** test files · **21** Alembic migrations · **198** Serena memories (78 architecture · 46 debugging · 26 conventions · 3 onboarding · 1 api).

## Project structure

```
backend/
├── main.py                  FastAPI entry
├── agents/                  LangGraph: 8 configs, 11 nodes, graph.py, sub_agents.py
├── algo/                    Algo trading (Sprint 10): backtest/, paper/, live/, strategy/, broker/, jobs/, runtime/, regime/, attribution/, factors/, sizing/, stream/, universe/
├── tools/                   35 LLM-callable tools
├── jobs/                    Scheduler executors + pipeline chaining (executor.py)
├── pipeline/                CLI data pipeline (runner.py, sources/, jobs/, screener/)
├── insights/                ScreenQL (screen_parser.py — 55 fields, Tables sub-mode)
├── maintenance/             Iceberg backup + compaction + retention
├── db/                      ORM, Alembic migrations (21), engine.py, duckdb_engine.py
├── crypto/byo_secrets.py    Fernet BYO key encryption
├── llm_byo.py, llm_fallback.py, token_budget.py
├── routes.py, ws.py, market_routes.py, dashboard_routes.py, insights_routes.py
└── observability.py

backend/algo/                Algo trading subsystem
├── backtest/                runner.py (period_end_mtm + MIS square-off), positions.py, sim_broker.py, walkforward.py
├── paper/                   PaperRuntime, supervisor.py, risk_engine.py, replay
├── live/                    LiveRuntime, kill_switch, position_hydration
├── strategy/                ast.py, repo.py, promotion.py (gates), mode_repo.py (audit), runtime_state.py
├── runtime/intraday_window  Shared MIS entry-cutoff helper (backtest+paper+live)
├── broker/                  KiteClient (api_key, dry_run), credentials_repo
├── jobs/                    intraday_bars_daily_ingest, intraday_bars_retention, algo_reconciliation, etc.
├── regime/                  HMM classifier, regime_history
├── attribution/             Brinson + factor regression
└── routes/                  strategies.py (CRUD + clone + PATCH /mode + eligibility), backtest.py, walkforward.py, paper.py, live.py

auth/                        JWT + RBAC + OAuth PKCE (general/pro/superuser)
stocks/                      Iceberg repository (5,200+ lines), repository.py, cached_repository.py

frontend/
├── app/(authenticated)/     12+ pages (App Router)
├── components/
│   ├── algo-trading/        StrategiesTab, PromoteModal, BacktestTradeTable, WalkForwardSubTab, builder/
│   ├── admin/               PipelineDAG, RecommendationsTab, SchedulerTab
│   ├── insights/            ColumnSelector, InsightsTable, ScreenQL
│   ├── advanced-analytics/  SwingMethodologyPanel, AA report tabs
│   └── charts/, widgets/, dashboard/
├── hooks/                   19+ SWR data hooks (useStrategies, useBacktestRuns, useSchedulerData...)
├── providers/               Chat, Layout, PortfolioActions contexts
└── lib/                     types, config, apiFetch, downloadCsv

e2e/                         Playwright: 51 specs, 17 page objects, fixtures, selectors registry
tests/                       97 pytest files (~839 tests)
scripts/                     28+ data/migration/seed scripts
docs/                        56+ MkDocs Material pages
```

## Database

**PostgreSQL (19 OLTP tables)**: users, user_tickers, payments, registry, scheduled_jobs, scheduler_runs, recommendation_runs/recommendations/recommendation_outcomes, market_indices, user_memories (pgvector 768-dim), conversation_contexts, stock_master, stock_tags, ingestion_cursor, ingestion_skipped, sentiment_dormant, pipelines, pipeline_steps.

**Algo PG tables** (in `algo` schema): strategies, runs (backtest/walkforward/live), positions, broker_credentials, live_caps, strategy_metadata, **strategy_mode_transitions** (audit, added 2026-05-14), drift_state.

**Iceberg (16+ tables)**: `stocks.*` — ohlcv (1.5M), company_info, dividends, quarterly_results, analysis_summary, forecast_runs, forecasts, piotroski_scores, sentiment_scores, nse_delivery, fundamentals_snapshot, corporate_events, promoter_holdings, **intraday_bars** (~11M 15m rows post-cleanup), llm_pricing, llm_usage, portfolio_transactions, regime_history, daily_factors. `algo.events` (algo-trading event log), `algo.intraday_bars` (live stream).

**Rule**: mutable → PG; append-only → Iceberg. DuckDB for ALL Iceberg reads (metadata cache, `invalidate_metadata()` after every write).

**Maintenance**: `backend/maintenance/iceberg_maintenance.py` — backup (rsync, 1800s timeout) → compact (overwrite → 1 file/partition) → `cleanup_orphans_v2` (snapshot expiry + orphan reclaim). Backup dir `~/Documents/projects/ai-agent-ui-backups/`.

## Entry points

| Entry | Path | Notes |
|---|---|---|
| Backend API | `backend/main.py` | FastAPI |
| Frontend SPA | `frontend/app/page.tsx` | Next.js 16 |
| Pipeline CLI | `backend/pipeline/runner.py` | 19 commands |
| Scheduler daemon | `backend/jobs/scheduler_service.py` | Started in `main.py` lifespan |
| Intraday backfill CLI | `backend/algo/backtest/intraday_backfill.py` | nifty500 / india_top200 / india_full |
| Docs | `docs/` | MkDocs |

## Key modules (compact)

| Module | Purpose |
|---|---|
| `backend/agents/sub_agents.py` | Sub-agent tool-calling loop factory |
| `backend/agents/conversation_context.py` | PG-persisted multi-turn context |
| `backend/llm_fallback.py` | N-tier cascade (Groq → OSS → Anthropic → Ollama) |
| `backend/llm_byo.py` | BYOContext + ContextVar + resolve_byo_for_chat |
| `backend/token_budget.py` | Per-model TPM/RPM/TPD/RPD sliding windows |
| `backend/observability.py` | LLM usage collector + Iceberg flush |
| `backend/insights/screen_parser.py` | ScreenQL parser/SQL-gen (55 fields, LIKE op, Tables) |
| `backend/jobs/executor.py` | 25+ `@register_job` executors + pipeline_executor |
| `backend/jobs/recommendation_engine.py` | Monthly-per-scope IST quota |
| `backend/maintenance/iceberg_maintenance.py` | Compact + orphan-sweep |
| `backend/maintenance/backup.py` | rsync + catalog.db + per-table backup |
| `backend/market_utils.py` | `detect_market`, `safe_str`, NaN-safe helpers |
| `backend/db/engine.py` | `get_session_factory` (cached) + **`disposable_pg_session`** (per-call NullPool, scheduler-job safe) |
| `backend/db/duckdb_engine.py` | Iceberg read engine + metadata cache + `query_iceberg_table` |
| `backend/algo/strategy/promotion.py` | Mode-transition gates (backtest+walkforward freshness, paper-fill events) |
| `backend/algo/backtest/runner.py` | period_end_mtm, MIS daily square-off honoring `square_off_time`, intraday timestamps |
| `backend/algo/runtime/intraday_window.py` | Shared `is_entry_allowed`/`is_past_square_off` for backtest+paper+live |
| `backend/algo/broker/kite_client.py` | KiteClient (dry_run pinned at construction, no Kite REST when dry_run=True) |
| `auth/dependencies.py` | `superuser_only`, `pro_or_superuser`, `require_role()` |
| `auth/repo/user_writes.py` | Tier→role auto-sync pinch point |
| `auth/repo/byo_repo.py` | BYO key CRUD + provider validators |
| `frontend/hooks/useStrategies.ts` | Strategies list + mode/promote/clone API + `filterStrategiesByMode` |
| `frontend/components/algo-trading/StrategiesTab.tsx` | Search + status filter + pagination + icon actions + Promote modal |
| `frontend/components/algo-trading/PromoteModal.tsx` | Per-target eligibility cards + bypass card (typed-name confirm) |
| `frontend/components/admin/PipelineDAG.tsx` | Pipelines DAG, prefers `step.job_name` over hardcoded labels |

## Promotion workflow (algo strategies)

Lifecycle `draft → paper → live`. Audit table `algo.strategy_mode_transitions`.

Gates: draft→paper needs fresh backtest + walkforward (`started_at >= strategies.updated_at`); paper→live needs fresh paper-fill events in `algo.events`.

Auto-demote on AST edit. Bypass to live unlocked after first `to_mode='live'` in history.

Picker filters (mode-strict): Backtest = all · Paper = paper · Dry-run = paper · Live = live.

→ `shared/architecture/strategy-promotion-workflow`.

## Pipelines

| Pipeline | Cron | Steps |
|---|---|---|
| `daily_india` | 15:30 IST mon-fri | data_refresh · regime_classifier · compute_daily_factors · run_sentiment · run_forecasts · iceberg_maintenance · recommendations |
| `Intraday Bars Daily Pipeline` | 15:45 IST mon-fri | intraday_bars_daily_ingest · intraday_bars_retention (monthly gate after PR #222) · iceberg_maintenance |
| `recommendation_cleanup` | 03:00 IST daily | 14-month retention purge |

Scheduler-job async PG access MUST use `disposable_pg_session()` (not `get_session_factory()`) — cached engine binds to uvicorn loop and crashes under `asyncio.run` from scheduled jobs. → `shared/debugging/disposable-pg-session-asyncio-loop-bug`.

## Quick start

```bash
./run.sh start                                  # all services
docker compose build backend && docker compose up -d  # rebuild
PYTHONPATH=. alembic upgrade head               # migrations
python -m pytest tests/ -v                      # 839 tests
cd frontend && npx vitest run                   # 18+ unit tests
cd e2e && npx playwright test --project=frontend-chromium  # ~3 min
```

## Memory categories (`.serena/memories/shared/`)

- `architecture/` (78) — system design, e.g. `strategy-promotion-workflow`, `algo-trading-system`, `mis-intraday-strategy-support`, `backtest-correctness-mis-cnc-suite`, `intraday-retention-monthly-cadence`
- `debugging/` (46) — root-cause memories, e.g. `disposable-pg-session-asyncio-loop-bug`, `iceberg-compact-duckdb-stale-read`, `cookie-hostname-mismatch`
- `conventions/` (26) — patterns, e.g. `swr-data-fetch-pattern`, `tabular-page-pattern`, `redis-cache-layer`
- `onboarding/` (3) — first-session bootstrapping
- `api/` (1)

Browse: `list_memories` via Serena MCP. Reference inline in code/docs as `→ memory-name`.

## See also

- `CLAUDE.md` — hard rules + patterns (9 sections, Pattern Index at §9)
- `PROGRESS.md` — dated session log
- `docs/algo-trading/` — algo subsystem guides
- `docs/backend/` — backend ops + maintenance + BYOM + recommendations + Iceberg orphan sweep
- `PROJECT_INDEX.json` — machine-readable counterpart

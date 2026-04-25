# CHANGELOG

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.13.0] — 2026-04-25: Sprint 8 — LCP <2s push, RSC + cookie auth, Iceberg disk reclaim, CLAUDE.md restructure

Sprint 8 **closed at 62/62 SP (100%)**, 14 tickets Done, 6 calendar days early (sprint ends 2026-05-01). 30 commits on `feature/sprint8`, all pushed.

### Added

- **`cleanup_orphans_v2()` — safe Iceberg orphan-parquet sweep** (ASETPLTFRM-338, commits `b9869a1` + `369ee4f` + `a68c9eb`). 9-step algorithm in `backend/maintenance/iceberg_maintenance.py` using PyIceberg 0.11.1 native `tbl.maintenance.expire_snapshots().by_ids(...).commit()` + `inspect.all_files()` + `inspect.all_manifests()` + per-snapshot `manifest_list` (load-bearing — see Bug found below) + catalog-pointer paranoid assertion + 30-min mtime grace + read-verify. First full sweep across the 4 hot tables: **12.4 GB reclaimed** (16 GB → 3.6 GB warehouse, −78%); 91 856 → 3361 files; 11 299 stale snapshots expired. 17 unit tests in `tests/backend/test_iceberg_orphan_sweep.py` covering all 5 load-bearing properties + helpers + the regression case. Endpoint p95 sub-5 ms after each table. Same-day consolidated into `iceberg_maintenance` (the standalone weekly `iceberg_orphan_sweep` job created earlier in the day was removed; orphan sweep now runs immediately after `compact_table()` per hot table inside the daily maintenance chain — single backup, single dashboard entry). Surfaced in Admin → Scheduler as the **Iceberg Maintenance** tile (amber `ZapIcon`, sub: "Compact + orphan sweep"). Full prose in `docs/backend/iceberg-orphan-sweep.md`.
- **CLAUDE.md as enforceable dev-rule layer** (commit `c5aef64`) — restructured from 1208 → ~580 lines focused on actionable hard rules + development patterns + bug-fix patterns. Six new shared Serena memories created so CLAUDE.md can act as a navigation layer with inline `→ memory-name` references (59 unique memories surfaced, 57-row Pattern Index): `shared/conventions/tabular-page-pattern`, `shared/conventions/swr-data-fetch-pattern`, `shared/conventions/modal-stacking-pattern`, `shared/conventions/backend-restart-triggers`, `shared/conventions/jira-3phase-lifecycle`, `shared/architecture/db-table-inventory`. Four new ★ design-pattern sections (tabular pages, stale-data chip, modal stacking, scope-aware admin) + three ★ cross-cutting sections (caching, e2e, performance) make conventions enforceable instead of implicit.
- **React Server Component pattern** for authenticated routes (ASETPLTFRM-334 phase A) — four-piece architecture: HttpOnly `access_token` cookie on `/v1/auth/login` (additive, JSON body unchanged) → `frontend/proxy.ts` (Next.js 16 rename of `middleware.ts`) checks cookie *presence* and gates protected routes → `frontend/lib/serverApi.ts` reads the cookie via `next/headers` and forwards as Bearer to the backend → `app/(authenticated)/dashboard/page.tsx` is now a Server Component that pre-fetches `/v1/dashboard/home` and seeds the result as `initialData` to the existing client tree (`DashboardClient.tsx`), which forwards it to SWR's `fallbackData`. First render paints with real data, no skeleton step. Pattern documented in `docs/frontend/ssr-patterns.md` (290 lines).
- **Containerized Lighthouse 34-route audit** (ASETPLTFRM-330): `Dockerfile.perf` + `frontend-perf` service in compose `perf` profile. Playwright login → 9 base + 25 tab variants per run. `pw-lh-summary.json` aggregate output. ESM-only Lighthouse 12 dynamic-imported, page-rotation every 12 audits, `crypto.randomUUID` polyfill for the insecure-context container origin. Run: `docker compose --profile perf run --rm perf`. Docs: `docs/frontend/perf-audit.md`.
- **`<Suspense>` boundaries** around `ForecastChart` + `PortfolioForecastChart` (ASETPLTFRM-334 phase B) — chart hydration cost no longer blocks route hydration. The `?tab=portfolio` and `?tab=portfolio-forecast` tabs hit **1515 ms LCP** (down from 3500 ms).
- **Atomic Redis counter primitives** on `CacheService` (ASETPLTFRM-327): `incr(key, by=1, ttl=None)` and `decr(key)` via Redis pipeline `INCRBY` + `EXPIRE` as one round-trip. Replaces the GET → check → SET race in `_check_and_increment_byo_counter`. New `test_parallel_requests_never_exceed_limit` runs 200 concurrent asyncio tasks against limit=50 — exactly 50 successes, 150 × 429, final counter == 50.
- **Backup `completed_at` field** (ASETPLTFRM-337) — `list_backups()` now stamps each entry with the directory's mtime as ISO 8601 UTC (`Z` suffix), and the admin endpoints surface it. `BackupHealthPanel.tsx` shows the IST-formatted absolute time as a tooltip on the relative-age string. Replaces the prior "hours since folder-name midnight in container TZ" age calculation that produced "9h ago" for an 18-minute-old backup.
- **TickerForecast `latest_close` field** (ASETPLTFRM-335) — backend's `/v1/dashboard/forecasts/summary` now includes today's live close (batched DuckDB `QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) = 1` query, single round-trip per request). Forecast widget displays it as the primary "Current Price" with the forecast-time anchor footnoted only when they differ.
- **`<link rel="preconnect">` + `dns-prefetch`** for the backend URL in `frontend/app/layout.tsx` (ASETPLTFRM-334 phase E) — saves ~100-200 ms TLS handshake on the first authenticated route load.
- **`SimpleBarChart`** ECharts component (ASETPLTFRM-331): replaces `plotly.js-basic-dist` (1 MB) on Insights `?tab=sectors` (LCP 8523 → 4622 ms, −46%) and `?tab=quarterly` (8593 → 3486 ms, −59%).
- **Bundle analysis** (ASETPLTFRM-331): `docs/frontend/bundle-analysis.md` documents top contributors and per-route LCP before/after metrics. Re-audit results from ASETPLTFRM-334 H appended.
- **`docs/frontend/ssr-patterns.md`** (ASETPLTFRM-334 phase G) — Client vs Server Component decision tree, cookie-auth flow, edge proxy, `<Suspense>` placement, preconnect guidelines, PPR ramp. Reference table of all 9 phase commits for traceability.
- **PEG ratio 3-variant** in Screener + ScreenQL (ASETPLTFRM-332): trailing PEG (`pe_ratio / earnings_growth`), yfinance raw PEG, quarterly PEG. Three new fields. Merged via PR #123.
- **Per-user column selector** on Screener + ScreenQL (ASETPLTFRM-333): user-pickable display columns across 39 Screener fields + 37 ScreenQL fields. `useColumnSelection` hook persists choice in `localStorage`.

### Changed

- **`execute_iceberg_maintenance` consolidated** (commit `a68c9eb`) — now calls `cleanup_orphans_v2(tbl, skip_backup=True)` per hot table immediately after `compact_table(tbl)`, sharing the single fail-closed backup taken at step 0. Legacy no-op `expire_snapshots()` + `cleanup_orphans()` calls in the maintenance loop removed. Standalone `execute_iceberg_orphan_sweep` deleted (158 lines) along with its `public.scheduled_jobs` row. Single backend job type, single dashboard entry, daily execution as part of both India + USA pipeline chains.
- **CLAUDE.md Rule 20 amended**: still NEVER `rm` Iceberg metadata/parquet directly; the sanctioned reclamation path is `cleanup_orphans_v2()`. Pattern Index row added for "Reclaim Iceberg disk".
- **`PipelineDAG.tsx` `JOB_LABELS`** (commit `875bf90`) — added `recommendations`, `recommendation_outcomes`, `iceberg_maintenance` entries. Pipeline DAG steps 5+6 now show pretty labels ("Outcome Tracker", "Iceberg Maintenance") instead of falling through `?? step.job_type` to raw underscored identifiers.
- **`SchedulerTab.tsx`** — added "Iceberg Maintenance" tile to job-type picker (amber `ZapIcon`), filter dropdown option, `typeLabelMap` entry.
- **`/dashboard/home` parallelizes its 4 sub-calls** via `asyncio.gather()` (ASETPLTFRM-334 phase D). Cold-cache cost is now bounded by `max(...)` not `sum(...)`. Wrapper TTL dropped from `VOLATILE` (60 s) to a new `TTL_HERO` (10 s).
- **Admin endpoint caching** (ASETPLTFRM-334 phase C): `/admin/usage-stats` now caches in Redis for 30 s (was uncached, 500-1500 ms cold). `/admin/audit-log` tightened from 60 s → 30 s for consistency. Verified: usage-stats 100 ms cold → 52 ms warm; audit-log 2000 ms cold → **2.5 ms warm**.
- **`MarkdownContent` lazy-loaded** in `MessageBubble` (ASETPLTFRM-334 phase C) — defers the ~105 KB `react-markdown` + `remark-gfm` chunk from the initial dashboard bundle.
- **Admin tab content `min-h`** bumped 400 → 600 px (ASETPLTFRM-334 phase C) — keeps CLS ≤ 0.02 when rows fill in.
- **`drop_dead_tables` hardened** (ASETPLTFRM-328): now runs `run_backup()` fail-closed at function entry, gates per-table `shutil.rmtree` on the catalog drop succeeding, treats `NoSuchTableError` as idempotent. Three new tests covering backup-fail abort, partial-failure dir preservation, and idempotent re-run.
- **ScreenQL CTE market derivation** (ASETPLTFRM-326): now reads `company_info.exchange` (Yahoo's internal codes — `NSI` for NSE, `BSE`, etc.) with a `.NS`/`.BO` suffix fallback for the 13 NULL-exchange rows + Indian indices fallback for `^NSEI`/`^BSESN`/`^INDIAVIX`. Validated 866 india + 15 us, 0 misclassifications on live data. Six new tests covering each CASE branch.
- **`apiFetch` for `/v1/insights/screen/fields`** in ScreenQL tab (ASETPLTFRM-325) — was bare `fetch()`, now uses the apiFetch wrapper for consistency. CLAUDE.md Rule 14 conformance.
- **`next.config.ts`** — `cacheComponents: false` flag scaffolded (Next.js 16 renamed `experimental.ppr` to top-level `cacheComponents`). Currently off pending Suspense audit of remaining `new Date()`/`useTheme()` Client Components in `/dashboard`, `/analytics`, `/admin`. Activation gated by Sprint-9 candidate TBD-D.
- **`AuthenticatedLayout` SSR unlock** (ASETPLTFRM-331, commit `8c2d1b8`) — removed the `mounted` state gate that returned a loading shell early. Single-line change dropped **10 routes** under the 2 s LCP target. Documented as `shared/architecture/auth-layout-ssr-unlock`.
- **FCP floor collapse** (ASETPLTFRM-331) — SSR fallback in `(authenticated)/layout.tsx` was a pure-CSS border-spinner with no text/image, so Lighthouse's FCP heuristic ignored it. Replaced with a sidebar-shaped skeleton + brand text + "Loading…" label. FCP now uniform ~1515 ms across every authenticated route (was ~3450 ms — −56%).
- **`StockChart` type-leak fix** — `analytics/analysis/page.tsx` imported a runtime const (`DEFAULT_INDICATORS`) from `StockChart.tsx`, dragging `lightweight-charts` (150 KB) into the initial bundle even though `StockChart` was already `dynamic`. Split types + constant into new `StockChart.types.ts`. Initial chunk: 292 KB → 127 KB.

### Fixed

- **`snapshot.manifest_list` orphan-sweep bug** (ASETPLTFRM-338, found in flight on the first live sweep of `analysis_summary`). `inspect.all_manifests()` returns DATA manifests (`{uuid}-m0.avro`) but NOT per-snapshot manifest LIST files (`snap-{snapshot_id}-{seq}-{uuid}.avro`). The latter is referenced by `snapshot.manifest_list` and is the FIRST file `tbl.scan()` opens for the current snapshot. First sweep deleted 7944 files / 964 MB but `verified=False` — table unreadable until backup restore (~30s rsync). Fix: explicit loop adding `snap.manifest_list` for every retained snapshot. Locked by `test_snapshot_manifest_list_files_kept_in_referenced` regression test. Documented in `docs/backend/iceberg-orphan-sweep.md` + `shared/architecture/iceberg-orphan-sweep-design`.
- **NIFTYBEES.NS 22-Apr-2026 OHLCV gap** (ASETPLTFRM-336): the scheduled bulk fetch missed it; the delta-fetch cursor advanced past 22-Apr because yfinance returned 23-Apr as the "latest." Direct `yf.Ticker().history(start='2026-04-22', end='2026-04-23')` confirmed the vendor *does* have 22-Apr data — inserted via `repo.insert_ohlcv()`. Coverage now 817/817 tickers, 0 NaN. Operational fix only; the systemic delta-fetch gap-detection is filed as a Sprint-9 candidate.
- **Backup health "9h ago" for an 18-min-old backup** (ASETPLTFRM-337) — see `completed_at` Added entry.
- **Forecast widget "Current Price"** showing forecast-run snapshot (₹817 from 9 days ago) labelled as live (ASETPLTFRM-335) — backend now also returns `latest_close`; widget prefers it.
- **Infinite redirect loop on legacy sessions** (ASETPLTFRM-334 hotfix `e33172d`) — Phase A.2 proxy.ts checked only the new `access_token` cookie, but pre-A.1 sessions only had the `refresh_token` cookie + a localStorage access token. Loop: `/dashboard` → proxy: no access_token → `/login`; `/login` → React reads localStorage → `/dashboard`; repeat. Fix: proxy now treats *either* `access_token` OR `refresh_token` cookie as authenticated. The first authenticated XHR refreshes and lands the new access_token cookie automatically.
- **TS build-break fixes** (ASETPLTFRM-329, 6 files): unblocks `next build` across all environments.

### Removed

- **Three dead Iceberg tables** dropped from the catalog (commit `c0447dc`): `stocks.scheduler_runs` and `stocks.scheduled_jobs` were migrated to PostgreSQL in Sprint 4 — their Iceberg shells were catalog-only. `stocks.technical_indicators` was scaffolded for persisted RSI/MACD/SMA but the design moved to compute-on-demand from OHLCV via `backend/tools/_analysis_indicators.py` — empty Iceberg table with 86 metadata.json files accumulated from snapshot bookkeeping. Drop ran via the ASETPLTFRM-328-hardened `drop_dead_tables()` (backup-before, per-table gating, idempotent re-run safe). PG `public.scheduler_runs` (104 kB) and `public.scheduled_jobs` (8 kB) untouched. Active Iceberg tables: 19 → 16.
- **Iceberg `technical_indicators` creation block** removed from `stocks/create_tables.py` so `_ensure_iceberg_tables()` no longer resurrects it on backend startup. The `_technical_indicators_schema()` helper is left in place for the unlikely scenario of reviving persisted indicators.
- **Legacy `middleware.ts`** at `frontend/middleware.ts` — Next.js 16 deprecated the `middleware` file convention in favour of `proxy.ts`. Functionality preserved + extended (cookie-auth gate added).

### Acceptance metric for ASETPLTFRM-334

The 13 SP scope explicitly migrated only the dashboard hero to RSC. Re-audit (containerized Lighthouse, 34/34 routes, 2026-04-25):

| Metric | Target | Result | Status |
|---|---|---|:---:|
| LCP < 2 000 ms | 34/34 | **10/34** | partial |
| FCP ≤ 1 500 ms | 34/34 | **32/34** | mostly ✓ |
| CLS ≤ 0.02 | 34/34 | **28/34** | partial |
| TBT ≤ 200 ms | 34/34 | **34/34** | ✅ |

10 routes hitting LCP target are all tabular pages where `FCP === LCP`. Chart-heavy and admin routes still 4-7 s — same root cause: chart hydration is the LCP element, not the hero. Five Sprint-9 follow-up candidates (TBD-A through E) itemized in `docs/frontend/bundle-analysis.md`: chart-route RSC, admin-tab RSC, sector widget RSC on `/dashboard`, `cacheComponents` activation, Prophet forecast chart re-implementation.

### Sprint 9 carry-over

- **Chart-route + admin-tab RSC migration** — Sprint 8's RSC pattern (cookie-auth + `serverApiOrNull` + SWR `fallbackData`) covered only the dashboard hero. 24 chart-heavy / admin routes still 4-7 s LCP because chart hydration is the LCP element. Five candidates (TBD-A through TBD-E) itemized in `docs/frontend/bundle-analysis.md`.
- **Activate `cacheComponents: true`** once the Suspense audit of remaining `new Date()` / `useTheme()` Client Components in `/dashboard`, `/analytics`, `/admin` passes (TBD-D).
- **Systemic delta-fetch gap detection** — the NIFTYBEES gap pattern (ASETPLTFRM-336) is fixable systematically. Filed as a Sprint 9 candidate.

---

## [0.12.0] — 2026-04-23: Sprint 7 Closure — Sentiment Hardening, Iceberg Pipeline Integration, Portfolio Transparency

Sprint 7 closed at **75/75 SP (100%)**. ASETPLTFRM-324 (BYOM) + ASETPLTFRM-323 (Pro role) marked Done. ~30 SP of follow-up work landed as comments on parent tickets (320, 315, 316, 319).

### Added

- **Sentiment dormancy** (`backend/db/models/sentiment_dormant.py` + Alembic `a9c1b3d5e7f2`): per-ticker dormancy table tracks tickers returning 0 headlines K times. Capped exponential cooldown (2/4/8/16/30 days). Excluded from learning/cold buckets in the daily sentiment batch; 5% probe re-tested by oldest `last_checked_at` so newly-trending tickers self-recover. ~60% reduction in daily Yahoo/Google HTTP calls. `force=True` runs ignore dormancy. Mirrors `ingestion_skipped` pattern. PG helpers in `backend/db/pg_stocks.py`.
- **Daily Iceberg compaction in pipeline** (`iceberg_maintenance` job_type): new step 6 of both `India Daily Pipeline` and `USA Daily Pipeline`. Compacts hot tables (`stocks.{ohlcv, sentiment_scores, company_info, analysis_summary}`) so OHLCV file count never grows unbounded again. Best-effort `expire_snapshots` + `cleanup_orphans` after each table.
- **Auto-backup before compaction** (preserves CLAUDE.md hard rule): `run_backup()` runs as **step 0** of `execute_iceberg_maintenance`. Fail-closed — if backup fails, compaction aborts. `rsync` added to `Dockerfile.backend` runtime stage so the container can run rsync (was host-only before).
- **Portfolio P&L stale-ticker chip** (`PortfolioPerformanceResponse.stale_tickers: list[StalePriceTicker]` + `PLTrendWidget::StaleTickerChip`): amber chip near panel title when held tickers' last valid close is older than the series end. Hover tooltip lists each ticker with its `last_valid_close_date` and days-stale. Auto-clears when list empty.
- **News & Sentiment unanalyzed chip** (`PortfolioNewsResponse.unanalyzed_tickers: list[str]` + `NewsWidget::UnanalyzedChip`): amber chip when portfolio sentiment aggregate is dominated by `market_fallback`/`none` proxy scores. Same UX pattern as the stale-price chip.
- **News widget 21-day max-age filter** on `/portfolio/news` — drops articles older than 21 days (mid/small caps were surfacing 60-100d-old items with no decisioning value).
- **Yahoo `^BSESN` stale-feed → Google Finance fallback** (`backend/market_routes.py`): Yahoo's BSE feed periodically freezes mid-session. Detect via `regularMarketTime` age (>300s during market hours), fall back to Google Finance scrape (`SENSEX:INDEXBOM`, `data-last-price` regex). Overlay live price on Yahoo's intraday-stable `prev_close`.
- **View-transactions modal on Portfolio tab**: eye icon replaces inline edit pencil. New `GET /v1/users/me/portfolio/{ticker}/transactions` endpoint returns date-sorted txns + summary (total qty, avg price, current price, gain/loss). Per-row edit pencil opens `EditStockModal` scoped to that specific txn. New `PortfolioActionsProvider.openTransactions(ticker)` context method.
- **CLAUDE.md gotcha**: uvicorn `--reload` doesn't re-register routes / new Pydantic fields — `docker compose restart backend` needed.

### Changed

- **Container `TZ=Asia/Kolkata`** in `docker-compose.yml` backend service (was UTC). The `schedule` library uses local time — cron strings like `"08:00"` were firing at 08:00 UTC = 13:30 IST (5.5h late). Now matches scheduled IST times verbatim.
- **`scheduler_catchup_enabled=False`** default in `backend/config.py` (was `True`). Startup catchup of "missed" jobs was silently pulling mid-day partial data on every restart. Opt-in via `SCHEDULER_CATCHUP_ENABLED=true` env if needed.
- **OHLCV upsert is now NaN-replaceable** (both `insert_ohlcv` + `batch_data_refresh`): existing-keys query filters `WHERE close IS NOT NULL AND NOT isnan(close)`, plus scoped pre-delete of NaN rows for the to-be-inserted `(ticker, date)` set before append. Without this, a stuck NaN-close row blocked future Yahoo-late-close re-fetches forever as "duplicate."
- **Sentiment Step-5 freshness re-query uses PyIceberg directly** (was DuckDB `query_iceberg_df`): under concurrent commits, DuckDB reads a metadata file via filesystem `glob` whose manifests aren't yet visible — returned empty, causing 802/802 market_fallback to overwrite finbert rows. Switched to `tbl.refresh().scan(EqualTo(score_date, today))` (SQLite catalog atomic per commit).
- **Sentiment Step-5 delete is source-aware**: predicate adds `In("source", ["market_fallback", "none"])` so force-runs cannot clobber finbert/llm rows.
- **Sentiment hot-classifier source filter** updated to `IN ('finbert', 'llm')` (was `'llm'`-only — stale post-FinBERT cutover; bucket was always empty).
- **Sentiment `market_cap` selector** now joins `stocks.company_info.market_cap` for the top-50 learning batch (was sorted alphabetically because `get_all_registry()` doesn't expose `market_cap` → picked obscure A-prefixed small-caps).
- **Sentiment workers 15 → 5** in `ThreadPoolExecutor` — Yahoo/Google rate-limit above ~5 parallel. Combined with dormancy, total HTTP calls drop ~60%; throughput unchanged.
- **`PortfolioActionsProvider`** extended with `openTransactions(ticker)`. Inline edit pencil REMOVED from portfolio rows (view-first-edit-from-within UX).
- **Sprint 7 status**: ASETPLTFRM-324 (BYOM, 13 SP) and ASETPLTFRM-323 (Pro role, 8 SP) transitioned In Progress → Done.

### Fixed

- **Portfolio P&L NaN-truncation** (`_build_portfolio_performance` in `backend/dashboard_routes.py`): used to drop entire dates when any held ticker had NaN close (`val += qty × NaN` → `val > 0` False → date skipped). Different users saw different "latest" dates depending on which ETFs they held. Four defenses landed:
  1. `math.isnan` guard in daily-aggregate loop
  2. per-ticker `df["close"].ffill()` before building close_maps
  3. `stale_tickers` field + amber chip on the P&L panel
  4. ffill-to-series-end (extend each ticker's close_map forward from last known close to series end) — fixes the dip after `Clean NaN Rows` admin action.
- **OHLCV chart duplicate-timestamp assertion** (`Assertion failed: data must be asc ordered by time, ... time=X, prev time=X`): three defensive layers added — Iceberg (NaN-replaceable upsert), backend route (`drop_duplicates(subset=["date"])` before serializing), frontend chart (`Map`-keyed by time before `setData`). Lightweight-charts asserts on duplicate timestamps; any single layer regressing won't crash the chart.
- **Backup Health panel renders empty** (fixes ASETPLTFRM-316): `_admin_backups_list` was crashing with `ValueError: Invalid isoformat string: '2026-04-22-pre-dedupe'` because it parsed the directory-name suffix as ISO date. Fix: try `datetime.fromisoformat(b["date"][:10])` first, fall back to dir mtime. Custom-named backups now coexist safely with scheduled `backup-YYYY-MM-DD` ones.
- **FinBERT cache stalled mid-download**: HF XET CDN reproducibly cut `pytorch_model.bin` transfer at ~67 MB. Cleanup `.incomplete` artifacts + re-download via `huggingface_hub.snapshot_download(allow_patterns=...)` skips the safetensors race. ~15s clean re-download.
- **OHLCV file fragmentation** observed at 16,156 parquet files for `stocks.ohlcv` (was 817 after the original ASETPLTFRM-315 compaction; grew unbounded from per-ticker daily writes). `Clean NaN Rows` button took 5+ min. Daily auto-compaction prevents recurrence; post-fix full-count of 1.5M rows runs in 0.50s (~18× faster than fragmented state).

### Migrations

- `a9c1b3d5e7f2` — `add_sentiment_dormant` table (PG).

---

## [0.11.0] — 2026-04-19: Bring-Your-Own-Model + Insights Three-Tier Scoping + Hallucination Guards (Sprint 7)

### Added

- **BYOM — Bring Your Own Model** (ASETPLTFRM-324, Phase A + B): chat-agent LLM costs shift from *platform-pays-all* to *platform-pays-first-10-then-BYO*. Every non-superuser gets **10 lifetime free chat turns**; after that they must configure their own Groq and/or Anthropic key or chat is blocked with `HTTP 429`. Non-chat flows (recommendations, sentiment, forecast) and superusers keep using platform keys. Ollama stays shared/native — free for all when available.
  - New Alembic `f8e7d6c5b4a3`: `users.chat_request_count`, `users.byo_monthly_limit`, new `user_llm_keys` table (Fernet-encrypted).
  - `backend/crypto/byo_secrets.py` — Fernet wrapper; master key in `BYO_SECRET_KEY` env.
  - `auth/endpoints/byo_routes.py` — 4 self-scoped CRUD endpoints under `/v1/users/me/llm-keys` + `/v1/users/me/byo-settings`.
  - `backend/llm_byo.py` — `BYOContext` + `ContextVar` + `resolve_byo_for_chat()` + Redis monthly counter `byo:month_counter:{user_id}:{yyyy-mm}` (IST, 40-day TTL) + per-user LangChain client cache.
  - `FallbackLLM._try_model` and Anthropic fallback both consult `get_active_byo_context()` and swap in user-keyed clients when BYO active. `llm_classifier` (Tier-2 intent) also BYO-aware. Stamps `key_source="user"` on Iceberg `llm_usage`.
  - `MyLLMUsageTab.tsx` redesign: free-allowance card with inline `BYOLimitEditor`, 3 provider cards (Groq + Anthropic configurable, Ollama native), 4 KPIs with free/user split subtitle, usage-by-model table with **Free · Your keys** badge column, 30-day sparkline. New `ConfigureProviderKeyModal` + `ConfirmDialog`-gated delete.
  - Iceberg schema evolution: nullable `key_source` column on `stocks.llm_usage`. No backfill; legacy null rows treated as `platform` at read time.
  - Scope-self `/admin/metrics` response enriched with `quota`, `providers`, `daily_trend`, per-user per-model rollup (tokens, cost, `last_used_at` in UTC-Z, platform/user request split).
  - Docs: `docs/backend/byom.md` with end-to-end workflow diagram, architecture, endpoints, local setup.
- **Insights three-tier ticker scoping**: `_scoped_tickers(user, scope)` helper drives 9 tabs across three tiers — `discovery` (Screener + ScreenQL + Sectors + Piotroski), `watchlist` (Risk + Targets + Dividends), `portfolio` (Correlation + Quarterly). Pro / superuser see the full stock+ETF universe on discovery tabs; general users see watchlist ∪ holdings. Portfolio-tier tabs always scope to current holdings only.

### Changed

- **`MessageCompressor` defaults**: `max_tool_result_chars` 800 → 4000 so typical portfolio/screener tables no longer clip mid-row. Progressive passes 500 → 2500 (pass 2) and 300 → 1500 (pass 3).
- **`chat_request_count` bump rule**: skipped when turn routes through BYO so the free-allowance counter stays pinned at 10 once the user is paying their own bill. Scope-self response clamps `free_allowance_used = min(count, 10)` for historical drift.
- **`get_dashboard_llm_usage` per-model rollup** now returns `input_tokens`, `output_tokens`, `last_used_at` (UTC-Z), `requests_platform`, `requests_user`. Filters `event_type == "request"` so cascade/compression bookkeeping rows (model=`n/a`) don't surface on the Usage by Model table.
- **Synthesis + portfolio sub-agent prompts** gained a `NO HALLUCINATION ON TRUNCATION` clause: when a tool message ends with `[truncated N chars]`, list only visible rows and explicitly tell the user some rows were trimmed — never fabricate.

### Fixed

- **Tool-truncation hallucination** (found via "You have 8 stocks … [Truncated in display, but confirmed in memory context]"): that phrase was pure LLM invention, not a system marker. 800-char cap was chopping the 8-row portfolio table mid-row; LLM fabricated the rest. Three-layer defense shipped (raise cap + prompt guardrail in synthesis + portfolio agent).
- **NaN sentinel string leak in `safe_str` / `safe_sector`**: literal `"NaN"`, `"None"`, `"null"`, `"N/A"`, `"NaT"` tokens slipped past the existing NaN/whitespace guards and ended up in LLM recommendation prompts (*"large weight of NaN (41.8%)"*) and Sectors-tab groupby keys. Added `_MISSING_SENTINELS` frozenset with case-insensitive post-strip check. Legit substrings like `"Naniwa"` or `"Financial Services"` still pass.
- **Naïve-UTC timestamps in frontend**: Iceberg `timestamp` is `datetime64[us]` (no tz); `str(ts)` emitted a bare ISO string that the browser's `new Date()` parsed as local (IST = UTC+5:30), showing fresh rows as "5h ago". New `_iso_utc()` helper in `backend/routes.py` + UTC-Z coercion in `get_dashboard_llm_usage` per-model aggregator.
- **WebSocket 429 delivery**: `_handle_chat` used to return an `event_queue` after enqueueing an error — but the drain loop hadn't started yet, so the client spun "Thinking…" forever. Now sends errors directly via `ws.send_json` with a terminating `final` frame. Same pattern fixed on the pre-existing quota-exceeded path.
- **`llm_classifier.py` Tier-2 raw ChatGroq bypass** — was creating a `ChatGroq` directly regardless of BYO state. Now consults `get_active_byo_context()` and builds user-keyed client when BYO active. Closed the last leak point for free-tier consumption on chat turns.
- **Post-chat `update_summary` leak**: the conversation-context summariser ran *outside* the `apply_byo_context()` block and landed on platform keys. Moved inside the scope via new `_update_summary_in_byo_scope` helper (HTTP + WS paths).
- **Delete-key 404 runtime error**: clicking Delete on a stale UI card threw because the key was already gone server-side. Handler now treats 404 as the same end state as 204 (removed).
- **Empty-string sector on Sectors tab**: 3 ETFs (`EQUAL50.NS`, `MOM50.NS`, `VALUE.NS`) had literal `""` sectors that `dropna` missed; they collapsed into an unnamed bucket. Fixed by routing `sector` through `safe_str` before groupby.
- **`CacheService.set()` kwarg mismatch**: BYO Redis counter was silently failing — calling `cache.set(..., ex=...)` raised `TypeError: unexpected keyword 'ex'` (takes `ttl=`). Swallowed by the `except Exception`. Fixed both the real code and the test fakes.

### Migration notes

Generate a Fernet master key and add to `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Apply the Alembic migration and recreate the backend container so `env_file` is re-read:

```bash
docker compose exec -e PYTHONPATH=/app:/app/backend backend alembic upgrade head
docker compose up -d --force-recreate backend
```

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

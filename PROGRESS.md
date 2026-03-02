# PROGRESS.md — Session Log

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

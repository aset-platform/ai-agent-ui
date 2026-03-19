# Changelog

Session-by-session record of what was built, changed, and fixed.

---

## Mar 18–19, 2026 — Performance, TradingView Charts, Portfolio Management, Dash Retirement

### Added
- **Redis write-through cache** (`backend/cache.py`) for all 22 API endpoints with invalidation map
- **Cache warm-up** at startup: shared keys sync + per-ticker background thread + top N frequent users
- **TradingView lightweight-charts v5** replacing Plotly for Analysis page (~45KB vs ~8MB)
  - 4-pane chart: Candlestick + Volume + RSI + MACD with crosshair, zoom, dark mode
  - D/W/M interval selector with candle aggregation
  - Indicator toggles dropdown (SMA 50/200, Bollinger, Volume, RSI, MACD)
  - OHLC legend in chart header (ref-based, zero re-renders)
  - Bollinger Bands with distinct cyan lines
- **SWR caching** on all pages (dashboard, analytics, marketplace, admin, insights)
- **Aggregate endpoint** `GET /v1/dashboard/home` — 4 requests → 1
- **Per-ticker refresh** pipeline: `POST /v1/dashboard/refresh/{ticker}` with 6-step background job
- **User preferences** — localStorage + Redis sync with sliding 7-day TTL (`usePreferences` hook)
- **Smart cache warming** — pre-warms top N active users' data at startup (`CACHE_WARM_TOP_USERS` config)
- **Portfolio Management MVP** — append-only `portfolio_transactions` Iceberg table
  - Add/edit/delete stocks (ticker from registry, qty, price, date)
  - WatchlistWidget 2-tab layout (Portfolio | Watchlist)
  - HeroSection shows portfolio value per currency with total P&L
  - AddStockModal + EditStockModal
- **Unit tests** for report_builder.py (16 test cases)
- **Lightweight doc generation** — no full app bootstrap for mkdocs build

### Changed
- **Dash service retired** — removed from run.sh, config, navigation (4 services: redis, backend, frontend, docs)
- `/insights` iframe → redirect to native `/analytics/insights`
- Analysis page: tabs on left, searchable ticker dropdown on right, full-page chart
- Hero card: "Welcome back, Abhay!" (gradient, first name), total P&L, navigation buttons
- Quick action buttons navigate to pages (not chat)
- Chat FAB removed — toggle moved to AppHeader (all screen sizes)
- Next.js dev indicator disabled (`devIndicators: false`)
- Compare metrics table: added RSI, MACD Signal, Sentiment, best performer badge
- Forecast tab: horizon picker (3M/6M/9M), today marker, price target annotations
- BaseAgent: extracted `_build_llm` + `_build_synthesis_llm` (removed 90 lines duplication)
- gen_api_docs.py: proper endpoint-level auth detection via `route.dependant.dependencies`
- setup.sh: Redis port uses `${REDIS_PORT:-6379}` variable
- CORS: added PUT to allow_methods

### Fixed
- Chart flickering on indicator toggle (ref-based crosshair, memoized deps)
- Dark mode sync on TradingView chart (reads DOM classList at build time)
- Null-guard on price/change/OHLC fields across all pages
- LLM request count discrepancy (Admin reads Iceberg, not ephemeral counter)
- Forecast hooks before early returns (Rules of Hooks)
- Portfolio tab isolated from Watchlist (no data mixing)
- Portfolio top ticker auto-selected on load (SWR race fix)
- Hero buttons force Analysis tab via URL ?tab= param
- Tab switch auto-selects top ticker for signals widget
- `usePreferences` setState deferred to avoid update-during-render

Tickets: ASETPLTFRM-72, 73, 74, 75, 112, 113, 114, 115, 116, 117, 118 (11 tickets, 46 story points)
Branch: feature/sprint2-planning

---

## Mar 16, 2026 — Dashboard UI Overhaul + Dash-to-Next.js Migration

### Added
- Native portfolio dashboard with 7 widgets (hero, watchlist, analysis signals, LLM usage, forecast chart)
- Collapsible sidebar navigation with Dashboard sub-pages
- Chat side panel (FAB-triggered, resizable, past sessions)
- react-plotly.js chart integration with dark/light theming
- Native Link Ticker page (replaces Dash Marketplace)
- Native Dashboard Home (stock cards, search/analyse)
- Native Compare page (normalized price chart, correlation heatmap)
- Native Analysis page (tabbed: candlestick+RSI+MACD, forecast, compare)
- 6 dashboard API endpoints + 3 chart data endpoints
- Chat audit log (Iceberg table + flush on logout)
- India/US country filter with ₹/$ currency support
- Breadcrumb titles in header (Dashboard → Home, etc.)

### Changed
- Post-login landing: chat → portfolio dashboard
- Sidebar: floating grid button → persistent collapsible sidebar
- Dashboard renamed to "Portfolio", Analytics renamed to "Dashboard"
- Removed Insights from top-level nav (now under Dashboard group)
- Removed Dash header (Next.js handles all navigation)

### Fixed
- SSR hydration mismatch from crypto.randomUUID()
- Content clipped behind fixed sidebar (missing margin-left)
- All stocks showing $ regardless of market
- Analysis signals showing N/A (fetches from technical_indicators)
- Iframe pages not filling viewport height

Tickets: ASETPLTFRM-82 to 114 (33 tickets, 25 Done)
Files: ~60 new/modified across frontend + backend

---

## Mar 15, 2026 — WSL2 Compat, LLM Cascade Split, Report Template, Auto-Docs

### PR #92 — WSL2 Compatibility + DevOps UX

- **ASETPLTFRM-67** — Fix setup.sh prompt functions leaking captions into .env values. Added default superuser menu (`admin@demo.local / Admin123!`), numbered API key prompts `[1/6]`–`[6/6]`, `[set]` confirmation for secrets.
- **ASETPLTFRM-68** — Crash-resume via `~/.ai-agent-ui/.setup_state` markers. `--repair` mode fixes only symlinks, env files, and git hooks. `--force` resets all state.
- **ASETPLTFRM-69** — `run.sh`: 3-state status table (`● up` / `◐ listening` / `○ down`) with HTTP health probes. New `logs`, `doctor` commands. Post-launch health check with error context.
- **ASETPLTFRM-70** — Cross-platform install guides: `docs/setup/macos.md`, `linux.md`, `windows.md` (full WSL2 walkthrough). MkDocs "Getting Started" nav section.

Files: `setup.sh`, `run.sh`, `docs/setup/` (3 new), `mkdocs.yml`, `README.md`

### PR #93 — LLM Cascade Split + Report Template

- **ASETPLTFRM-66** — Split LLM cascade into 3 profiles: tool-calling (llama → kimi → scout), synthesis (gpt-oss-120b → kimi → Anthropic), test (free-only, no Anthropic). `AI_AGENT_UI_ENV=test` activates test profile. `BaseAgent` now has `llm_with_tools` + `llm_synthesis`.
- **ASETPLTFRM-65** — New `report_builder.py`: parses tool output text via regex, renders 5 deterministic markdown sections (header, technicals, forecast, calendar, charts). LLM produces verdict only (~150–250 tokens vs ~800–1200). `StockAgent.format_response()` prepends template.
- **ASETPLTFRM-71** (Bug) — Fix synthesis double-invoke (eliminated wasted LLM call per request). Cap news sub-agent to `max_iterations=2` (was 5+1=6 calls). Reinforce stock agent pipeline prompt. Result: 10 → 5 API calls per stock analysis (50% reduction).

Files: `backend/config.py`, `llm_fallback.py`, `agents/base.py`, `agents/loop.py`, `agents/stream.py`, `agents/general_agent.py`, `agents/stock_agent.py`, `agents/report_builder.py` (new), `tools/agent_tool.py`, `e2e/playwright.config.ts`, `tests/conftest.py`, `run.sh`

### PR #94 — Auto-Generated Docs + Drift Detection

- **ASETPLTFRM-63** — `scripts/gen_api_docs.py` + `scripts/gen_config_docs.py` introspect FastAPI routes and Settings fields, generate markdown tables. Wired via `mkdocs-gen-files` plugin. Generated pages gitignored.
- **ASETPLTFRM-64** — `scripts/docs_drift_check.py` compares code routes/config against hand-written `api.md`/`config.md`. Reports MISSING and STALE entries. `./run.sh docs-check` command.

Files: `scripts/gen_api_docs.py` (new), `scripts/gen_config_docs.py` (new), `scripts/docs_drift_check.py` (new), `mkdocs.yml`, `run.sh`, `requirements.txt`, `.gitignore`

---

## Mar 14, 2026 — Dark Mode CSS Fix

### ASETPLTFRM-61 — Fix: Dark mode "2 selected" badge font color

The multi-select dropdown on the Compare Stocks page showed dark, unreadable text for the "2 selected" count badge in dark mode.

- **Root cause**: Dash 4's `.dash-dropdown-value-count` uses built-in CSS variables (`--Dash-Text-Weak`, `--Dash-Fill-Interactive-Weak`) that aren't overridden by the app's `body.dark-mode` token system
- **Fix**: Added `body.dark-mode .dash-dropdown-value-count` rule in `dashboard/assets/custom.css` setting `color: var(--text-primary)` and `background: var(--border)`

Files: `dashboard/assets/custom.css` (4 lines added)

---

## Mar 13, 2026 — Tier Health Monitoring + API v1 Cutover

### ASETPLTFRM-13 — Groq Tier Health Monitoring

Added per-tier health monitoring to the N-tier Groq/Anthropic LLM cascade:

- **Health classification**: healthy (0 failures in 5-min window), degraded (1–3), down (4+), disabled (manual)
- **Latency stats**: avg + p95 from sliding window of 100 recent values
- **Cascade tracking**: per-model cascade count and failure timestamps
- **Admin endpoints**: `GET /v1/admin/tier-health`, `POST /v1/admin/tier-health/{model}/toggle` (superuser only)
- **Dashboard health cards**: color-coded status (green/yellow/red/grey) with cascade count and latency

Files: `backend/observability.py` (major), `backend/routes.py`, `dashboard/layouts/observability.py`, `dashboard/callbacks/observability_cbs.py`

Tests: 12 backend (`test_tier_health.py`), 6 dashboard (`test_tier_health_cards.py`), 3 E2E (`admin-deep.spec.ts`)

### ASETPLTFRM-20 — API v1 Full Cutover

Removed root-mounted duplicate API routes. All API traffic now goes through `/v1/` prefix only:

- **Backend**: removed root_router mount in `routes.py`; auth, ticker, and admin routers mounted with `prefix="/v1"`
- **Frontend**: added `API_URL` constant (`${BACKEND_URL}/v1`) in `lib/config.ts`; 9 files updated to use `API_URL` for API calls; `BACKEND_URL` kept for static assets (avatars) and WS URL derivation
- **Dashboard**: split `_BACKEND_URL` → `_BACKEND_HOST` + `_BACKEND_URL` (host/v1) in `auth_utils.py` and `admin_cbs2.py`
- **WebSocket**: stays at `/ws/chat` (not versioned)
- **Static files**: `/avatars/*` stays at root (not versioned)

| Before | After |
|--------|-------|
| `POST /chat` + `POST /v1/chat` | `POST /v1/chat` only |
| `GET /health` + `GET /v1/health` | `GET /v1/health` only |
| `GET /agents` + `GET /v1/agents` | `GET /v1/agents` only |

Tests: rewrote `test_api_versioning.py` (8 tests), updated `test_chat_stream.py` to `/v1/` paths

### Python 3.9 Compatibility

Added `from __future__ import annotations` to 7 backend files using PEP 604 `X | None` syntax: `observability.py`, `validation.py`, `token_budget.py`, `llm_fallback.py`, `models.py`, `tools/_ticker_linker.py`, `ws.py`

---

## Mar 11, 2026 — Sprint Phase 3 + Dashboard fixes

### Redis Token Store (Story 1.3)

Introduced a pluggable `TokenStore` protocol (`auth/token_store.py`) with two implementations:

- **`InMemoryTokenStore`** — dict-based with lazy TTL expiry (default)
- **`RedisTokenStore`** — uses `SETEX` for auto-expiry; lazy `import redis`

Factory: `create_token_store(redis_url, prefix)` — empty URL returns in-memory; connection failure falls back gracefully.

The JWT deny-list and OAuth state store now use `TokenStore` instead of raw `Set[str]` / `Dict`. Entries auto-expire via TTL matching token lifetime.

### API Versioning (Story 2.2)

Dual-mount of core routes at `/` (backward compat) and `/v1/`:

| Endpoint | Legacy | Versioned |
|----------|--------|-----------|
| Chat stream | `POST /chat/stream` | `POST /v1/chat/stream` |
| Health | `GET /health` | `GET /v1/health` |
| Agents | `GET /agents` | `GET /v1/agents` |

### Frontend Config Centralization

New `frontend/lib/config.ts` exports `BACKEND_URL`, `DASHBOARD_URL`, `DOCS_URL`. Replaced 18 duplicate `process.env.NEXT_PUBLIC_*` declarations across 9 frontend files.

### Dashboard Callback Race Conditions

Fixed two E2E failures caused by Dash callback timing:

1. **Admin RBAC blank page**: `display_page` used `State("auth-token-store")` — changed to `Input()` so it re-fires after `store_token_from_url` persists the JWT
2. **Analysis "Authentication required"**: Chart callbacks (`update_analysis_chart`, `update_compare`, etc.) only read `auth-token-store` State. Added `State("url", "search")` + `_resolve_token()` fallback to 6 callbacks

### Rate Limit Tuning

Increased from 5/15min → 30/15min (login), 3/hr → 10/hr (register), 10/min → 30/min (OAuth). Added 429 retry logic to E2E `apiLogin` and `auth.setup.ts`. Frontend login page shows distinct rate-limit message.

### Files changed: 7 new, 20+ modified

| File | Change |
|------|--------|
| `auth/token_store.py` | NEW — TokenStore protocol + implementations |
| `frontend/lib/config.ts` | NEW — centralized URL config |
| `auth/service.py` | REWRITE — uses TokenStore |
| `auth/tokens.py` | REWRITE — uses TokenStore |
| `auth/dependencies.py` | Creates store via factory |
| `auth/oauth_service.py` | OAuth state via TokenStore |
| `backend/routes.py` | Dual-mount /v1/ |
| `dashboard/app_layout.py` | Input trigger fix |
| `dashboard/callbacks/analysis_cbs.py` | URL token fallback |
| `dashboard/callbacks/forecast_cbs.py` | URL token fallback |
| `dashboard/callbacks/home_cbs.py` | URL token fallback |

**Tests**: 324 unit + 50 E2E pass. 16 new token store tests.

---

## Mar 11, 2026 — Sprint execution (Phases 1–2)

### Security Hardening

| # | Fix | Details |
|---|-----|---------|
| 1 | CORS | Whitelisted origins replace `allow_origins=["*"]` |
| 2 | Security headers | X-Content-Type-Options, X-Frame-Options, Referrer-Policy |
| 3 | Avatar traversal | Ext allowlist + `resolve().is_relative_to()` |
| 4 | Password reset | `secrets.token_urlsafe(32)` replaces `uuid4()` |
| 5 | PEP 604 | `Optional[X]` → `X \| None` in 6 files |

### Phase 1 — Rate limiting, JWKS, caching, algo opts

| # | Story | Details |
|---|-------|---------|
| 1.1 | Rate limiting | slowapi on login, password-reset, OAuth |
| 1.4 | JWKS verification | PyJWKClient for Google OAuth |
| 3.1 | Iceberg caching | Column projection + CachedRepository |
| 3.2 | Algo optimizations | TokenBudget O(1) totals, compressor early-exit |

### Phase 2 — Decomposition + HttpOnly cookies

| # | Story | Details |
|---|-------|---------|
| 2.1 | ChatServer decomp | Extracted `bootstrap.py` + `routes.py` |
| 1.2 | HttpOnly cookies | Refresh token in HttpOnly cookie |

---

## Mar 10, 2026 — N-tier Groq LLM cascade

### LLM Architecture Refactor

Replaced the 2-model (router/responder) FallbackLLM with an N-tier cascade: 4 Groq models tried in order, with Anthropic Claude Sonnet 4.6 as the final paid fallback.

**Tier order:** `llama-3.3-70b-versatile` (12K TPM) → `kimi-k2-instruct` (10K TPM) → `gpt-oss-120b` (8K TPM) → `llama-4-scout-17b` (30K TPM) → `claude-sonnet-4-6` (paid, unlimited).

### Key Changes

| # | Change | Details |
|---|--------|---------|
| 1 | N-tier FallbackLLM | `llm_fallback.py` rewritten — `groq_models: List[str]` replaces `router_model`/`responder_model` |
| 2 | Budget-aware routing | Per-model TPM checks with progressive compression at 70% headroom |
| 3 | Groq SDK `max_retries=0` | Disabled internal retries (was 45-56s delay); errors cascade immediately |
| 4 | `APIStatusError` cascade | 413 errors now caught and cascaded (not just 429) |
| 5 | Ticker auto-linking fix | Frontend sends `user_id`; 3 missing tools wired with `auto_link_ticker()` |
| 6 | Config simplification | Single `groq_model_tiers` CSV replaces router/responder/threshold fields |
| 7 | Test rewrite | 12 tests covering N-tier API: cascade, budget skip, compression, no-key fallback |

### Files changed: 9 modified

| File | Change |
|------|--------|
| `backend/llm_fallback.py` | REWRITE — N-tier cascade |
| `backend/config.py` | `groq_model_tiers` CSV setting |
| `backend/agents/config.py` | `groq_model_tiers: List[str]` field |
| `backend/agents/general_agent.py` | N-tier factory |
| `backend/agents/stock_agent.py` | N-tier factory |
| `tests/backend/test_llm_fallback.py` | 12 tests rewritten |
| `frontend/lib/auth.ts` | `getUserIdFromToken()` added |
| `frontend/hooks/useSendMessage.ts` | Sends `user_id` in chat body |
| `backend/tools/stock_data_tool.py` | `auto_link_ticker()` in 3 tools |

---

## Mar 10, 2026 — Team knowledge sharing ecosystem

### Infrastructure

- **Slim CLAUDE.md**: Rewrote from ~650 lines (~3,500 tokens) to ~85 lines (~800 tokens). Hard rules only; all detailed architecture, conventions, debugging, and onboarding content migrated to Serena shared memories
- **15 shared Serena memories**: Git-committed, PR-reviewed knowledge base across 5 categories — architecture (5), conventions (6), debugging (2), onboarding (1), api (1)
- **Selective `.serena/` gitignore**: `memories/shared/` tracked in git; `session/`, `personal/`, `cache/`, `project.local.yml` remain gitignored

### Automation

- **`/promote-memory` Claude Code skill**: AI-powered promotion from session/personal memories to shared. Cleans session-specific context, generalizes findings, creates branch + commit for PR
- **`/check-stale-memories` Claude Code skill**: Semantic staleness detection using Serena's `find_symbol` and `search_for_pattern`. Reports stale references with suggested fixes
- **`scripts/check-stale-memories.sh`**: CI grep-based stale memory check. Scans shared memories for backtick-quoted file paths that no longer exist. Non-blocking (exit 0)
- **`scripts/dev-setup.sh`**: Single-command AI tooling onboarding — verifies Claude Code, Serena, shared memories, creates local dirs, installs hooks. ~5 minutes for new developers

### Design

- Design doc: `docs/plans/2026-03-09-team-knowledge-sharing-design.md`
- Implementation plan: `docs/plans/2026-03-09-team-knowledge-sharing-plan.md`
- Hybrid sharing model with PR review gate for shared memories
- Two-layer staleness detection (CI + AI)

### Files changed: 21 new, 2 modified

| File | Change |
|------|--------|
| `.serena/memories/shared/**/*.md` (15) | NEW — shared memories |
| `.claude/commands/*.md` (2) | NEW — skills |
| `scripts/dev-setup.sh` | NEW — onboarding |
| `scripts/check-stale-memories.sh` | NEW — CI check |
| `docs/plans/*.md` (2) | NEW — design + plan |
| `CLAUDE.md` | REWRITE — 650→85 lines |
| `.gitignore` | EDIT — selective .serena/ |

---

## Mar 9, 2026 — Seed fixes, profile NaN fix, backfill script, Groq chunking strategy

### Bug Fixes

- **seed_demo_data.py**: Fixed 4 bugs — OHLCV column casing (lowercase → uppercase for `insert_ohlcv`), `insert_forecast_run` missing `horizon_months` arg, `insert_forecast_series` needs `run_date` + Prophet-style column rename, `.local` TLD rejected by Pydantic EmailStr (changed to `.com`)
- **auth/endpoints/helpers.py**: Profile edit crash — Parquet returns `float('nan')` for null string columns; added `_str_or_none()` guard and `isinstance(raw_perms, str)` check before `json.loads()`
- **E2E credential mismatch**: Updated 6 E2E files to use seeded demo credentials (`@demo.com`) instead of `@example.com`
- **E2E flaky tests**: Agent switcher retry with `force: true`, Enter-key test focus wait, forecast test rewrite for pre-populated dropdown

### New Features

- **`scripts/backfill_all.py`**: Full truncate + refetch pipeline — OHLCV, company info, dividends, analysis, quarterly results, Prophet forecast. Supports `--tickers`, `--period`, `--no-truncate`, `--skip-forecast`
- **`StockRepository.delete_ticker_data()`**: Copy-on-write bulk truncation across all 9 Iceberg tables
- **E2E profile save test**: New test verifying profile name edit saves without error

### Groq Rate-Limit Chunking Strategy (3 layers)

1. **`backend/token_budget.py`** (NEW): Sliding-window deque tracker for TPM/RPM/TPD/RPD per Groq model. 80% threshold for preemptive routing. Thread-safe via per-model locks. Hardcoded free-tier limits for 6 models
2. **`backend/message_compressor.py`** (NEW): Three-stage compression — system prompt condensing on iteration 2+ (~40% of original), history truncation (last 3 turns), tool result truncation (2K chars). Progressive fallback for tight budgets
3. **`backend/llm_fallback.py`** (REWRITE): Three-tier model routing — `llama-4-scout-17b` (30K TPM router) → `gpt-oss-120b` (8K TPM responder) → Anthropic Claude (fallback). Budget-checked before each call, cascades on exhaustion or 429

**Config additions**: `GROQ_ROUTER_MODEL`, `GROQ_RESPONDER_MODEL`, `MAX_HISTORY_TURNS`, `MAX_TOOL_RESULT_CHARS` in `backend/config.py`

**Design doc**: `docs/design/groq-chunking-strategy.md`

### Files changed: 16 modified, 4 new

| File | Change |
|------|--------|
| `backend/token_budget.py` | NEW — rate tracker |
| `backend/message_compressor.py` | NEW — message compression |
| `backend/llm_fallback.py` | REWRITE — three-tier router |
| `docs/design/groq-chunking-strategy.md` | NEW — design doc |
| `scripts/backfill_all.py` | NEW — backfill pipeline |
| `backend/config.py` | Added 4 settings |
| `backend/agents/config.py` | Added `router_model` field |
| `backend/agents/base.py` | Default token_budget/compressor attrs |
| `backend/agents/general_agent.py` | Wired router + budget |
| `backend/agents/stock_agent.py` | Wired router + budget |
| `backend/agents/loop.py` | Pass `iteration=` to invoke |
| `backend/agents/stream.py` | Pass `iteration=` to invoke |
| `backend/main.py` | Shared TokenBudget + Compressor |
| `auth/endpoints/helpers.py` | NaN guard |
| `scripts/seed_demo_data.py` | 4 bug fixes |
| `stocks/repository.py` | `delete_ticker_data()` |
| `tests/backend/test_llm_fallback.py` | Updated for new API |
| E2E files (6) | Credential + flaky fixes |

**Tests**: 155 backend pass, 50 E2E pass. Zero new dependencies.

---

## Mar 8, 2026 — E2E test stabilization

Ran full Playwright E2E suite against live services and fixed all failures. 10 root causes identified and resolved:

- **React 19 `fill()` incompatibility**: `pressSequentially()` for controlled textarea inputs
- **dbc 2.0.4 `data-testid` crash**: Wrapped dbc components in `html.Div` for test attributes
- **Dash debug menu overlay**: `{ force: true }` on pagination clicks
- **Mock NDJSON field name**: `response` not `content`
- **Agent selector**: Button group, not dropdown — `getByRole("button")`
- **Transient 500s**: Login retry loop (3 attempts) in `apiLogin()`
- **Dash reloader**: Test `outputDir` moved to `/tmp/` to avoid file-watcher restarts

**Result**: 49 tests passing (48 clean + 1 flaky), 0 hard failures. Exceeds the 43-test target.

---

## Mar 7, 2026 — Error overlay + Playwright E2E framework

### Error Overlay

Reusable error banner for dashboard refresh failures (`dashboard/components/error_overlay.py`). Fixed-position red banner with `dbc.Alert(duration=8000)` auto-dismiss. Wired to 3 refresh callbacks (home, analysis, forecast).

### Playwright E2E Framework

Full `e2e/` project at project root — Playwright 1.50+, TypeScript, Page Object Model.

| Component | Details |
|-----------|---------|
| Projects | 6: setup, auth, frontend, dashboard, admin, errors |
| Spec files | 14 across `tests/{auth,frontend,dashboard,errors}/` |
| POMs | 10 classes in `pages/{frontend,dashboard}/` |
| Fixtures | Auth setup (storageState), JWT token fixture for Dash |
| Dash helpers | `waitForDashCallback`, `waitForPlotlyChart`, `waitForDashLoading` |
| CI | `.github/workflows/e2e.yml` — chromium-only, caches browsers |

Added `data-testid` attributes to 16 frontend and 11 dashboard components.

---

## Mar 7, 2026 — 5-Epic feature sprint

### Epic 1: Admin Password Reset

New superuser-only endpoint `POST /users/{user_id}/reset-password` with dashboard modal integration. Pattern-match "Reset Pwd" button on each row of the admin users table. Audit-logged as `ADMIN_PASSWORD_RESET`.

### Epic 2: Smart Data Freshness Gates

- **Analysis**: Iceberg `analysis_date == today` check — skips re-analysis if already done today
- **Forecast**: 7-day cooldown — skips if `run_date` within last 7 days
- Both gates are non-blocking (wrapped in try/except, fall through on error)

### Epic 3: Virtualenv Relocation

Moved Python virtualenv from `backend/demoenv` (inside project tree) to `~/.ai-agent-ui/venv` (outside). Prevents linter tools from rewriting site-packages. `setup.sh` auto-migrates with symlink for backwards compat. Updated: `run.sh`, hooks, CI, `pyproject.toml`, `.flake8`, all docs.

### Epic 4: Per-User Ticker Linking

New `auth.user_tickers` Iceberg table linking users to their tracked tickers.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/users/me/tickers` | GET | List user's linked tickers |
| `/users/me/tickers` | POST | Link a ticker (validated) |
| `/users/me/tickers/{ticker}` | DELETE | Unlink a ticker |

Auto-linking: stock tools (`fetch_stock_data`, `analyse_stock_price`, `forecast_stock`) auto-link tickers to the requesting user via thread-local tracking. Default ticker (RELIANCE.NS) linked on user creation.

Dashboard home page now filters stock cards by user's linked tickers (dropdown still shows all).

### Epic 5: Ticker Marketplace

New dashboard page at `/marketplace` listing all available tickers from the central registry. Users can Add/Remove tickers from their watchlist with pattern-match buttons. Search filtering, market indicators, and company names displayed.

### Tests (+19 new → 255 total)

---

## Mar 7, 2026 — RSI/MACD tooltips + input validation hardening

### Feature: Dashboard Tooltips

Added educational info-icon tooltips for RSI and MACD indicators across the dashboard (screener, comparison table, filter labels, chart panel titles). Generalised the existing Sharpe tooltip system into a reusable `label_with_tooltip()` pattern.

### Security: Input Validation

Full OWASP-style audit identified 18 input validation gaps. All fixed:

| Priority | Fix | Files |
|----------|-----|-------|
| P0 | `ChatRequest.message` max 10k chars, `agent_id` regex | `backend/models.py` |
| P0 | `search_web` / `search_market_news` query validation | `search_tool.py`, `agent_tool.py` |
| P1 | Ticker regex on all 8 stock tools + 50-ticker batch limit | `stock_data_tool.py`, `forecasting_tool.py`, `price_analysis_tool.py` |
| P1 | `role` field: `Literal["general", "superuser"]` | `auth/models/request.py` |
| P2 | `max_length` on all auth string fields | `auth/models/request.py` |

### Bug Fixes

- Fixed duplicate DOM IDs preventing RSI tooltip from rendering on screener
- Added `captureevents=True` to Plotly annotations for hover to work
- Replaced `<`/`>` in tooltip text with Unicode `≤`/`≥`

### Tests (+28 new → 236 total)

---

## Mar 4, 2026 — Home page load latency optimisation

### Performance

Reduced home page load time from ~5 s to ~500 ms (cold) and ~50 ms (warm cache) by replacing 3N sequential per-ticker Iceberg scans with 2 batch reads + TTL-cached dict lookups.

| Scenario | Before | After | Speedup |
|----------|--------|-------|---------|
| Cold load (30 tickers) | ~5 s | ~500 ms | 10x |
| Warm cache (within 5 min) | ~2 s | ~50 ms | 40x |

### Changes

| File | Change |
|------|--------|
| `stocks/repository.py` | Added `get_all_latest_forecast_runs(horizon_months)` — batch read, one row per ticker |
| `dashboard/callbacks/iceberg.py` | Added `_get_registry_cached()`, `_get_forecast_runs_cached()` (5-min TTL); updated `clear_caches()` |
| `dashboard/callbacks/home_cbs.py` | Rewrote `refresh_stock_cards()`: batch pre-fetch + dict lookups replace per-ticker Iceberg calls; timing instrumentation added |
| `dashboard/callbacks/data_loaders.py` | `_load_reg_cb()` uses `_get_registry_cached()` |
| `dashboard/layouts/helpers.py` | `_load_registry()` uses `_get_registry_cached()` |

### Tests (+9 new → 157 total)

| Class | Tests |
|-------|-------|
| `TestGetAllLatestForecastRuns` | 3 — batch returns 1 row/ticker, filters by horizon, handles empty table |
| `TestRegistryCached` | 2 — caches on 2nd call, refreshes after TTL |
| `TestForecastRunsCached` | 2 — caches on 2nd call, refreshes after TTL |
| `TestRefreshStockCardsBatch` | 1 — batch card shape with mocked helpers |
| `TestClearCachesIncludesNewCaches` | 1 — both new caches invalidated |

---

## Mar 2, 2026 — Fix Adj Close NaN IndexError on forecast page

### Bug fix
- **Root cause**: yfinance 1.2 dropped `Adj Close`; Iceberg `stocks.ohlcv` stores `adj_close` as all NaN. Forecast page crashed with `IndexError` when building prophet DataFrame because all price values were NaN after `dropna`.
- `forecast_cbs.py`, `_forecast_model.py`, `iceberg.py`: Check `notna().any()` before using `Adj Close`; fall back to `Close`.
- `forecast_cbs.py`: Guard against empty `prophet_df` — returns error figure instead of crash.

### Tests (+5 new → 131 total)
- `TestPrepareDataForProphet` (3 tests): valid Adj Close, all-NaN Adj Close, missing column
- `TestOhlcvAdjCloseNanFallback` (2 tests): all-NaN fallback, valid adj_close passthrough

---

## Mar 1, 2026 — Documentation refresh (README, architecture diagrams, all docs pages)

Brought all documentation in sync with the modular refactor and Claude Sonnet 4.6 switch.

### README.md

| Section | Change |
|---------|--------|
| Services table | Backend stack updated from "FastAPI + LangChain + Groq" to "Claude Sonnet 4.6" |
| Quick Start | `GROQ_API_KEY` → `ANTHROPIC_API_KEY` |
| Architecture diagram | Agent nodes: "Groq LLM" → "Claude Sonnet 4.6" |
| Agentic loop diagram | Participant: "Groq LLM" → "Claude Sonnet 4.6" |
| Project structure | `auth/` subpackages (`models/`, `repo/`, `endpoints/`); `stocks/` package; `frontend/hooks/` + `frontend/components/` (9 components); `dashboard/layouts/` + `dashboard/callbacks/` packages; `backend/agents/` split files + `llm_fallback.py` |
| Tech stack | `langchain-groq` → `langchain-anthropic` |
| Env vars | `GROQ_API_KEY` → `ANTHROPIC_API_KEY` |
| "Switch to Claude" section | Removed — switch is complete |
| "Add a new agent" | References `frontend/lib/constants.ts` instead of `page.tsx` |
| Known Limitations | Removed "Groq is temporary" row |

### docs/dashboard/overview.md

- Home card refresh interval: **5 minutes → 30 minutes**
- Architecture section: monolithic `layouts.py` + `callbacks.py` → full package layout listing all sub-modules

### docs/frontend/overview.md

- File structure: added `hooks/` and `components/` with all files
- Component architecture: updated from "single file" to hooks-table + composition-shell description
- State table: `profile` added; OAuth state (now on login page) removed
- Effects: updated to reflect AbortController + debounced localStorage patterns
- Token propagation: IIFE → `useMemo` pattern
- Navigation menu: RBAC filtering description added

### docs/dev/decisions.md

- "Switching back to Claude (not yet done)" → "Claude Sonnet 4.6 is the active LLM" with FallbackLLM note
- "Single-file component" → "Modular components + hooks" with accurate description
- Session persistence: updated to mention 1-second debounce

### docs/backend/overview.md

- Module map: `langchain_groq` → `langchain_anthropic`; `ChatGroq` → `ChatAnthropic`; model `openai/gpt-oss-120b` → `claude-sonnet-4-6`; agents/ split files added
- Startup sequence: `AgentConfig` model field updated

**Commits:** `efc75c7` — *docs: update README and docs to reflect modular refactor and Claude Sonnet 4.6*
`52e67d4` — *docs: fix broken #path-replacement anchor in frontend/overview.md*

---

## Mar 1, 2026 — 23 dashboard + 17 frontend performance fixes

### Dashboard — 23 performance fixes across 9 files

#### `dashboard/callbacks/data_loaders.py` (Fix #1, #2, #5, #14, #19)

- **Column projection**: `tbl.scan(selected_fields=(...))` on registry Iceberg read — avoids loading all columns
- **Vectorised `iterrows()`**: replaced with `.values` array iteration + pre-computed column-index dict `_ci`
- **`_add_indicators_cached(ticker, df)`**: module-level TTL dict (5 min) wraps `_add_indicators()` — avoids recomputing RSI/MACD/BB on repeated chart loads for the same ticker

#### `dashboard/callbacks/chart_builders.py` (Fix #22)

- Volume and MACD histogram colour arrays: list comprehension → `np.where()` (vectorised, ~5× faster on 1000+ rows)

#### `dashboard/callbacks/utils.py` (Fix #11)

- `_get_currency()` now wraps `_load_currency_from_file()` with a 5-minute TTL dict cache — avoids opening the JSON metadata file on every callback render

#### `dashboard/callbacks/iceberg.py` (Fix #6, #10)

- `_get_iceberg_repo()`: init-once flag → TTL expiry (1-hour refresh) — survives Iceberg catalog restarts
- `_get_analysis_summary_cached(repo)` and `_get_company_info_cached(repo)`: 5-min TTL shared by screener, risk, and sectors callbacks — single Iceberg read per 5 minutes

#### `dashboard/callbacks/home_cbs.py` (Fix #4, #8)

- Hoisted `_load_raw(ticker)` — was called twice per iteration; now called once and reused
- `glob.glob() + os.stat()` → `pathlib.Path.glob()` with `.stat().st_mtime` sort

#### `dashboard/app_layout.py` (Fix #20)

- `dcc.Interval`: `5 * 60 * 1000` → `30 * 60 * 1000` ms — reduces unnecessary full-page refreshes

#### `dashboard/callbacks/insights_cbs.py` (Fix #5, #6, #7, #12, #13, #16)

- All 4× `iterrows()` loops → `.to_dict("records")`
- Direct `repo.get_all_latest_analysis_summary()` → `_get_analysis_summary_cached(repo)` in screener, risk, sectors
- Date push-down in correlation callback: filter `df_all` by 1-year cutoff before per-ticker loop
- `pd.to_numeric(df["rsi"])` computed once, reused for mask
- `load_catalog("local") + tbl.scan()` → `_get_iceberg_repo()` in `update_targets`
- Market filter: `apply(lambda)` → vectorised `.str.endswith((".NS", ".BO"))` mask

#### `dashboard/callbacks/analysis_cbs.py` (Fix #1, #2, #14)

- `update_analysis_chart` and `update_compare` loop: `_add_indicators(df)` → `_add_indicators_cached(ticker, df)`

#### `dashboard/layouts/analysis.py` (Fix #17)

- `_get_available_tickers_cached()`: 5-min TTL module-level dict wraps `_get_available_tickers()` — avoids repeated filesystem scans on layout rebuilds

**Commit:** `b683ce4` — *perf: implement 12 backend performance fixes* (superseded by full list above)

---

### Frontend — 17 performance fixes across 9 files

#### `frontend/hooks/useSendMessage.ts` (Fix #1, #2)

- `AbortController` ref; cleanup `useEffect` aborts in-flight stream on unmount
- `sendMessage`, `handleKeyDown`, `handleInput` wrapped in `useCallback`

#### `frontend/hooks/useChatHistory.ts` (Fix #3)

- localStorage save debounced 1 second with `useRef` timer — avoids blocking the main thread on every streaming token

#### `frontend/components/MarkdownContent.tsx` (Fix #4)

- `preprocessContent(content)` → `useMemo(() => preprocessContent(content), [content])` — avoids reprocessing on every parent render

#### `frontend/components/EditProfileModal.tsx` (Fix #5, #6)

- `reader.readAsDataURL` → `URL.createObjectURL` — non-blocking, no base64 encoding overhead
- `useEffect` cleanup revokes the blob URL when modal closes — prevents memory leak
- `handleFileChange` wrapped in `useCallback`

#### `frontend/app/auth/oauth/callback/page.tsx` (Fix #7)

- `cancelled` flag pattern replaces the eslint-disable comment; all state updates guarded by `if (!cancelled)`; deps corrected to `[searchParams, router]`

#### `frontend/app/login/page.tsx` (Fix #8, #9)

- `AbortController` on OAuth providers fetch with cleanup
- `loginAbortRef` cancels any previous in-flight login on re-submit

#### `frontend/components/NavigationMenu.tsx` (Fix #10)

- `visibleItems = useMemo(() => NAV_ITEMS.filter(...), [profile])` — filter only recomputes when profile changes

#### `frontend/lib/auth.ts` (Fix #11)

- `refreshAccessToken`: 10-second `AbortController` timeout prevents a hung refresh from blocking all API calls

#### `frontend/app/page.tsx` (Fix #12–#17)

- Profile fetch: `AbortController` + cleanup on unmount
- `handleMenuOutsideClick`: extracted to `useCallback` — stable reference across effect re-runs
- `iframeSrc`: IIFE → `useMemo([view, iframeUrl])` — avoids calling `getAccessToken()` on every render
- `agentHint`: inline `AGENTS.find()` → `useMemo([agentId])` — avoids O(n) scan on every keystroke
- Message keys: `key={i}` → `key={\`${timestamp}-${role}-${i}\`}` — stable composite key

**Commit:** `b683ce4` — *perf: implement 23 dashboard performance fixes*
`1203e4d` — *perf: fix 17 frontend performance bottlenecks*

---

## Feb 26, 2026 — Dashboard pagination, market filter, pre-commit hook

### Dashboard — Home page market filter + pagination

Added market segmentation and pagination to the Home stock cards.

**Market filter:**

| Button | Tickers shown |
|--------|--------------|
| 🇮🇳 India | `.NS` (NSE) and `.BO` (BSE) tickers |
| 🇺🇸 US | All other tickers |

Defaults to India. Switching market resets to page 1.

**Home card pagination:**

- 12 cards per page (default); page-size dropdown: 10 / 25 / 50 / 100
- Count label: "Showing 1–12 of 47"
- `paddingBottom: "5rem"` on `#page-content` prevents the pagination row overlapping the fixed Next.js navigation FAB and the Plotly watermark

**Architecture — data/render split:**

`refresh_stock_cards` now stores raw serialisable dicts in `dcc.Store(id="stock-raw-data-store")` instead of building Dash components. A new `render_home_cards` callback reads the store, filters by market, paginates, and builds components — fully client-side without re-fetching data.

| File | Change |
|------|--------|
| `dashboard/layouts.py` | India/US `dbc.ButtonGroup`, pagination row with count text + `dbc.Select`, `dcc.Store` additions |
| `dashboard/callbacks.py` | `_get_market()` helper, rewritten `refresh_stock_cards`, 4 new callbacks (`update_market_filter`, `render_home_cards`, `reset_home_page_on_size_change`, plus 3 for admin) |

---

### Dashboard — Admin Users + Audit Log pagination and search

**Users tab:**

- Debounced search input filters by name, email, or role
- Table paginated (10 / page default); page-size dropdown: 10 / 25 / 50 / 100
- Filter or size change resets to page 1

**Audit Log tab:**

- Debounced search input filters by event type, actor, target, or metadata
- Table paginated (10 / page default); configurable page size

| File | Change |
|------|--------|
| `dashboard/layouts.py` | Search inputs + pagination rows with page-size selects; `dcc.Store(id="audit-data-store")` |
| `dashboard/callbacks.py` | `load_users_table` → `users-store.data`; `load_audit_log` → `audit-data-store.data`; new `render_users_page`, `render_audit_page`, `reset_users_page_on_filter`, `reset_audit_page_on_filter` |

---

### Pre-commit quality gate (`hooks/pre-commit` + `hooks/pre_commit_checks.py`)

New hook runs on every `git commit`, operating only on staged modified/created files (`git diff --cached --diff-filter=ACM`).

**Four checks:**

| # | Check | Auto-fix |
|---|-------|---------|
| 1 | Bare `print()`, missing Google docstrings, naming, OOP, XSS/SQL injection | Yes — Claude rewrites the file and re-stages |
| 2 | `CLAUDE.md`, `PROGRESS.md`, `README.md` freshness | Yes — Claude patches stale sections and re-stages |
| 3 | Docs pages freshness (via `_DOCS_MAP`) | Yes — Claude patches and re-stages |
| 4 | `docs/dev/changelog.md` descending date order | Yes — deterministic sort, no API needed |

Checks 1–3 require `ANTHROPIC_API_KEY`. The hook loads `backend/.env` automatically so the key doesn't need to be exported in the shell.

**Environment variables:**

| Variable | Effect |
|----------|--------|
| `SKIP_PRE_COMMIT=1` | Bypass the entire hook |
| `SKIP_CLAUDE_CHECKS=1` | Run static checks only; skip API-based auto-fix and doc updates |
| `ANTHROPIC_API_KEY` | Enables checks 1–3; set in `backend/.env` |

**Install:**
```bash
cp hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

**Commits:** `c68c2fc` — *feat: dashboard pagination, market filter, and pre-commit quality gate*
`16a9441` — *docs: update README and dashboard docs; fix hook dotenv loading*

---

## Feb 25, 2026 (continued — deployment fixes)

### Auth deployment fixes: JWT env propagation + dashboard dotenv loader

Two runtime bugs discovered and fixed after first-deploy of the auth module.

**Bug 1 — `JWT_SECRET_KEY` not visible to `auth/dependencies.py`**

`auth/dependencies.py` reads `JWT_SECRET_KEY` directly from `os.environ`. Pydantic `Settings` reads `backend/.env` but does **not** write values back to `os.environ`. Without an explicit shell export the backend crashed on every auth request.

| File | Change |
|------|--------|
| `backend/main.py` | Added module-level block after `settings = get_settings()` that copies `jwt_secret_key`, `access_token_expire_minutes`, and `refresh_token_expire_days` from the Pydantic model into `os.environ` (only if not already set) |

**Bug 2 — Dashboard showed "Authentication required" for valid tokens**

The Dash process is separate from the backend. `_validate_token()` in `dashboard/callbacks.py` reads `JWT_SECRET_KEY` from `os.environ`. The dashboard process never loaded `backend/.env`, so the secret was always empty and every token failed validation.

| File | Change |
|------|--------|
| `dashboard/app.py` | Added `_load_dotenv()` helper (same pattern as `scripts/seed_admin.py`) executed at module import time; reads `<project-root>/.env` and `backend/.env` into `os.environ` before any Dash or callback imports |

**Commits:** `4d4bb84` — *feat: complete auth module with JWT, RBAC, admin UI, and deployment fixes*

---

## Feb 25, 2026

### Auth module — Phases 1–6 (JWT, RBAC, admin UI, seed script)

Complete JWT-based authentication and role-based access control (RBAC) added across all three surfaces.

**New files:**

| File | Description |
|------|-------------|
| `auth/__init__.py` | Package marker |
| `auth/create_tables.py` | Idempotent Iceberg table init (`auth.users` + `auth.audit_log`) |
| `auth/repository.py` | `IcebergUserRepository` — full CRUD + audit log append |
| `auth/service.py` | `AuthService` — bcrypt hashing, JWT HS256 create/decode, in-memory deny-list |
| `auth/models.py` | Pydantic request/response models for all auth + user endpoints |
| `auth/dependencies.py` | FastAPI dependency functions: `get_current_user`, `superuser_only`, `get_auth_service` |
| `auth/api.py` | `create_auth_router()` — 12 REST endpoints |
| `scripts/seed_admin.py` | Idempotent superuser bootstrap from `ADMIN_EMAIL` + `ADMIN_PASSWORD` env vars |
| `.pyiceberg.yaml.example` | Committed reference for Iceberg catalog config |
| `docs/backend/auth.md` | Full auth documentation page |
| `frontend/lib/auth.ts` | Token helpers: `getAccessToken`, `setTokens`, `clearTokens`, `isTokenExpired`, `getRoleFromToken`, `refreshAccessToken` |
| `frontend/lib/apiFetch.ts` | Drop-in authenticated `fetch` wrapper — injects Bearer token, auto-refreshes on expiry, redirects to `/login` on 401 |
| `frontend/app/login/page.tsx` | Login page — email + password form, redirect-if-already-authed guard |

**Modified files:**

| File | Change |
|------|--------|
| `backend/config.py` | Added `jwt_secret_key`, `access_token_expire_minutes`, `refresh_token_expire_days` to `Settings` |
| `backend/main.py` | Project root added to `sys.path`; `create_auth_router()` mounted via `app.include_router()` |
| `backend/requirements.txt` | Added `pyiceberg[sql-sqlite]`, `python-jose[cryptography]`, `passlib[bcrypt]`, `bcrypt==4.0.1`, `email-validator`, `python-multipart` |
| `dashboard/app.py` | Imported auth helpers; added `dcc.Store(id="auth-token-store")`; `/admin/users` route; global change-password modal to layout |
| `dashboard/callbacks.py` | Added `_validate_token()`, `_unauth_notice()`, `_admin_forbidden()`, `_resolve_token()`, `_api_call()`, `store_token_from_url` callback, `display_page` auth guard, 7 admin callbacks |
| `dashboard/layouts.py` | Added `admin_users_layout()`; updated NAVBAR with "Admin" link + "Change Password" button |
| `frontend/app/page.tsx` | Auth guard on mount; `apiFetch` replaces `fetch`; logout button; `"admin"` view type; Admin nav item (superuser-only); `iframeSrc` appends `?token=<jwt>` |
| `run.sh` | Added `_init_auth()` — runs `create_tables.py` + `seed_admin.py` on first `./run.sh start` |
| `mkdocs.yml` | Added "Auth & Users: backend/auth.md" to Backend nav |
| `.gitignore` | Added `data/iceberg/`, `.pyiceberg.yaml` |

**New API endpoints:**

| Method | Path | Auth |
|--------|------|------|
| `POST` | `/auth/login` | Public |
| `POST` | `/auth/login/form` | Public (OAuth2 form) |
| `POST` | `/auth/refresh` | Refresh token |
| `POST` | `/auth/logout` | Access token |
| `POST` | `/auth/password-reset/request` | Access token |
| `POST` | `/auth/password-reset/confirm` | Access token |
| `GET` | `/users` | Superuser |
| `POST` | `/users` | Superuser |
| `GET` | `/users/{user_id}` | Superuser |
| `PATCH` | `/users/{user_id}` | Superuser |
| `DELETE` | `/users/{user_id}` | Superuser |
| `GET` | `/admin/audit-log` | Superuser |

**Commits:** `4d4bb84` — *feat: complete auth module with JWT, RBAC, admin UI, and deployment fixes*

---

## Feb 24, 2026 (continued — currency fix)

### Dynamic currency symbols for multi-market stocks

Replaced all hard-coded `$` (USD) price symbols with dynamic currency symbols
loaded from `data/metadata/{TICKER}_info.json`. Indian stocks now show `₹`,
UK stocks `£`, EU stocks `€`, etc.

**Backend:**

| File | Change |
|------|--------|
| `backend/tools/price_analysis_tool.py` | Added `import json`, `_DATA_METADATA` path, `_currency_symbol()` and `_load_currency()` helpers; 5 report-string `$` → `{sym}` |
| `backend/tools/forecasting_tool.py` | Same helpers added; 2 chart annotation `$` → `{sym}`; 5 report-string `$` → `{sym}`; `yaxis_title` → dynamic currency code |
| `backend/tools/stock_data_tool.py` | Same helpers added; dividend report `$` → dynamic symbol |

**Dashboard:**

| File | Change |
|------|--------|
| `dashboard/callbacks.py` | Added `_currency_symbol()` and `_get_currency()` helpers; `_build_stats_cards` / `_build_target_cards` / `_build_accuracy_row` / `_build_forecast_fig` / `refresh_stock_cards` all use dynamic symbol; `_build_target_cards` and `_build_accuracy_row` gained `ticker` parameter |

**Commit:** `5c017f2` — *fix: dynamic currency symbols for multi-market stocks*

---

## Feb 24, 2026 (continued)

### Streaming, request timeout, iframe cross-origin, and dashboard light theme

Four independent improvements.

**Backend:**

| File | Change |
|------|--------|
| `backend/config.py` | Added `agent_timeout_seconds: int = 120` to `Settings` |
| `backend/agents/base.py` | Added `stream()` method — yields NDJSON events; added `json`, `Iterator` imports |
| `backend/main.py` | Added `asyncio`/`queue`/`threading`/`StreamingResponse` imports; `/chat` now uses `asyncio.wait_for` (HTTP 504 on timeout); new `POST /chat/stream` endpoint |

**Dashboard:**

| File | Change |
|------|--------|
| `dashboard/app.py` | `dbc.themes.DARKLY` → `dbc.themes.FLATLY`; added `@server.after_request` `allow_iframe` hook |
| `dashboard/assets/custom.css` | Full rewrite — light palette with CSS variables; indigo accent matching chat UI |
| `dashboard/callbacks.py` | All `template="plotly_dark"` → `"plotly_white"`; explicit `paper_bgcolor`/`plot_bgcolor`/`font`/`gridcolor`; annotation colors updated for light bg; stock card `text-white` → `text-dark`; table class updated |
| `dashboard/layouts.py` | NAVBAR `color="light"`, `dark=False`; H2 `text-white` removed; input `bg-dark text-white` removed; controls rows `bg-dark` → `bg-light border`; loading spinners `#4c8eff`/`#4caf50` → `#4f46e5` |

**Frontend:**

| File | Change |
|------|--------|
| `frontend/app/page.tsx` | `axios.post` → `fetch()` + `ReadableStream`; `TypingDots` → `StatusBadge`; `statusLine` state; `iframeLoading`/`iframeError` state; spinner + error banner on iframe; "Open in new tab ↗" in header; `switchView` resets iframe states; `handleInternalLink` resets iframe states |

**Commit:** `be09863` — *feat: streaming, request timeout, iframe cross-origin, and dashboard light theme*

---

## Feb 24, 2026

### SPA navigation, session persistence, and UI hardening

Eight improvements across frontend and backend committed as a single session.

**Backend:**

| File | Change |
|------|--------|
| `backend/agents/base.py` | Added `MAX_ITERATIONS = 15` constant; guard at top of agentic loop logs `WARNING` and breaks when `iteration > MAX_ITERATIONS` |

**Frontend new files:**

| File | Description |
|------|-------------|
| `frontend/.env.local.example` | Committed reference for `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_DASHBOARD_URL`, `NEXT_PUBLIC_DOCS_URL` |

**Frontend changes (`frontend/app/page.tsx`):**

| Change | Detail |
|--------|--------|
| Env-based backend URL | `http://127.0.0.1:8181` replaced with `` `${process.env.NEXT_PUBLIC_BACKEND_URL}/chat` `` |
| localStorage persistence | Load-on-mount + save-on-change `useEffect` hooks; `Date` objects revived on load |
| `View` state + SPA routing | `"chat" \| "docs" \| "dashboard"` state; docs and dashboard rendered as full-height `<iframe>`; chat state preserved on view switch |
| `iframeUrl` state | Stores the specific URL when opened via an internal link; reset to `null` when switching via menu |
| Navigation menu | Grid icon FAB (bottom-right); `NAV_ITEMS` array with Chat / Docs / Dashboard; active view highlighted |
| `preprocessContent()` | Replaces chart file paths with dashboard links; strips data file paths |
| `handleInternalLink()` | Sets `view` + `iframeUrl` when a dashboard/docs link in chat is clicked |
| Internal link routing in `MarkdownContent` | `onInternalLink` prop; `a` renderer renders `<button>` for internal links, `<a target="_blank">` for external |

**Commit:** `c570a98` — *feat: SPA navigation, session persistence, iteration cap, and env config*

---

## Feb 23, 2026 (continued)

### Per-agent history, analysis cache, and market news tool

Three independent improvements committed as `895df0f`.

**New files:**

| File | Description |
|------|-------------|
| `backend/tools/agent_tool.py` | `create_search_market_news_tool(general_agent)` — wraps `GeneralAgent` as a `@tool` callable by the stock agent |

**Modified:**

| File | Change |
|------|--------|
| `frontend/app/page.tsx` | Replaced single `messages` state with `histories: Record<string, Message[]>` keyed by `agentId`; switching agents now preserves each conversation independently |
| `backend/tools/price_analysis_tool.py` | Added same-day text cache (`data/cache/{TICKER}_analysis_{date}.txt`); `analyse_stock_price` returns cached result immediately on repeat calls |
| `backend/tools/forecasting_tool.py` | Same-day cache added (`data/cache/{TICKER}_forecast_{N}m_{date}.txt`); `forecast_stock` skips Prophet retraining if cache exists |
| `backend/agents/stock_agent.py` | Added `"search_market_news"` to `tool_names`; updated system prompt step 5 to call it before finalising each report |
| `backend/main.py` | Creates and registers `search_market_news` tool between general and stock agent construction (dependency order) |
| `.gitignore` | Added `data/cache/` |

**Commit:** `895df0f` — *feat: per-agent history, analysis cache, and market news tool*

---

## Feb 23, 2026

### Plotly Dash Dashboard (Phase 8)

Completed the four-page interactive web dashboard.

**New files:**

| File | Description |
|------|-------------|
| `dashboard/__init__.py` | Package marker with module docstring |
| `dashboard/app.py` | Dash entry point — DARKLY theme, `dcc.Location` routing, `dcc.Store`, `dcc.Interval`, `server` attr for gunicorn |
| `dashboard/layouts.py` | `home_layout`, `analysis_layout`, `forecast_layout`, `compare_layout` factories + `NAVBAR` |
| `dashboard/callbacks.py` | All interactive callbacks registered via `register_callbacks(app)` |
| `dashboard/assets/custom.css` | Dark theme overrides (cards, sliders, dropdowns, tables) |
| `run_dashboard.sh` | Convenience launcher script |
| `docs/dashboard/overview.md` | This documentation page |

**Bug fix:**

- Added `allow_duplicate=True` on `forecast-accuracy-row.children` in `run_new_analysis` callback — two callbacks write to that output and Dash requires explicit opt-in for duplicate outputs.

**Commits:**

| Hash | Message |
|------|---------|
| `c219dac` | feat: add Plotly Dash stock analysis dashboard (Phase 8) |
| `422e85b` | fix: allow duplicate forecast-accuracy-row output in run\_new\_analysis callback |

---

### Stock Analysis Agent

Added a full stock analysis capability backed by Yahoo Finance, Prophet, and Plotly.

**New files:**

| File | Description |
|------|-------------|
| `backend/agents/stock_agent.py` | `StockAgent(BaseAgent)` + `create_stock_agent` factory |
| `backend/tools/stock_data_tool.py` | 6 `@tool` functions — delta fetch + parquet storage |
| `backend/tools/price_analysis_tool.py` | `analyse_stock_price` — technical indicators + 3-panel chart |
| `backend/tools/forecasting_tool.py` | `forecast_stock` — Prophet forecast + confidence chart |
| `docs/stock_agent.md` | Stock agent documentation |

**Modified:**

- `backend/agents/base.py` — added `SystemMessage` support; `_build_messages()` prepends system prompt when set.
- `backend/main.py` — registered 8 stock tools and `StockAgent`.
- `frontend/app/page.tsx` — agent selector toggle (General / Stock Analysis).

**Commit:** `bdd3701` — *feat: add stock analysis agent with Yahoo Finance delta fetching, Prophet forecasting, price analysis, and Plotly charts*

---

## Feb 22, 2026

### OOP Backend Refactor

Deleted `backend/agent.py` and replaced it with a proper package structure.

**New files:**

| File | Description |
|------|-------------|
| `backend/agents/__init__.py` | Package marker |
| `backend/agents/base.py` | `AgentConfig` dataclass + `BaseAgent` ABC with full agentic loop |
| `backend/agents/registry.py` | `AgentRegistry` — maps agent IDs to agent instances |
| `backend/agents/general_agent.py` | `GeneralAgent(BaseAgent)` + `create_general_agent` factory |
| `backend/tools/__init__.py` | Package marker |
| `backend/tools/registry.py` | `ToolRegistry` — maps tool names to `BaseTool` instances |
| `backend/tools/time_tool.py` | `get_current_time` `@tool` |
| `backend/tools/search_tool.py` | `search_web` `@tool` (with try/except) |
| `backend/config.py` | `Settings(BaseSettings)` with `@lru_cache` singleton |
| `backend/logging_config.py` | `setup_logging()` — console + rotating file handler |

**Rewritten:**

- `backend/main.py` — full rewrite as `ChatServer` class; added `GET /agents` endpoint; `POST /chat` now accepts `agent_id` and returns it in the response; errors now raise `HTTPException` (404/500) instead of returning error strings in 200 bodies.

**Updated:**

- `.gitignore` — added `logs/` entry.
- `CLAUDE.md` — full sync with new file tree, API shapes, new decisions.
- `PROGRESS.md` — Feb 22 session log added.

**Commit:** `fa20966` — *refactor: OOP backend restructure with agents/, tools/ packages and structured logging*

---

### MkDocs Setup

- Installed `mkdocs==1.6.1` and `mkdocs-material==9.7.2` into `demoenv`.
- Created `mkdocs.yml` with material theme (indigo, light/dark toggle), navigation tabs, code copy buttons, and full nav tree.
- Created `docs/` directory structure with all pages.

---

## Feb 21, 2026

### Initial Build

Built the complete application from scratch in a single session.

**Backend (`backend/main.py`, `backend/agent.py`):**

- FastAPI server with CORS open to all origins.
- `POST /chat` endpoint accepting `message` and `history`.
- LangChain agentic loop in `run_agent()`: invokes LLM → executes tool calls → feeds `ToolMessage` results back → repeats until no tool calls → returns `response.content`.
- Two tools: `get_current_time` and `search_web`.

**Frontend (`frontend/app/page.tsx`):**

- Single-page chat UI with message bubbles, avatars, timestamps, typing indicator, auto-growing textarea.
- Full conversation history sent with every request.
- Error state shown in the chat bubble on network failure.

**LLM history:**

| Commit | LLM | Reason |
|--------|-----|--------|
| `6604b74` | Claude Sonnet 4.6 | Initial implementation |
| `ee7967f` | Groq `openai/gpt-oss-120b` | Anthropic API not working during testing |
| `ef643f7` | Groq (unchanged) | Added real SerpAPI search tool |

**Commits:**

| Hash | Message |
|------|---------|
| `6604b74` | Initial commit: agentic chat app with Claude Sonnet 4.6 |
| `ee7967f` | chore: swap LLM back to Groq (openai/gpt-oss-120b) for testing |
| `ef643f7` | feat: implement search_web tool with SerpAPI (real Google results) |
| `89d7eb4` | docs: update CLAUDE.md and add PROGRESS.md session log |

---

## Known Issues / Pending Work

| Issue | Priority | Notes |
|-------|----------|-------|
| SerpAPI key required for web search | Medium | Free tier (100/month) at serpapi.com |
| Refresh token deny-list is in-memory | Low | Cleared on backend restart — revoked tokens valid until natural expiry (7 days) |
| Facebook SSO | Low | Code complete; credentials are placeholders — button hidden until real credentials added |

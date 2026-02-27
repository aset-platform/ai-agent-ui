# PROGRESS.md — Session Log

---

# Session: Feb 27, 2026 — Branching strategy + Pre-commit hook improvements

## What We Built

Four improvements to developer workflow and CI/CD infrastructure.

### 1. Branching strategy

Created `dev`, `qa`, and `release` branches from `main` and pushed to remote.
Full `feature/* → dev → qa → release → main` workflow with CI/CD and branch protection rules.

**Files created:**

| File | Purpose |
|---|---|
| `.github/workflows/ci.yml` | GitHub Actions CI — per-branch jobs (dev/qa/release/main); Python + Next.js lint + mkdocs build; QA+ adds backend smoke test |
| `.github/CODEOWNERS` | Maps merge paths to reviewer groups (qa-team, tech-lead+qa-lead, senior-engineers) |
| `.github/pull_request_template.md` | Standard PR checklist (type tag, tests, docs, no secrets) |

**Branch protection rules** (to be applied manually in GitHub → Settings → Branches):
- `main`: no direct push, 2 approvals, CI must pass
- `release`: PR from `qa` only, 1 approval + QA lead, CI must pass
- `qa`: PR from `dev` only, 1 approval, integration tests must pass
- `dev`: PR from `feature/*`, 1 approval, unit tests + lint must pass

### 2. Pre-commit hook: Groq → Claude

`hooks/pre_commit_checks.py` now uses the Anthropic SDK (`claude-sonnet-4-6`) instead of Groq
for all LLM-powered auto-fix and documentation updates.

- `_call_claude()` now calls `anthropic.Anthropic(api_key=...).messages.create(model="claude-sonnet-4-6", ...)`
- `has_llm` renamed to `has_claude`; checks `ANTHROPIC_API_KEY` instead of `GROQ_API_KEY`
- All skip messages updated to reference `ANTHROPIC_API_KEY`
- Module + class docstrings updated to remove Groq references
- Graceful skip when `ANTHROPIC_API_KEY` is absent (no blocking)

### 3. Docs check: mkdocs build validation

`_update_docs()` now calls `_run_mkdocs_build()` after all LLM doc patches are applied.

- `_run_mkdocs_build()` — new helper: runs demoenv mkdocs in a temp dir, cleans up with `shutil.rmtree`
- Build failure is **warned but never blocking** — the developer is informed and can fix before pushing (pre-push hook will block)
- If no docs pages were mapped to staged changes, `mkdocs build` still runs as a final sanity check
- `import shutil` added to top-level imports

### 4. Branch-aware pre-commit

`hooks/pre-commit` detects the current branch and warns on direct commits to protected branches.

- Detects branch via `git symbolic-ref --short HEAD`
- Warns (does not block) when committing directly to `main`, `qa`, or `release`
- Exports `GIT_CURRENT_BRANCH` env var for the Python script
- `PreCommitChecker.__init__` reads `GIT_CURRENT_BRANCH` into `self.current_branch`
- Banner for check [1/4] now shows `(branch: <name>)` suffix

### Files changed

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | New — CI pipeline for all 4 branches |
| `.github/CODEOWNERS` | New — reviewer group mappings |
| `.github/pull_request_template.md` | New — standard PR checklist |
| `hooks/pre-commit` | Branch detection + warning + GIT_CURRENT_BRANCH export; Groq → Claude comment |
| `hooks/pre_commit_checks.py` | Groq → Anthropic SDK; has_llm → has_claude; GROQ_API_KEY → ANTHROPIC_API_KEY; _run_mkdocs_build(); import shutil; current_branch |

---

# Session: Feb 26, 2026 — Google + Facebook SSO (OAuth2 PKCE)

## What We Built

Full SSO integration on top of the existing JWT auth, following `sso_plan.md`.
Google is live (credentials already in `backend/.env`).
Facebook code is fully implemented; credentials are placeholder — will be
wired up once the user provides real Facebook Developer credentials.

### Backend changes

**`auth/oauth_service.py`** (new)
- `OAuthService` class with in-memory CSRF state store (10-min TTL, lazy cleanup)
- `generate_authorize_url(provider, code_challenge)` — builds consent URL + registers state
- `validate_state(state, provider)` — single-use consumption with expiry check
- `exchange_google_code(code, code_verifier)` — PKCE token exchange; decodes `id_token` with PyJWT (no sig verification, trust HTTPS)
- `exchange_facebook_code(code)` — two-step Graph API exchange (access token → `/me` profile)

**`auth/migrate_users_table.py`** (new)
- Idempotent Iceberg schema migration — adds `oauth_provider`, `oauth_sub`, `profile_picture_url` to `auth.users`
- Run once: `python auth/migrate_users_table.py`

**`auth/create_tables.py`** — extended `_users_schema()` with 3 new nullable fields (field IDs 12–14) so fresh installs include them

**`auth/repository.py`**
- Updated `_USERS_PA_SCHEMA` with 3 new nullable string fields
- Updated `create()` to include `oauth_provider`, `oauth_sub`, `profile_picture_url`
- Added `get_by_oauth_sub(provider, oauth_sub)` — full-scan lookup
- Added `get_or_create_by_oauth(...)` — three-way upsert (sub match → email match → create new)

**`auth/models.py`**
- `OAuthProvider` enum (`google`, `facebook`)
- `OAuthAuthorizeResponse` — `{state, authorize_url}`
- `OAuthCallbackRequest` — `{provider, code, state, code_verifier?}`

**`auth/api.py`** — 3 new endpoints (15 total):
- `GET /auth/oauth/providers`
- `GET /auth/oauth/{provider}/authorize?code_challenge=<hash>`
- `POST /auth/oauth/callback`
- `_get_oauth_svc()` singleton via `@lru_cache`

**`backend/config.py`** — 5 new settings: `google_client_id`, `google_client_secret`, `facebook_app_id`, `facebook_app_secret`, `oauth_redirect_uri`

**`PyJWT`** installed into `demoenv`; `requirements.txt` frozen.

### Frontend changes

**`frontend/lib/oauth.ts`** (new)
- `generateCodeVerifier(length)` — random base64url string
- `generateCodeChallenge(verifier)` — async SHA-256 via `crypto.subtle`
- `storeOAuthSession(provider, verifier)` / `getStoredProvider()` / `getStoredVerifier()` / `clearOAuthSession()` — sessionStorage helpers

**`frontend/app/auth/oauth/callback/page.tsx`** (new)
- Reads `code` + `state` from URL; `code_verifier` + `provider` from sessionStorage
- POSTs to `/auth/oauth/callback`; stores JWT pair; redirects to `/`
- Shows spinner while loading, error banner on failure

**`frontend/app/login/page.tsx`**
- Fetches `/auth/oauth/providers` on mount; shows SSO buttons only when providers are enabled
- `handleOAuthLogin(provider)` — generates verifier/challenge, fetches authorize URL, stores session, redirects
- Google SVG logo and Facebook SVG logo inline (no external assets)

**`frontend/lib/auth.ts`** — re-exports all PKCE helpers from `oauth.ts`

### Docs / config

- `docs/backend/oauth.md` (new) — full SSO documentation (flow diagram, endpoints, schema, edge cases, config)
- `mkdocs.yml` — "OAuth / SSO" added under Backend nav
- `mkdocs build` exits 0 ✓

### Migration

Migration ran successfully:
```
+ Added column: oauth_provider
+ Added column: oauth_sub
+ Added column: profile_picture_url
Migration complete
```
Verified idempotent (second run shows "already exists" for all 3 columns).

## Facebook bypass note

Facebook code is fully implemented but credentials in `backend/.env` are still
`<your-app-id>` / `<your-app-secret>`.  Since `facebook_app_id` is non-empty,
the Facebook button will render.  Clicking it will redirect to a broken Facebook
URL — the flow will complete once real credentials are added via:
```
FACEBOOK_APP_ID=<real-id>
FACEBOOK_APP_SECRET=<real-secret>
```

## Pending (user action required)

- [ ] Register `http://localhost:3000/auth/oauth/callback` as authorised
  redirect URI in Google Cloud Console (if not already done)
- [ ] Obtain real Facebook credentials and update `backend/.env`
- [ ] Run `./run.sh restart` to pick up new env vars / code

---

# Session: Feb 25, 2026 — Dashboard pagination, market filter, and pre-commit hook

## What We Built

Three UX improvements to the dashboard and a new pre-commit quality gate.

### 1. Home page market filter + pagination

- Added India 🇮🇳 / US 🇺🇸 `dbc.ButtonGroup` above the stock cards grid; defaults to India
- `_get_market(ticker)` helper: `.NS`/`.BO` → `'india'`, else `'us'`
- Rewrote `refresh_stock_cards` to output raw dicts to `dcc.Store(id="stock-raw-data-store")` instead of Dash components — enables client-side filter + paginate
- New `render_home_cards` callback: filters by market, paginates with configurable page size
- Page-size `dbc.Select` (10 / 25 / 50 / 100) next to each pagination row
- Count text ("Showing 1–12 of 47") shown left-aligned beside each paginator

### 2. Admin pagination + search

- Users table: `dcc.Store(id="users-store")` + `render_users_page` callback; `dbc.Input(id="users-search", debounce=True)` filters by name/email/role
- Audit log: `dcc.Store(id="audit-data-store")` + `render_audit_page` callback; search filters by event_type/actor/target/metadata
- Both tables use configurable page size; reset-to-page-1 callbacks fire on filter or size change

### 3. Pagination footer / FAB overlap fix

- Added `style={"paddingBottom": "5rem"}` (80 px) to `html.Div(id="page-content")` in `dashboard/app.py`
- Prevents the pagination rows from hiding behind the Next.js bottom-right navigation FAB and the Plotly watermark

### 4. Pre-commit hook (`hooks/pre-commit` + `hooks/pre_commit_checks.py`)

- **Bash entry point** (`hooks/pre-commit`): checks `SKIP_PRE_COMMIT=1`, validates `demoenv`, counts staged files, delegates to Python script
- **Python quality gate** (`hooks/pre_commit_checks.py`): `PreCommitChecker` class, 4 checks, operates only on `git diff --cached --name-only --diff-filter=ACM` (staged modified/created files)
  1. **Static analysis** — AST walk for bare `print()` (error/blocks), missing docstrings (warning), naming conventions (warning), mutable module-level globals (warning), XSS/SQL-injection patterns (error/blocks); auto-fix via Claude API when `ANTHROPIC_API_KEY` is set; fixed files re-staged
  2. **Meta-file freshness** — CLAUDE.md, PROGRESS.md, README.md reviewed against staged diff; auto-updated and re-staged if stale; single Claude call
  3. **Docs freshness** — `_DOCS_MAP` maps source files to `docs/` pages; affected pages patched via Claude; re-staged
  4. **Changelog order** — `docs/dev/changelog.md` H2 date headings verified descending; reordered automatically without any API call
- Environment variables: `SKIP_PRE_COMMIT=1` (bypass all), `SKIP_CLAUDE_CHECKS=1` (static-only, no API), `ANTHROPIC_API_KEY` (enables auto-fix + doc updates)
- Install: `cp hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit` (done this session)
- Installed and executable: `hooks/pre-commit`, `hooks/pre_commit_checks.py`

### Files changed

| File | Change |
|---|---|
| `dashboard/layouts.py` | Market filter buttons, pagination rows with count text + page-size select, search inputs, `dcc.Store` additions |
| `dashboard/callbacks.py` | `_get_market()`, rewritten `refresh_stock_cards`, 7 new callbacks (filter, render×3, reset×3) |
| `dashboard/app.py` | `paddingBottom: "5rem"` on `#page-content` |
| `hooks/pre-commit` | New — bash entry point for pre-commit quality gate |
| `hooks/pre_commit_checks.py` | New — full Python implementation (~400 lines) |

---

# Session: Feb 25, 2026 — Auth deployment fixes (JWT env propagation + dashboard dotenv)

## What We Fixed

Two runtime bugs that prevented the auth module from working end-to-end after first deploy.

### Bug 1 — `auth/dependencies.py` couldn't find `JWT_SECRET_KEY`

**Root cause:** `auth/dependencies.py` reads `JWT_SECRET_KEY` directly from `os.environ`.
Pydantic `Settings` reads `backend/.env` and populates its own model fields — but it does **not**
write those values back to `os.environ`.  So when uvicorn started the backend without `JWT_SECRET_KEY`
explicitly exported in the shell, every auth endpoint raised `ValueError: JWT_SECRET_KEY must be at
least 32 characters`.

**Fix:** Added 6 lines in `backend/main.py` (module-level startup block) that copy the three JWT
settings loaded by Pydantic into `os.environ` if they aren't already there:

```python
if settings.jwt_secret_key and "JWT_SECRET_KEY" not in os.environ:
    os.environ["JWT_SECRET_KEY"] = settings.jwt_secret_key
if "ACCESS_TOKEN_EXPIRE_MINUTES" not in os.environ:
    os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = str(settings.access_token_expire_minutes)
if "REFRESH_TOKEN_EXPIRE_DAYS" not in os.environ:
    os.environ["REFRESH_TOKEN_EXPIRE_DAYS"] = str(settings.refresh_token_expire_days)
```

### Bug 3 — Backend failed to start after `./run.sh restart` (GROQ_API_KEY not in os.environ)

**Root cause:** Same pattern as Bug 1, but for LangChain's `ChatGroq`.  `ChatGroq` reads
`GROQ_API_KEY` from `os.environ` directly.  The initial Bug 1 fix only exported the three JWT
variables, so `GROQ_API_KEY` (and `SERPAPI_API_KEY`, `ANTHROPIC_API_KEY`) remained invisible to
third-party libraries when running via `./run.sh` without explicit shell exports.

**Fix:** Replaced the three individual `if` statements with a single `_env_exports` dict that covers
all six settings fields (`GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `SERPAPI_API_KEY`, `JWT_SECRET_KEY`,
`ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`).  One loop handles all of them.  This
is now reflected in the PROGRESS.md and committed as a follow-up fix.

### Bug 2 — Dashboard iframe showed "Authentication required" even with a valid token

**Root cause:** The Dash dashboard is a **separate process** from the backend.  `dashboard/callbacks.py`
`_validate_token()` reads `JWT_SECRET_KEY` from `os.environ` directly.  The dashboard process
inherited only the shell's environment — it never loaded `backend/.env`, so `JWT_SECRET_KEY` was
always empty, and `_validate_token()` returned `None` for every token.

**Fix:** Added a `_load_dotenv()` helper in `dashboard/app.py` (executed at module import time, before
any Dash or callback imports) that reads both `<project-root>/.env` and `backend/.env` into
`os.environ`, using the same pattern as `scripts/seed_admin.py`.  Existing env vars are never
overwritten, so explicit shell exports still take precedence.

### Files changed

| File | Change |
|---|---|
| `backend/main.py` | Expanded env export block to cover all six settings fields (all API keys + JWT + token TTLs) |
| `dashboard/app.py` | Added `_load_dotenv()` helper + calls for `.env` and `backend/.env` |
| `backend/.env` | Created — contains all secrets and API keys (gitignored) |

### Superuser bootstrapped

- Email: `asequitytrading@gmail.com`
- Seeded via `python scripts/seed_admin.py` with `ADMIN_EMAIL` + `ADMIN_PASSWORD` passed as env vars
- `backend/.env` generated with a 64-char hex `JWT_SECRET_KEY`

---

# Session: Feb 24, 2026 — Auth Module Phase 1 (Iceberg Foundation)

## What We Built

Implemented Phase 1 of the authentication module: Apache Iceberg storage layer.

### Installed packages

Added to `demoenv` and frozen to `backend/requirements.txt`:
- `pyiceberg[sql-sqlite]` 0.10.0 — SqlCatalog + SQLite-backed local warehouse
- `python-jose[cryptography]` 3.5.0 — JWT (Phase 2)
- `passlib[bcrypt]` 1.7.4 + `bcrypt` 5.0.0 — password hashing (Phase 2)

### Files created

| File | Purpose |
|---|---|
| `.pyiceberg.yaml` | Iceberg catalog config (gitignored) — absolute warehouse path |
| `.pyiceberg.yaml.example` | Template committed to git |
| `auth/__init__.py` | Package init with module-level docstring |
| `auth/create_tables.py` | Idempotent one-time init script for `auth.users` + `auth.audit_log` |
| `auth/repository.py` | `IcebergUserRepository` — full CRUD + audit log |

### Files modified

- `.gitignore` — added `data/iceberg/` and `.pyiceberg.yaml`
- `backend/requirements.txt` — re-frozen with new packages

### Key technical notes

- PyIceberg 0.10 `table.append()` requires a `pa.Table`, not a `pa.RecordBatch`.
- `TimestampType` in PyIceberg maps to `pa.timestamp("us")` (microseconds, no timezone). Naive UTC datetimes must be passed — timezone-aware datetimes are normalised before storage.
- Timestamps are returned as timezone-aware UTC `datetime` objects to callers.
- Copy-on-write update pattern: read full table as pandas DataFrame, mutate, overwrite.
- Audit log is append-only; sorted newest-first in `list_audit_events()`.

### Smoke test results

All 7 repository operations verified: create, get_by_email, get_by_id, list_all, update, append_audit_event, delete (soft).

---

# Session: Feb 24, 2026 — Auth Module Phase 2 (AuthService + Models + Dependencies)

## What We Built

### Files created

| File | Purpose |
|---|---|
| `auth/models.py` | Pydantic request/response models for all auth + user endpoints |
| `auth/service.py` | `AuthService` — bcrypt hashing, JWT create/decode, deny-list |
| `auth/dependencies.py` | FastAPI dependency functions: `get_current_user`, `superuser_only` |

### Files modified

| File | Change |
|---|---|
| `backend/config.py` | Added `jwt_secret_key`, `access_token_expire_minutes`, `refresh_token_expire_days` to `Settings` |
| `backend/requirements.txt` | Re-frozen: `bcrypt==4.0.1`, `email-validator==2.3.0`, `dnspython` |

### Key technical notes

- `passlib 1.7.4` + `bcrypt 5.0` incompatible (bcrypt 5.0 rejects >72-byte passwords during passlib's detection routine). Downgraded to `bcrypt==4.0.1`.
- `EmailStr` in Pydantic v2 requires `email-validator` package — installed separately.
- `AuthService` is instantiated once per process (via `@lru_cache` in `dependencies.py`) so the in-memory refresh-token deny-list persists across requests.
- `_get_service()` reads `JWT_SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` from env vars — decoupled from `backend/config.py` so `auth/` can be imported independently of the backend.
- JWT payload: `sub`, `email`, `role`, `type` (`access`/`refresh`), `jti`, `iat`, `exp`.
- `superuser_only` composes on top of `get_current_user` — returns HTTP 403 if role ≠ `superuser`.

### Smoke test results

All tests passed: hash/verify, access token, refresh token, wrong-type rejection, deny-list revocation, password strength validation, Pydantic model construction, dependency module import.

---

# Session: Feb 24, 2026 — Auth Module Phase 3 (API Router + main.py wiring)

## What We Built

### Files created

| File | Purpose |
|---|---|
| `auth/api.py` | `create_auth_router()` — all 12 auth + user endpoints |

### Files modified

| File | Change |
|---|---|
| `backend/main.py` | Project root added to `sys.path`; `create_auth_router()` mounted via `app.include_router()` |
| `backend/requirements.txt` | Re-frozen: `python-multipart==0.0.20` (OAuth2 form support) |

### Endpoints added

| Method | Path | Auth |
|---|---|---|
| POST | `/auth/login` | Public |
| POST | `/auth/login/form` | Public (OAuth2 form for OpenAPI UI) |
| POST | `/auth/refresh` | Refresh token (rotates on use) |
| POST | `/auth/logout` | Access token |
| POST | `/auth/password-reset/request` | Access token (self only) |
| POST | `/auth/password-reset/confirm` | Access token (self only) |
| GET | `/users` | Superuser |
| POST | `/users` | Superuser |
| GET | `/users/{user_id}` | Superuser |
| PATCH | `/users/{user_id}` | Superuser |
| DELETE | `/users/{user_id}` | Superuser (soft delete; self-delete blocked) |
| GET | `/admin/audit-log` | Superuser |

### Key technical notes

- `python-multipart` required for `OAuth2PasswordRequestForm` (form endpoint).
- Refresh token rotation on every `/auth/refresh` call — old token is immediately revoked.
- Password reset token is returned in the response body for development; production should replace with email delivery.
- `create_auth_router()` is a factory (not a module-level router) so it can be called after `sys.path` is fully configured.
- `_get_repo()` uses `@lru_cache` for singleton — avoids repeated `os.chdir` calls.

### End-to-end test results

13/13 scenarios passed: login, wrong password → 401, create user, list users, get user, patch user, general user blocked → 403, token refresh, revoked refresh → 401, logout, delete user, audit log events.

---

# Session: Feb 25, 2026 — Auth Module Phase 6 (Prompt 7: Hardening + Docs)

## What We Built

Prompt 7 (Plan Phase 6): seed script, run.sh init, auth docs, mkdocs build.

### Files created

| File | Purpose |
|---|---|
| `scripts/seed_admin.py` | Idempotent superuser bootstrap from `ADMIN_EMAIL` + `ADMIN_PASSWORD` env vars; loads `.env` without extra deps |
| `docs/backend/auth.md` | Full MkDocs auth documentation page |

### Files modified

| File | Change |
|---|---|
| `run.sh` | Added `_init_auth()` function; called at start of `do_start()` — creates Iceberg tables + seeds admin on first run only |
| `mkdocs.yml` | Added "Auth & Users: backend/auth.md" to Backend nav section |

### Password strength validation

Already implemented in `auth/service.py` as `AuthService.validate_password_strength()` (min 8 chars, at least one digit) and called in `auth/api.py` for `POST /users` and `POST /auth/password-reset/confirm`. No change needed.

### Security checklist verified

- [x] `.env` in `.gitignore`
- [x] `data/iceberg/` in `.gitignore`
- [x] JWT secret min-32-char guard in `AuthService.__init__`
- [x] No plaintext passwords in any log format strings
- [x] All admin endpoints use `superuser_only` dependency
- [x] Password reset tokens single-use + 30-minute expiry
- [x] `mkdocs build` passes — documentation built in 0.86 seconds

### `scripts/seed_admin.py` features

- Loads `.env` from project root + `backend/.env` without requiring `python-dotenv`
- Validates ADMIN_EMAIL, ADMIN_PASSWORD, JWT_SECRET_KEY before any DB operations
- Validates password strength (min 8 chars, at least one digit)
- Validates JWT_SECRET_KEY length (≥ 32 chars)
- Idempotent: exits 0 with info message if user already exists
- Logs all actions via `logging` (no bare `print()`)
- Full module + function docstrings (pre-push hook compliant)

### `run.sh` `_init_auth()` behaviour

- Triggered by `./run.sh start` only when `data/iceberg/catalog.db` does not exist
- Requires `JWT_SECRET_KEY` (exits 1 with instructions if missing)
- Creates tables via `auth/create_tables.py`
- Seeds admin via `scripts/seed_admin.py` if `ADMIN_EMAIL` + `ADMIN_PASSWORD` are set
- Warns (does not fail) if admin env vars are absent

## What's Next

Auth module is **complete** (Phases 1–6).

### End-to-end verification checklist (manual, requires services running)

- [ ] `python auth/create_tables.py` — creates both Iceberg tables
- [ ] `python scripts/seed_admin.py` — creates initial superuser
- [ ] `POST /auth/login` — returns JWT pair for valid credentials
- [ ] `GET /users` — returns 403 for a general user token
- [ ] Next.js `/login` page — authenticates and redirects to chat
- [ ] Unauthenticated `/` — redirects to `/login`
- [ ] Dash dashboard — shows unauthenticated notice without token
- [ ] `/admin/users` — shows 403 notice for general user, full UI for superuser
- [ ] Add user modal — creates user, table refreshes
- [ ] Deactivate button — soft-deletes user, status badge updates
- [ ] Audit log tab — shows all events
- [ ] Change Password modal — updates password via reset flow
- [ ] `mkdocs build` passes ✓

---

# Session: Feb 25, 2026 — Auth Module Phase 5 + Prompt 6 (Admin UI)

## What We Built

Implemented Prompt 6 (Plan Phase 5): `/admin/users` Dash page with full user
management, plus the global Change Password modal accessible from the NAVBAR.

### Files modified

| File | Change |
|---|---|
| `dashboard/layouts.py` | Added `admin_users_layout()` (two-tab admin page); updated NAVBAR with "Admin" link + "Change Password" button |
| `dashboard/callbacks.py` | Added `_admin_forbidden()`, `_resolve_token()`, `_api_call()`, `_build_users_table()`, `_build_audit_table()` helpers; added 7 new callbacks (load_users_table, load_audit_log, toggle_user_modal, save_user, toggle_user_activation, toggle_change_password_modal, save_new_password); added `ALL` to top-level import |
| `dashboard/app.py` | Imported `_admin_forbidden` + `admin_users_layout`; added `/admin/users` route with superuser role check; added global change-password modal to `app.layout` |
| `frontend/app/page.tsx` | Added `"admin"` to `View` type; added Admin nav item (superuserOnly) to NAV_ITEMS; updated `iframeSrc` to handle admin view (uses `/admin/users` default URL with token); filtered NAV_ITEMS by `getRoleFromToken()`; updated breadcrumb + iframe title for admin view; imported `getRoleFromToken` |

### Key features delivered

- **Users tab**: DataTable of all accounts (name, email, role badge, status badge, created, last login) with per-row Edit and Deactivate/Reactivate buttons
- **Add/Edit modal**: full form (name, email, password for new users, role, is-active toggle for edits); inline error messages; calls `POST /users` or `PATCH /users/{id}`
- **Deactivate/Reactivate**: single-click toggle; calls `DELETE /users/{id}` (deactivate) or `PATCH /users/{id}` with `is_active: true` (reactivate)
- **Audit Log tab**: full audit event table (timestamp, event type, actor, target, metadata)
- **Change Password modal**: global, accessible from NAVBAR; validates locally; calls `POST /auth/password-reset/request` + `POST /auth/password-reset/confirm`
- **Access control**: Admin page shows 403 notice for non-superusers; Admin nav item only visible for superusers in the Next.js frontend
- **Token propagation**: admin iframe (`/admin/users`) gets `?token=<jwt>` automatically, same as the dashboard

### Verification

- `python -c "import dashboard.app"` — no errors ✓
- `npx tsc --noEmit` — 0 TypeScript errors ✓
- No bare `print()` in dashboard/ ✓

## What's Next (Prompt 7)

Phase 6: `scripts/seed_admin.py`, password strength validation in `auth/api.py`, security hardening checklist, docs update (`docs/backend/auth.md`), MkDocs build.

---

# Session: Feb 25, 2026 — Auth Module Phase 4 (Next.js Frontend Auth)

## What We Built

### Files created

| File | Purpose |
|---|---|
| `frontend/lib/auth.ts` | Token helpers: `getAccessToken`, `setTokens`, `clearTokens`, `isTokenExpired`, `getRoleFromToken`, `refreshAccessToken` |
| `frontend/lib/apiFetch.ts` | Authenticated fetch wrapper — injects Bearer token, auto-refreshes on expiry, redirects to `/login` on 401 |
| `frontend/app/login/page.tsx` | Login page — email + password form, stores tokens on success, redirects to `/` |

### Files modified

| File | Change |
|---|---|
| `frontend/app/page.tsx` | Auth guard on mount (redirects to `/login` if no valid token); `fetch → apiFetch` in `sendMessage`; logout button in header |

### Key design decisions

- JWT payload decoded client-side (no library) — only reads `exp` claim for expiry check with 30s clock-skew buffer.
- `refreshAccessToken` uses token rotation — stores new access + refresh tokens on success, clears tokens on failure.
- `apiFetch` is a drop-in replacement for `fetch` — same signature, returns `Response`.
- Logout: `clearTokens()` + `router.replace("/login")` — no server call needed for the access token (short TTL); the refresh token's deny-list entry is not written (acceptable: call `/auth/logout` from a dedicated profile page in a future phase).
- Login page redirects away immediately if a valid token already exists (covers browser back-button scenarios).
- Generic error message on login failure: "Invalid email or password" — never reveals which field was wrong.

### TypeScript / lint status

`npx tsc --noEmit` — 0 errors. ESLint — 0 errors, 1 pre-existing warning (unrelated to this work).

## What's Next (Prompt 5)

Dash token store, `_validate_token()` in `dashboard/callbacks.py`, callback guards, token propagation via `?token=` query param.

---

# Session: Feb 24, 2026 — Streaming, timeout, iframe cross-origin, dashboard theme

## What We Built

Four independent improvements to UX, reliability, and visual consistency.

### Task 1 — Dashboard theme: dark → light

- Changed `dbc.themes.DARKLY` → `dbc.themes.FLATLY` in `dashboard/app.py`.
- Rewrote `dashboard/assets/custom.css` with a light palette (`--bg: #f9fafb`, `--card-bg: #ffffff`, `--accent: #4f46e5`). All dark card/navbar/slider colors replaced with light-theme equivalents.
- Updated all Plotly chart templates from `"plotly_dark"` → `"plotly_white"` in `dashboard/callbacks.py`. Added explicit `paper_bgcolor="#ffffff"`, `plot_bgcolor="#f9fafb"`, `font=dict(color="#111827")`, `gridcolor="#e5e7eb"` on all figures.
- Replaced white annotation colors (`rgba(255,255,255,...)`) with dark (`rgba(0,0,0,...)`) equivalents in forecast figure. Updated price-target annotation colors from neon to amber/orange/red for light background contrast.
- Changed NAVBAR to `color="light"`, `dark=False`. Removed `text-white` from H2, removed `bg-dark text-white` from search input, changed controls rows from `bg-dark` → `bg-light border`. Updated loading spinner color to `#4f46e5`.
- Changed metrics table from `table-dark` → plain Bootstrap table class.
- Changed stock card current-price text from `text-white` → `text-dark`.

### Task 2 — Iframe cross-origin headers

- Added `@server.after_request` hook in `dashboard/app.py` that sets `X-Frame-Options: ALLOWALL` and `Content-Security-Policy: frame-ancestors *` on every response.

### Task 3 — Request timeout

- Added `agent_timeout_seconds: int = 120` field to `backend/config.py` `Settings`.
- Updated `POST /chat` handler in `main.py` to run `agent.run()` via `asyncio.run_in_executor` wrapped in `asyncio.wait_for(timeout=...)`. Returns HTTP 504 on timeout.

### Task 4 — Streaming via NDJSON

- Added `stream()` method to `BaseAgent` in `backend/agents/base.py`. Yields one JSON event per line: `thinking`, `tool_start`, `tool_done`, `warning`, `final`, `error`.
- Added `POST /chat/stream` endpoint in `main.py`. Runs `agent.stream()` in a daemon thread, passes events through a `queue.Queue`, applies timeout via queue `.get(timeout=...)`. Returns `StreamingResponse(media_type="application/x-ndjson")`.
- Updated `frontend/app/page.tsx`:
  - Replaced `axios.post` with native `fetch()` + `ReadableStream` line-by-line parsing.
  - Added `statusLine` state. `StatusBadge` component (pulsing dot + status text) replaces `TypingDots`.
  - Status text updates with each stream event: "Thinking..." → "Fetching stock data..." → "Got result..." → (final message appears).
  - Added `iframeLoading` and `iframeError` state on `<iframe>`. Shows spinner overlay on load, error banner with "Open in new tab ↗" on failure.
  - `switchView()` resets `iframeLoading=true` and `iframeError=false`.
  - Added "Open in new tab ↗" button in header whenever `view !== "chat"`.
  - Removed `axios` import (no longer used in chat path).

## Files Changed

| File | What changed |
|------|-------------|
| `backend/config.py` | Added `agent_timeout_seconds` field |
| `backend/agents/base.py` | Added `stream()` method + `json`, `Iterator` imports |
| `backend/main.py` | Added `asyncio`/`queue`/`threading`/`StreamingResponse` imports; timeout on `/chat`; new `/chat/stream` endpoint |
| `dashboard/app.py` | FLATLY theme, `allow_iframe` after_request hook |
| `dashboard/assets/custom.css` | Complete rewrite for light palette |
| `dashboard/callbacks.py` | All chart templates → `plotly_white`; explicit light colors; table class; stock card text class |
| `dashboard/layouts.py` | NAVBAR light; H2 no text-white; input no bg-dark; controls rows bg-light |
| `frontend/app/page.tsx` | Streaming fetch; StatusBadge; iframe loading/error states; "Open in new tab" button |

## Commit

`be09863` — feat: streaming, request timeout, iframe cross-origin, and dashboard light theme

---

# Session: Feb 24, 2026 — Dynamic currency symbols for multi-market stocks

## What We Built

Replaced all hard-coded `$` currency symbols with dynamic symbols loaded from
`data/metadata/{TICKER}_info.json`. This fixes Indian stocks (RELIANCE.NS, TCS.NS,
INFY.NS etc.) which previously displayed `$` instead of `₹`.

**Infrastructure already in place:** `stock_data_tool.get_stock_info()` already
stores `"currency": info.get("currency", "USD")` in each ticker's metadata JSON.
yfinance returns `"INR"` for NSE-listed stocks.

### What changed

Added a shared `_currency_symbol(code) -> str` + `_load_currency(ticker) -> str`
helper pair to three backend tool modules and a `_currency_symbol()` /
`_get_currency(ticker)` pair to the dashboard callbacks.

Currency mapping: `USD→$`, `INR→₹`, `GBP→£`, `EUR→€`, `JPY/CNY→¥`, `AUD→A$`,
`CAD→CA$`, `HKD→HK$`, `SGD→S$`. Unmapped codes fall back to the code itself.

## Files Changed

| File | What changed |
|------|-------------|
| `backend/tools/price_analysis_tool.py` | `import json`, `_DATA_METADATA` path; `_currency_symbol()` + `_load_currency()`; 5 report `$` → `{sym}` |
| `backend/tools/forecasting_tool.py` | Same helpers; 2 chart annotation `$` → `{sym}`; 5 report `$` → `{sym}`; `yaxis_title` now uses actual currency code |
| `backend/tools/stock_data_tool.py` | Same helpers; dividend report `$` → dynamic symbol |
| `dashboard/callbacks.py` | `_currency_symbol()` + `_get_currency()` helpers; all price displays in stat cards, target cards, accuracy row, forecast chart, home cards now dynamic; `_build_target_cards` and `_build_accuracy_row` gained `ticker` param |
| `docs/dev/changelog.md` | New session entry |
| `PROGRESS.md` | This entry |

## Commit

`5c017f2` — fix: dynamic currency symbols for multi-market stocks

---

# Session: Feb 24, 2026 — SPA navigation and internal link routing

## What We Built

Three frontend follow-ups after the hardening session.

### Fix 1 — Menu repositioned to bottom-right

- Changed outer container from `fixed bottom-6 left-6` → `fixed bottom-6 right-6`; popup anchor from `left-0` → `right-0` to avoid overlap with the Next.js dev watermark badge.

### Fix 2 — SPA navigation (view state + iframes)

- Added `type View = "chat" | "docs" | "dashboard"` and `const [view, setView] = useState<View>("chat")`.
- Replaced `<a target="_blank">` menu links with `<button>` elements calling `switchView(v)`.
- When `view === "chat"`: renders full chat UI (scrollable `<main>` + `<footer>`).
- When `view === "docs"` or `view === "dashboard"`: renders a full-height `<iframe>` pointed at the service URL; chat state is preserved because the component stays mounted.
- `switchView(v)` sets `view`, resets `iframeUrl` to `null`, and closes the menu.
- Menu items highlight the active view (indigo background + small dot indicator).
- Header adapts: agent selector shown only on chat view; other views show a text breadcrumb.
- Added `NAV_ITEMS` constant (Chat, Docs, Dashboard) with SVG icons, replacing the inline `<a>` elements.

### Fix 3 — Internal links from chat messages use view state

- Added `onInternalLink: (href: string) => void` prop to `MarkdownContent`.
- The custom `a` renderer checks if `href` starts with `NEXT_PUBLIC_DASHBOARD_URL` or `NEXT_PUBLIC_DOCS_URL`; if so renders a `<button onClick={() => onInternalLink(href)}>` instead of `<a target="_blank">`.
- `handleInternalLink(href)` in `ChatPage` sets `view` and `iframeUrl` to the exact URL (e.g. `…/analysis?ticker=AAPL`) so the iframe loads the right page directly.
- External links (news, Wikipedia, etc.) still open in a new tab.
- `iframeUrl` is reset to `null` when the user navigates via the menu (so the menu always opens the homepage of each service).

---

# Session: Feb 24, 2026 — UI/backend hardening

## What We Built

Four independent improvements across frontend and backend.

### Task 1 — Backend URL → `.env.local`

- Created `frontend/.env.local` and `frontend/.env.local.example` with `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_DASHBOARD_URL`, `NEXT_PUBLIC_DOCS_URL`.
- Updated `frontend/app/page.tsx` to use `` `${process.env.NEXT_PUBLIC_BACKEND_URL}/chat` `` with a fallback.
- Added `!.env.local.example` negation to `frontend/.gitignore` so the example file is committable.

### Task 2 — Agentic loop iteration cap

- Added `MAX_ITERATIONS: int = 15` module-level constant in `backend/agents/base.py`.
- Added guard at the top of the `while True:` loop: logs `WARNING` and breaks if `iteration > MAX_ITERATIONS`.

### Task 3 — Session persistence via localStorage

- Added load-on-mount `useEffect` in `page.tsx` that reads `chat_histories` from localStorage and revives `Date` objects via `new Date(m.timestamp)`.
- Added save-on-change `useEffect` that writes `histories` to localStorage on every state update.

### Task 4 — Navigation menu + path replacement

- Added `menuOpen` state, `menuRef`, and click-outside handler.
- Added fixed bottom-right FAB button (grid icon) that opens a popup with Chat / Docs / Dashboard items.
- Added `preprocessContent()` that replaces absolute chart paths with clickable dashboard links and strips raw data file paths before markdown rendering.

---

# Session: Feb 23, 2026 (continued) — Per-agent history, cache, and news tool

## What We Built

Four independent improvements across frontend and backend.

### Task 1 — Backend URL → `.env.local`

- Created `frontend/.env.local` and `frontend/.env.local.example` with `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_DASHBOARD_URL`, `NEXT_PUBLIC_DOCS_URL`.
- Updated `frontend/app/page.tsx` line ~144 to use `` `${process.env.NEXT_PUBLIC_BACKEND_URL}/chat` `` with a fallback.
- Added `!.env.local.example` negation to `frontend/.gitignore` so the example file is committable.

### Task 2 — Agentic loop iteration cap

- Added `MAX_ITERATIONS: int = 15` module-level constant in `backend/agents/base.py`.
- Added guard at the top of the `while True:` loop: logs `WARNING` and breaks if `iteration > MAX_ITERATIONS`.

### Task 3 — Session persistence via localStorage

- Added load-on-mount `useEffect` in `page.tsx` that reads `chat_histories` from localStorage and revives `Date` objects via `new Date(m.timestamp)`.
- Added save-on-change `useEffect` that writes `histories` to localStorage on every state update.

### Task 4 — Bottom-left navigation menu + path replacement

- Added `menuOpen` state, `menuRef`, and click-outside handler in `page.tsx`.
- Added fixed bottom-left FAB button (grid icon) that opens a popup with Docs and Dashboard links (using `NEXT_PUBLIC_DOCS_URL` / `NEXT_PUBLIC_DASHBOARD_URL`).
- Added `preprocessContent()` function that replaces absolute chart paths with clickable dashboard links and strips raw data file paths before markdown rendering.

---

# Session: Feb 23, 2026 (continued) — Per-agent history, cache, and news tool

## What We Built

Three independent improvements implemented and pushed as commit `895df0f`.

### Task 1 — Agent-to-agent news lookup (`search_market_news`)

**New file:** `backend/tools/agent_tool.py`
- `create_search_market_news_tool(general_agent)` factory creates a `@tool`-decorated function that delegates to `general_agent.run()`.
- The stock agent calls this tool in step 5 of its pipeline to enrich analysis reports with live news.

**Modified:**
- `backend/main.py` — registers `search_market_news` tool between general and stock agent construction (dependency ordering).
- `backend/agents/stock_agent.py` — added `"search_market_news"` to `tool_names`; updated system prompt to call it before finalising each report.

### Task 2 — Per-agent chat history

**Modified:** `frontend/app/page.tsx`
- Replaced `const [messages, setMessages] = useState<Message[]>([])` with `histories: Record<string, Message[]>` keyed by `agentId`.
- Added scoped `setMessages` helper that writes only to `histories[agentId]`.
- Removed `setMessages([])` from agent switch button — switching agents now preserves both conversations.

### Task 3 — Same-day analysis/forecast cache

**Modified:** `backend/tools/price_analysis_tool.py`, `backend/tools/forecasting_tool.py`
- Added `_CACHE_DIR = data/cache/`, `_load_cache(ticker, key)`, `_save_cache(ticker, key, result)` helpers to both files.
- `analyse_stock_price` checks `data/cache/{TICKER}_analysis_{YYYY-MM-DD}.txt` on every call; returns instantly on cache hit.
- `forecast_stock` checks `data/cache/{TICKER}_forecast_{N}m_{YYYY-MM-DD}.txt` similarly.
- `.gitignore` — added `data/cache/`.

### Commit

| Hash | Message |
|------|---------|
| `895df0f` | feat: per-agent history, analysis cache, and market news tool |

---

# Session: Feb 23, 2026 (continued) — Plotly Dash Dashboard

## What We Built — Phase 8 Dashboard

Completed and committed the Plotly Dash web dashboard (`dashboard/`), which was written but not yet committed.

### Files Added / Changed

| File | Description |
|------|-------------|
| `dashboard/__init__.py` | Package init with module docstring |
| `dashboard/app.py` | Entry point — Dash app, DARKLY theme, routing callback, `server` for gunicorn |
| `dashboard/layouts.py` | Four page-layout factories (`home_layout`, `analysis_layout`, `forecast_layout`, `compare_layout`) + `NAVBAR` |
| `dashboard/callbacks.py` | All callbacks — stock cards, analysis chart, forecast chart, compare; Run New Analysis pipeline |
| `dashboard/assets/custom.css` | Dark theme overrides on top of DARKLY |
| `run_dashboard.sh` | Convenience launcher (activates demoenv, runs `dashboard/app.py`) |
| `CLAUDE.md` | Added Dashboard Details section, How to Run entry, updated project tree |

### Code Fix

- Removed redundant `import_dbc_row()` wrapper in `callbacks.py`; inlined `dbc.Row(cols)` directly in `_build_stats_cards`.

### Architecture Decisions

- **Direct parquet reads** — dashboard reads `data/raw/*.parquet` and `data/forecasts/*.parquet` directly; no HTTP call to the FastAPI backend.
- **"Run New Analysis" imports backend tools directly** — `callbacks.py` imports `backend.tools.forecasting_tool` private helpers via `sys.path` insertion done in `app.py`; this avoids duplicating the Prophet pipeline.
- **`dcc.Store` for cross-page ticker** — `nav-ticker-store` carries the selected ticker from the Home page search/dropdown to the Analysis and Forecast dropdowns.
- **`suppress_callback_exceptions=True`** — required because dropdowns and charts only exist in the DOM once their page is rendered.
- **DARKLY theme + custom.css** — `dbc.themes.DARKLY` provides the base dark theme; `assets/custom.css` overrides card backgrounds, slider colours, and table styles.

### Verified

| Check | Result |
|-------|--------|
| Import check (`python -c "import dashboard.app"`) | ✅ No errors |
| HTTP 200 on `/` | ✅ |
| Dashboard layout JSON valid | ✅ |
| No bare `print()` calls | ✅ |
| Module docstrings present | ✅ |

---

# Session: Feb 23, 2026

## What We Built Today — Stock Analysis Agent

Added a full stock analysis capability to the existing agentic chat app, following Option B (fit existing standards): all new code integrates into the existing `BaseAgent` / `ToolRegistry` / `AgentRegistry` framework rather than being standalone.

### New Files Created

| File | Description |
|---|---|
| `backend/agents/stock_agent.py` | `StockAgent(BaseAgent)` + `create_stock_agent()` factory |
| `backend/tools/stock_data_tool.py` | 6 `@tool` functions — Yahoo Finance delta fetch + parquet storage |
| `backend/tools/price_analysis_tool.py` | 1 `@tool` — technical indicators + 3-panel Plotly chart |
| `backend/tools/forecasting_tool.py` | 1 `@tool` — Prophet forecast + confidence chart |
| `docs/stock_agent.md` | Full MkDocs documentation page for the stock agent |
| `data/metadata/stock_registry.json` | Live registry — 5 stocks fetched (AAPL, TSLA, RELIANCE.NS, MSFT, GOOGL) |
| `data/raw/*.parquet` | 5 OHLCV parquet files (~130KB each, gitignored) |
| `data/forecasts/*.parquet` | 6 forecast files (gitignored) |
| `charts/analysis/*.html` | 5 interactive analysis charts (~5.4MB each, gitignored) |
| `charts/forecasts/*.html` | 6 interactive forecast charts (~4.8MB each, gitignored) |

### Files Modified

| File | Change |
|---|---|
| `backend/agents/base.py` | Added `SystemMessage` import; `_build_messages()` now prepends system prompt when set |
| `backend/main.py` | Registered 8 stock tools and `StockAgent` in `ChatServer` |
| `frontend/app/page.tsx` | Added agent selector toggle (General / Stock Analysis); `agent_id` passed in requests |
| `.gitignore` | Added `data/raw/`, `data/processed/`, `data/forecasts/`, `charts/analysis/`, `charts/forecasts/`, `site/` |
| `mkdocs.yml` | Added Stock Agent nav section |
| `backend/requirements.txt` | Frozen with 10 new stock agent dependencies |
| `CLAUDE.md` | Updated project structure, backend details, dependencies, run commands |

### Architecture Decisions

- **Option B adopted** — stock agent extends `BaseAgent`, tools are `@tool` functions registered in `ToolRegistry`, routing via `agent_id="stock"` on `POST /chat`. No pattern matching.
- **`src/` folder not created** — all Python code stays in `backend/agents/` and `backend/tools/`.
- **`StockAgent` is not an orchestrator** — the LLM drives the pipeline via the system prompt.
- **Tools return strings** — all `@tool` functions return formatted strings; DataFrames are stored to parquet and loaded internally by subsequent tools.
- **Delta fetching** — `fetch_stock_data` checks `stock_registry.json` before fetching; only missing date range is downloaded on subsequent calls.
- **pyarrow pinned to <18** — pyarrow 21.x has no pre-built wheel for Python 3.9 on macOS x86_64; pinned at 17.0.0.
- **Plotly 6.x `add_vline` workaround** — replaced with `add_shape` + `add_annotation` due to a datetime axis incompatibility in Plotly 6.

### Test Results

| Test | Result |
|---|---|
| Data fetcher (4 tickers) | All pass — AAPL, TSLA, RELIANCE.NS, MSFT, GOOGL fetched |
| Delta fetch | Correctly skips on same-day re-fetch |
| Price analysis (5 tickers) | All pass — indicators, drawdown, Sharpe, charts |
| Forecasting (3 tickers) | All pass — Prophet trained, targets at 3/6/9M, MAPE <11% for AAPL/TSLA/MSFT |
| Chat interface (6 messages) | 6/6 pass — routing, content, error handling, general agent unchanged |

---

# Session: Feb 22, 2026

## What We Did Today

### 1. OOP Backend Refactor — agents/ and tools/ packages

Deleted `backend/agent.py` and split its responsibilities across a proper package structure:

**`backend/agents/`**
- `base.py` — `AgentConfig` dataclass + `BaseAgent` ABC; the full agentic loop lives here (`run()` method); subclasses only implement `_build_llm()`
- `registry.py` — `AgentRegistry` maps `agent_id` strings to `BaseAgent` instances; used by the HTTP layer for routing
- `general_agent.py` — `GeneralAgent(BaseAgent)` backed by Groq; `create_general_agent(tool_registry)` factory function

**`backend/tools/`**
- `registry.py` — `ToolRegistry` maps tool name strings to LangChain `BaseTool` instances; provides `register`, `get_tools`, `invoke`, `list_names`
- `time_tool.py` — `get_current_time` `@tool`
- `search_tool.py` — `search_web` `@tool` (now wrapped in try/except — see item 3)

### 2. Rewrote backend/main.py with ChatServer class

- All server state (registries, app) encapsulated in `ChatServer`; no more module-level globals
- `POST /chat` now accepts optional `agent_id` (default `"general"`) and echoes it in the response
- Added `GET /agents` endpoint — returns list of registered agents with id, name, description
- Replaced bare `except` returning error strings in `200` body with proper `HTTPException` `404`/`500`

### 3. Fixed search_web error handling (was a listed TODO)

- Wrapped `SerpAPIWrapper().run(query)` in try/except
- On failure returns `"Search failed: <reason>"` as a `ToolMessage` so the LLM can recover gracefully instead of raising an unhandled exception

### 4. Added backend/config.py

- `Settings(BaseSettings)` with fields: `groq_api_key`, `anthropic_api_key`, `serpapi_api_key`, `log_level`, `log_to_file`
- Reads from env vars; also reads `backend/.env` if present
- `get_settings()` cached with `@lru_cache`

### 5. Added backend/logging_config.py

- `setup_logging(level, log_to_file, log_dir)` configures the root logger
- Always adds a stdout console handler
- Optionally adds a `TimedRotatingFileHandler` → `backend/logs/agent.log` (daily rotation, 7-day retention)
- `logs/` added to `.gitignore`

### 6. Added Google-style Sphinx docstrings

- All backend Python files have module-level, class-level, and method-level docstrings

### 7. Python 3.9 annotation compatibility fix

- `X | Y` union syntax (PEP 604, Python 3.10+) replaced with `Optional[X]` from `typing` throughout — `demoenv` runs Python 3.9.13

### 8. Updated CLAUDE.md

- Replaced old flat backend tree with new package layout
- Rewrote Backend Details section (removed `agent.py`, rewrote `main.py`, added subsections for all new modules)
- Updated "Switching back to Claude" to reference `agents/general_agent.py` → `_build_llm()`
- Added 7 new Decisions Made entries
- Removed fixed TODO (search_web error handling)

### 9. Committed and pushed (commit fa20966)

```
refactor: OOP backend restructure with agents/, tools/ packages and structured logging
```
14 files changed, 1,191 insertions, 127 deletions

### 10. MkDocs documentation site

- Installed `mkdocs==1.6.1` and `mkdocs-material==9.7.2` into `demoenv`
- Created `mkdocs.yml` — material theme (indigo, light/dark toggle), nav tabs, code copy, pymdownx extensions
- Populated all 11 docs pages from scratch based on a full codebase read-and-analyse pass:

| Page | Content |
|------|---------|
| `docs/index.md` | Stack, end-to-end flow, quick start, layout |
| `docs/backend/overview.md` | Module map, startup sequence, agentic loop, extension guide |
| `docs/backend/api.md` | Both endpoints, JSON shapes, error codes, curl examples |
| `docs/backend/agents.md` | AgentConfig, BaseAgent loop, AgentRegistry, GeneralAgent, Claude switch |
| `docs/backend/tools.md` | ToolRegistry, both tools, invoke() comparison, how to add tools |
| `docs/backend/config.md` | Settings fields, priority order, .env usage, lru_cache |
| `docs/backend/logging.md` | Format, handlers, hot-reload safety, what-gets-logged table |
| `docs/frontend/overview.md` | Component, state, send flow, UI layout, limitations |
| `docs/dev/how-to-run.md` | Prerequisites, backend + frontend setup, verification, Claude switch |
| `docs/dev/decisions.md` | Full rationale for every architectural and tooling decision |
| `docs/dev/changelog.md` | Session log with commit hashes, pending-issues table |

- Committed and pushed (commit f7f1cbc)
```
docs: add MkDocs site with full project documentation
```
13 files changed, 1,951 insertions

### 11. Pre-push quality gate (hooks/pre-push)

Added a git pre-push hook that enforces 5 mandatory steps before every push to `main`:

| Check | Mechanism | On failure |
|-------|-----------|------------|
| No bare `print()` in backend Python | AST walk via `demoenv/bin/python` | Hard block (exit 1) |
| Module-level docstrings on non-`__init__.py` files | AST walk | Warning only (exit 0) |
| `mkdocs build` passes | Runs in `demoenv` | Hard block (exit 1) |
| OOP architecture / standard practices | CLAUDE.md checklist (manual) | — |
| CLAUDE.md + PROGRESS.md + docs updated | CLAUDE.md checklist (manual) | — |

- Hook saved to `hooks/pre-push` (tracked by git — source of truth)
- Install: `cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`
- Uses AST parsing (not `grep`) so `print()` inside docstring examples is not flagged
- Only activates on pushes targeting `refs/heads/main`; other branches pass through
- Added "Pre-Push Checklist" section to CLAUDE.md with the full 5-step gate

---

## Current State of the Codebase

| Component | Status |
|-----------|--------|
| FastAPI backend (`main.py`) | ✅ `ChatServer` class — `/chat` + `/agents` endpoints |
| Agentic loop (`agents/base.py`) | ✅ `BaseAgent.run()` — proper multi-turn tool loop |
| Agent routing | ✅ `AgentRegistry` — dispatch by `agent_id` |
| Tool registry | ✅ `ToolRegistry` — decoupled from agent code |
| LLM | ⚠️ Groq `openai/gpt-oss-120b` (temporary — Claude Sonnet 4.6 intended) |
| `get_current_time` tool | ✅ Working |
| `search_web` tool | ✅ SerpAPI with try/except error handling |
| Structured logging | ✅ Console + rotating file (`logs/agent.log`) |
| Config / env vars | ✅ Pydantic Settings with `.env` support |
| Frontend chat UI | ✅ Unchanged — working |
| Multi-turn history | ✅ Working |
| Documentation | ✅ MkDocs site — 11 pages, material theme (`mkdocs serve`) |
| Pre-push hook | ✅ `hooks/pre-push` — print() + docstring + mkdocs checks |
| Git + GitHub | ✅ Clean — 7 commits pushed |

---

## What's Pending

### High Priority
- **Fix Anthropic API access** — once resolved, swap back to Claude (2-line change in `agents/general_agent.py` → `_build_llm()`):
  - Change import to `from langchain_anthropic import ChatAnthropic`
  - Change return to `return ChatAnthropic(model=self.config.model, temperature=self.config.temperature)`
  - Update `model` field in `create_general_agent()` to `"claude-sonnet-4-6"`
  - Set `ANTHROPIC_API_KEY` instead of `GROQ_API_KEY`
- **Set `SERPAPI_API_KEY`** — sign up at serpapi.com, get key, export before running backend

### Nice to Have
- **Streaming responses** — backend waits for full agentic loop; SSE or WebSockets would improve perceived speed
- **Move backend URL to env var** — `http://127.0.0.1:8181` hardcoded in `frontend/app/page.tsx`; move to `.env.local`
- **Session persistence** — history lost on page refresh (React state only)
- **Add more agents** — registry supports multiple agents; register additional `BaseAgent` subclasses in `ChatServer._register_agents()`
- **Add more tools** — register additional `@tool` functions in `ChatServer._register_tools()`
- **Agentic loop iteration cap** — no `max_iterations` guard; a misbehaving LLM could loop forever

---

## Environment Variables Required to Run

```bash
# Backend
export GROQ_API_KEY=...          # current (Groq)
export SERPAPI_API_KEY=...       # for search_web tool

# Optional
export LOG_LEVEL=DEBUG           # default: DEBUG
export LOG_TO_FILE=true          # default: true  (writes to backend/logs/agent.log)

# When switching back to Claude:
export ANTHROPIC_API_KEY=...     # replaces GROQ_API_KEY
```

## How to Run

```bash
# Backend
cd backend
source demoenv/bin/activate
uvicorn main:app --port 8181 --reload

# Frontend (separate terminal)
cd frontend
npm run dev
# → http://localhost:3000
```

## Git Log

| Commit | Message |
|--------|---------|
| `6604b74` | Initial commit: agentic chat app with Claude Sonnet 4.6 |
| `ee7967f` | chore: swap LLM back to Groq (openai/gpt-oss-120b) for testing |
| `ef643f7` | feat: implement search_web tool with SerpAPI (real Google results) |
| `89d7eb4` | docs: update CLAUDE.md and add PROGRESS.md session log |
| `fa20966` | refactor: OOP backend restructure with agents/, tools/ packages and structured logging |
| `f7f1cbc` | docs: add MkDocs site with full project documentation |
| `58bdd6a` | docs: update CLAUDE.md and PROGRESS.md with MkDocs session |

---

# Session: Feb 21, 2026

---

## What We Did Today

### 1. Migrated LLM from Groq → Claude Sonnet 4.6
- Replaced `langchain_groq.ChatGroq` with `langchain_anthropic.ChatAnthropic`
- Model set to `claude-sonnet-4-6`

### 2. Fixed the Agentic Loop (Critical Bug)
- The original loop called one tool and returned immediately — it never sent the tool result back to the model
- Rewrote `run_agent()` as a proper while loop: invokes model → executes all tool calls → feeds `ToolMessage` results back → repeats until no more tool calls → returns final response

### 3. Added Multi-Turn Conversation Support
- `main.py`: added `history: list[dict] = []` field to `ChatRequest`
- `agent.py`: converts history dicts to `HumanMessage` / `AIMessage` objects before the loop
- Frontend now sends full conversation history with every request

### 4. Redesigned the Frontend UI
- Header with "✦ AI Agent / Claude Sonnet 4.6" badge
- Clear chat button (trash icon, only shown when messages exist)
- Message bubbles: indigo for user (right), white card for Claude (left)
- Avatars: gradient "✦" for Claude, "You" circle for user
- Timestamps below each bubble
- Three-dot bouncing typing indicator while loading
- Auto-growing textarea (max 160px), resets after send
- Empty state with centered prompt when no messages
- Removed Next.js footer promo

### 5. Created CLAUDE.md
- Full project documentation for future Claude Code sessions
- Covers stack, how to run, backend/frontend internals, migration history, known TODOs

### 6. Fixed .gitignore (Was Completely Broken)
- File had markdown code fences (` ``` `) wrapping it — none of the rules were active
- Rewrote as a proper gitignore
- Added `demoenv/` and `*env/` to cover the Python virtualenv

### 7. Fixed Nested Git Repo
- `frontend/.git` existed, making it a broken git submodule from the root repo's perspective
- Removed `frontend/.git` so frontend is tracked as regular files

### 8. Populated requirements.txt
- Was empty — froze all deps from `demoenv` with `pip freeze`

### 9. Made First Git Commit & Pushed to GitHub
- Remote: `git@github.com:asequitytrading-design/ai-agent-ui.git`
- Initial commit: `6604b74` — 22 files

### 10. Swapped Back to Groq (Temporary)
- Anthropic API not working during testing
- Reverted to `ChatGroq(model="openai/gpt-oss-120b")` — agentic loop and all other logic unchanged
- Commit: `ee7967f`

### 11. Implemented Real search_web Tool with SerpAPI
- Replaced dummy stub with `SerpAPIWrapper().run(query)` from `langchain_community`
- Installed `google-search-results==2.4.2` and updated `requirements.txt`
- Commit: `ef643f7`

---

## Current State of the Codebase

| Component | Status |
|-----------|--------|
| FastAPI backend (`main.py`) | ✅ Working — `/chat` endpoint with history support |
| Agentic loop (`agent.py`) | ✅ Fixed — proper multi-turn tool loop |
| LLM | ⚠️ Groq `openai/gpt-oss-120b` (temporary — Claude Sonnet 4.6 intended) |
| `get_current_time` tool | ✅ Working |
| `search_web` tool | ✅ Implemented with SerpAPI — needs `SERPAPI_API_KEY` env var |
| Frontend chat UI | ✅ Redesigned and working |
| Multi-turn history | ✅ Working |
| Git + GitHub | ✅ Clean — 3 commits pushed |

---

## What's Pending

### High Priority
- **Fix Anthropic API access** — once resolved, swap back to Claude:
  - `agent.py` line 1: `from langchain_anthropic import ChatAnthropic`
  - `agent.py` line 29: `llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)`
  - Update comment on line 28 to reflect Claude again
- **Set `SERPAPI_API_KEY`** — sign up at serpapi.com, get key, export before running backend

### Nice to Have
- **Streaming responses** — currently the backend waits for the full agentic loop before responding; SSE or WebSockets would make the UI feel faster
- **Move backend URL to env var** — `http://127.0.0.1:8181` is hardcoded in `frontend/app/page.tsx`; move to `.env.local` for easier deployment
- **Session persistence** — conversation history is lost on page refresh (stored only in React state)
- **Real search_web error handling** — SerpAPI calls can fail; wrap in try/except and return a graceful error message
- **Replace placeholder tools** — `search_web` now uses SerpAPI; could add more tools (calculator, weather, etc.)

---

## Environment Variables Required to Run

```bash
# Backend
export GROQ_API_KEY=...          # current (Groq)
export SERPAPI_API_KEY=...       # for search_web tool

# When switching back to Claude:
export ANTHROPIC_API_KEY=...     # replaces GROQ_API_KEY
```

## How to Run

```bash
# Backend
cd backend
source demoenv/bin/activate
uvicorn main:app --port 8181 --reload

# Frontend (separate terminal)
cd frontend
npm run dev
# → http://localhost:3000
```

## Git Log

| Commit | Message |
|--------|---------|
| `6604b74` | Initial commit: agentic chat app with Claude Sonnet 4.6 |
| `ee7967f` | chore: swap LLM back to Groq (openai/gpt-oss-120b) for testing |
| `ef643f7` | feat: implement search_web tool with SerpAPI (real Google results) |

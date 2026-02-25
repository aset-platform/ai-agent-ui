# SSO Implementation Plan — Google & Facebook

Single sign-on via Google OAuth2 and Facebook OAuth2, layered on top of the
existing JWT + email/password auth.  Email login is preserved unchanged.

---

## Current Auth State

| Layer | Implementation |
|-------|---------------|
| Storage | Apache Iceberg (`auth.users`) via PyIceberg + SQLite catalog |
| Auth | JWT HS256 (access 60 min, refresh 7 days), bcrypt passwords, in-memory deny-list |
| API | 12 endpoints in `auth/api.py`, mounted at root in `backend/main.py` |
| Frontend | `/login` page; `lib/auth.ts` token helpers; `lib/apiFetch.ts` auto-refresh wrapper |
| Dashboard | Token passed via `?token=<jwt>` query param; validated in `_validate_token()` |

### Iceberg `auth.users` current columns

```
user_id, email, hashed_password, full_name, role, is_active,
created_at, updated_at, last_login_at,
password_reset_token, password_reset_expiry
```

---

## New Columns Required

Three nullable columns to add to `auth.users` (Iceberg schema evolution — no migration, just `update_schema()`):

| Column | Type | Purpose |
|--------|------|---------|
| `oauth_provider` | StringType (nullable) | `"google"` / `"facebook"` / `None` for email accounts |
| `oauth_sub` | StringType (nullable) | Provider's unique user ID |
| `profile_picture_url` | StringType (nullable) | Avatar URL from provider (refreshed on each SSO login) |

**Email-only users**: all three columns remain `None`.
**SSO users**: `hashed_password` set to a random 64-char sentinel so password login always fails.

---

## New Files

| File | Purpose |
|------|---------|
| `auth/oauth_service.py` | `OAuthService` class — state/PKCE management, Google/Facebook token exchange |
| `auth/migrate_users_table.py` | Idempotent schema migration — adds three new columns to existing table |
| `frontend/app/auth/oauth/callback/page.tsx` | OAuth callback page — reads code+state, calls backend, stores JWT |
| `frontend/lib/oauth.ts` | PKCE helpers (`generateCodeVerifier`, `generateCodeChallenge`), state helpers |
| `docs/backend/oauth.md` | MkDocs page — flow diagram, endpoints, config, edge cases |

---

## Modified Files

| File | Change |
|------|--------|
| `backend/config.py` | Add `google_client_id`, `google_client_secret`, `facebook_app_id`, `facebook_app_secret`, `oauth_redirect_uri` to `Settings` |
| `backend/.env` | Add the five new env vars (after obtaining credentials) |
| `auth/create_tables.py` | Extend schema definition with three new nullable columns (idempotent) |
| `auth/repository.py` | Add `get_by_oauth_sub()`, `get_or_create_by_oauth()` methods |
| `auth/models.py` | Add `OAuthProvider` enum, `OAuthCallbackRequest`, `OAuthAuthorizeResponse` |
| `auth/api.py` | Add three new OAuth endpoints (see below) |
| `frontend/app/login/page.tsx` | Add Google + Facebook buttons, `handleOAuthLogin()` function |
| `frontend/lib/auth.ts` | Import and re-export PKCE helpers from `oauth.ts` |
| `mkdocs.yml` | Add `oauth.md` under Backend nav |
| `CLAUDE.md` | Document SSO architecture, new env vars, new files |
| `PROGRESS.md` | Session log |

**No changes needed**: `backend/main.py`, `auth/service.py`, `auth/dependencies.py`,
`frontend/app/page.tsx`, `frontend/lib/apiFetch.ts`, `dashboard/app.py`, `dashboard/callbacks.py`.

---

## New API Endpoints (3)

All added to `auth/api.py` inside the existing `create_auth_router()` factory.

### `GET /auth/oauth/{provider}/authorize`

Returns the provider's consent URL and a server-side `state` token.
Frontend uses this to start the OAuth flow.

```
Response: OAuthAuthorizeResponse
{
  "state": "<random 32-char token>",
  "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth?..."
}
```

### `POST /auth/oauth/callback`

Exchanges the auth code for our own JWT pair.
Called by the frontend callback page after the provider redirects back.

```
Request: OAuthCallbackRequest
{
  "provider": "google" | "facebook",
  "code": "<auth code from provider>",
  "state": "<state token from /authorize>",
  "code_verifier": "<PKCE verifier stored in sessionStorage>"
}

Response: TokenResponse  (same model as /auth/login)
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer"
}
```

### `GET /auth/oauth/providers`

Lists enabled providers (for the frontend to show/hide buttons dynamically).

```
Response: { "providers": ["google", "facebook"] }
```

---

## OAuth Flow (PKCE — Authorization Code)

PKCE is mandatory for SPAs: the code verifier lives only in `sessionStorage` and is
never sent to the provider, so a stolen auth code cannot be exchanged.

```
Browser                   Next.js               FastAPI              Google / Facebook
  │                          │                     │                        │
  │ click "Sign in with Google"                     │                        │
  │ ─────────────────────────►                     │                        │
  │                          │ GET /auth/oauth/google/authorize              │
  │                          │ ──────────────────►  │                        │
  │                          │ ◄─── {state, authorize_url}                   │
  │                          │                     │                        │
  │  generate code_verifier  │                     │                        │
  │  store in sessionStorage │                     │                        │
  │  redirect to authorize_url + code_challenge    │                        │
  │ ─────────────────────────────────────────────────────────────────────► │
  │                          │                     │   user consents        │
  │ ◄────────────────── redirect to /auth/oauth/callback?code=X&state=Y ─── │
  │                          │                     │                        │
  │ page.tsx mounts,         │                     │                        │
  │ POST /auth/oauth/callback {provider, code, state, code_verifier}        │
  │ ──────────────────────────────────────────────►│                        │
  │                          │                     │ validate state          │
  │                          │                     │ exchange code + verifier│
  │                          │                     │ ─────────────────────► │
  │                          │                     │ ◄──── {id_token, ...} ─ │
  │                          │                     │                        │
  │                          │                     │ decode id_token         │
  │                          │                     │ get_or_create_by_oauth()│
  │                          │                     │ create_access_token()   │
  │ ◄────────────────────────────────────────────── {access_token, refresh_token}
  │                          │                     │                        │
  │ setTokens() → localStorage                     │                        │
  │ router.replace("/")      │                     │                        │
```

---

## Phase-by-Phase Implementation

### Phase 1 — Prerequisites (manual, before writing code)

1. **Google Cloud Console** (`console.cloud.google.com`):
   - Create / select a project
   - Enable **Google+ API** or **Google Identity** scope
   - Create OAuth 2.0 client credentials (Web application type)
   - Add Authorised redirect URI: `http://localhost:3000/auth/oauth/callback`
   - Copy `Client ID` and `Client Secret`

2. **Facebook Developers** (`developers.facebook.com`):
   - Create a new App → Consumer → Facebook Login product
   - Add Valid OAuth Redirect URI: `http://localhost:3000/auth/oauth/callback`
   - Copy `App ID` and `App Secret`

3. Add to `backend/.env`:
   ```
   GOOGLE_CLIENT_ID=<your-client-id>.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=<your-client-secret>
   FACEBOOK_APP_ID=<your-app-id>
   FACEBOOK_APP_SECRET=<your-app-secret>
   OAUTH_REDIRECT_URI=http://localhost:3000/auth/oauth/callback
   ```

4. Install backend dependency:
   ```bash
   source backend/demoenv/bin/activate
   pip install httpx PyJWT
   pip freeze > backend/requirements.txt
   ```
   (`httpx` for provider HTTP calls; `PyJWT` to decode Google's `id_token` without signature check)

---

### Phase 2 — Database Schema Extension

**File: `auth/migrate_users_table.py`** (new, idempotent)

```python
"""Iceberg schema migration — add OAuth columns to auth.users.

Run once after pulling this branch:
    python auth/migrate_users_table.py
"""
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.types import NestedField, StringType

CATALOG_URI = "sqlite:///data/iceberg/catalog.db"
WAREHOUSE   = "data/iceberg/warehouse"

catalog = SqlCatalog("default", uri=CATALOG_URI, warehouse=WAREHOUSE)
table   = catalog.load_table("auth.users")

existing_names = {f.name for f in table.schema().fields}
new_fields = [
    ("oauth_provider",      StringType()),
    ("oauth_sub",           StringType()),
    ("profile_picture_url", StringType()),
]

with table.update_schema() as upd:
    for name, ftype in new_fields:
        if name not in existing_names:
            upd.add_column(name, ftype)
            print(f"  + Added column: {name}")
        else:
            print(f"  ✓ Column already exists: {name}")

print("Migration complete.")
```

**File: `auth/create_tables.py`** — extend the `USERS_SCHEMA` with the three new fields so fresh installs include them from day one.

**File: `auth/repository.py`** — add two new methods:

```python
def get_by_oauth_sub(self, provider: str, oauth_sub: str) -> Optional[Dict[str, Any]]:
    """Return user dict for a given OAuth provider + sub, or None."""

def get_or_create_by_oauth(
    self,
    provider: str,
    oauth_sub: str,
    email: str,
    full_name: str,
    picture_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Return existing user or create a new SSO account.

    Lookup order:
    1. Match on (oauth_sub, oauth_provider) — returning user via same provider.
    2. Match on email — existing email account; link the OAuth provider.
    3. No match — create new account with sentinel password hash.
    """
```

---

### Phase 3 — Backend OAuth Service

**File: `auth/oauth_service.py`** (new)

```python
class OAuthService:
    """Handles OAuth2 PKCE flow for Google and Facebook."""

    _GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
    _GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    _FB_AUTH_URL      = "https://www.facebook.com/v18.0/dialog/oauth"
    _FB_TOKEN_URL     = "https://graph.facebook.com/v18.0/oauth/access_token"
    _FB_ME_URL        = "https://graph.facebook.com/me"

    def __init__(self, settings: Settings) -> None: ...

    # State / CSRF
    def generate_authorize_url(self, provider: str, code_challenge: str) -> Tuple[str, str]:
        """Return (state, authorize_url)."""

    def validate_state(self, state: str, provider: str) -> bool:
        """Consume state token and return True if valid."""

    # Google
    def exchange_google_code(self, code: str, code_verifier: str) -> Dict[str, Any]:
        """Exchange auth code for {provider, sub, email, full_name, picture}."""

    # Facebook
    def exchange_facebook_code(self, code: str) -> Dict[str, Any]:
        """Exchange auth code for {provider, sub, email, full_name, picture}."""
```

The state store is an in-memory `Dict[str, Dict]` with a 10-minute TTL; entries are
cleaned by a background task registered with `asyncio` on startup (added to `backend/main.py`).

---

### Phase 4 — New Auth Endpoints

**File: `auth/api.py`** — add inside existing `create_auth_router()`:

```python
@router.get("/auth/oauth/providers")
def list_oauth_providers(settings: Settings = Depends(get_settings)):
    providers = []
    if settings.google_client_id:
        providers.append("google")
    if settings.facebook_app_id:
        providers.append("facebook")
    return {"providers": providers}


@router.get("/auth/oauth/{provider}/authorize")
def oauth_authorize(provider: str, code_challenge: str, ...):
    state, url = oauth_svc.generate_authorize_url(provider, code_challenge)
    return OAuthAuthorizeResponse(state=state, authorize_url=url)


@router.post("/auth/oauth/callback", response_model=TokenResponse)
def oauth_callback(body: OAuthCallbackRequest, ...):
    # 1. validate state
    # 2. exchange code → user_info dict
    # 3. repo.get_or_create_by_oauth(...)
    # 4. auth_svc.create_access_token(...) + create_refresh_token(...)
    # 5. repo.append_audit_event("OAUTH_LOGIN", ...)
    # 6. return TokenResponse
```

---

### Phase 5 — Frontend

**File: `frontend/lib/oauth.ts`** (new)

```typescript
export function generateCodeVerifier(length = 128): string
export async function generateCodeChallenge(verifier: string): Promise<string>
```

**File: `frontend/app/auth/oauth/callback/page.tsx`** (new)

```typescript
// On mount:
// 1. Read code, state from URL search params
// 2. Read code_verifier from sessionStorage
// 3. POST /auth/oauth/callback
// 4. setTokens(access_token, refresh_token)
// 5. router.replace("/")
// Show spinner while processing; error message on failure
```

**File: `frontend/app/login/page.tsx`** — add below the existing form:

```typescript
// Divider
<div className="flex items-center gap-3 my-4">
  <hr className="flex-1 border-zinc-700" />
  <span className="text-xs text-zinc-500">or continue with</span>
  <hr className="flex-1 border-zinc-700" />
</div>

// Buttons
<button onClick={() => handleOAuthLogin("google")}>
  <GoogleIcon /> Sign in with Google
</button>
<button onClick={() => handleOAuthLogin("facebook")}>
  <FacebookIcon /> Sign in with Facebook
</button>
```

`handleOAuthLogin(provider)`:
1. `GET /auth/oauth/{provider}/authorize?code_challenge=<sha256(verifier)>`
2. Store `code_verifier` in `sessionStorage`
3. `window.location.href = authorize_url`

---

### Phase 6 — Account Linking (optional, post-MVP)

Allow existing email-password users to link a Google/Facebook account from a settings page:
- `POST /auth/oauth/link/{provider}` — requires authenticated user + OAuth callback
- `DELETE /auth/oauth/unlink/{provider}` — remove the link (only if another login method exists)
- Frontend: "Connected accounts" section in user profile

---

## Edge Cases & Decisions

| Scenario | Decision |
|----------|----------|
| Same email from two different OAuth providers | Merge into one account; `oauth_sub` stores the new provider's ID; audit event records the link |
| Existing email/password account + same email via OAuth | Link on first SSO login — no duplicate accounts; user can then login via either method |
| SSO user attempts password login | `hashed_password` is a sentinel (`"!sso_only_<random>"`); bcrypt verify always fails; password reset endpoint returns `400 "SSO account — use Google/Facebook to sign in"` |
| Facebook email not granted | Facebook email permission is optional; fall back to `fb_{sub}@facebook.local` as placeholder email |
| OAuth state token cleanup | Background task in `backend/main.py` runs every 5 min; removes states older than 10 min |
| Dashboard + SSO | No changes needed — dashboard receives same JWT from frontend, validates identically |
| Admin user (superuser) via SSO | Superuser role is only set via `seed_admin.py`; SSO auto-creates `role="general"` accounts |
| `id_token` verification (Google) | Decode without signature verification for now (trust TLS + HTTPS); for production, verify using Google's public JWKS |

---

## New Environment Variables

Add to `backend/.env` after setting up credentials:

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_CLIENT_ID` | For Google SSO | OAuth 2.0 client ID from Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | For Google SSO | OAuth 2.0 client secret |
| `FACEBOOK_APP_ID` | For Facebook SSO | App ID from Facebook Developers portal |
| `FACEBOOK_APP_SECRET` | For Facebook SSO | App secret from Facebook Developers portal |
| `OAUTH_REDIRECT_URI` | Yes (both) | Must match redirect URI registered with each provider. Default: `http://localhost:3000/auth/oauth/callback` |

---

## Python Dependencies to Install

```bash
source backend/demoenv/bin/activate
pip install httpx PyJWT
pip freeze > backend/requirements.txt
```

| Package | Version | Purpose |
|---------|---------|---------|
| `httpx` | latest | Async/sync HTTP client for token exchange calls to Google/Facebook |
| `PyJWT` | latest | Decode Google `id_token` (no signature verification needed; trust HTTPS) |

---

## Files at a Glance

```
ai-agent-ui/
│
├── auth/
│   ├── oauth_service.py          ← NEW: OAuthService (state, PKCE, token exchange)
│   ├── migrate_users_table.py    ← NEW: Add 3 columns to existing Iceberg table
│   ├── create_tables.py          ← MODIFY: Add 3 fields to schema definition
│   ├── repository.py             ← MODIFY: get_by_oauth_sub, get_or_create_by_oauth
│   ├── models.py                 ← MODIFY: OAuthProvider enum, 2 new request models
│   └── api.py                    ← MODIFY: 3 new endpoints
│
├── backend/
│   └── config.py                 ← MODIFY: 5 new settings fields
│
├── frontend/
│   ├── app/
│   │   ├── auth/oauth/callback/
│   │   │   └── page.tsx          ← NEW: OAuth callback handler
│   │   └── login/page.tsx        ← MODIFY: Google + Facebook buttons
│   └── lib/
│       ├── oauth.ts              ← NEW: PKCE helpers
│       └── auth.ts               ← MODIFY: re-export PKCE helpers
│
└── docs/
    └── backend/
        └── oauth.md              ← NEW: SSO documentation page
```

---

## Success Criteria

- [ ] "Sign in with Google" button on `/login` — completes OAuth flow and lands on `/`
- [ ] "Sign in with Facebook" button on `/login` — completes OAuth flow and lands on `/`
- [ ] First SSO login auto-creates account with `role="general"`
- [ ] Repeat SSO login finds existing account (no duplicates)
- [ ] Email/password login still works unchanged
- [ ] Same email via email auth + Google SSO merges into one account
- [ ] JWT auto-refresh works for SSO-originated tokens (`apiFetch`)
- [ ] Dashboard validates SSO tokens correctly (no changes needed)
- [ ] Audit log records `OAUTH_LOGIN` events with provider metadata
- [ ] SSO user cannot login with password (sentinel hash always fails)
- [ ] `mkdocs build` passes after adding `oauth.md`

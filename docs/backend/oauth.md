# OAuth2 / SSO

Single sign-on via Google and Facebook OAuth2, layered on top of the existing
JWT + email/password auth.  Email login is preserved unchanged.

---

## Overview

| Provider | Protocol | User-ID claim | Email scope |
|----------|----------|---------------|-------------|
| Google   | OAuth2 + OpenID Connect | `sub` in `id_token` | `email` (always granted) |
| Facebook | OAuth2 Graph API | `id` from `/me` | `email` (user may decline) |

PKCE (Proof Key for Code Exchange) is enforced for all providers.
The `code_verifier` never leaves the browser; only its SHA-256 hash
(`code_challenge`) is sent to the backend.

---

## OAuth2 PKCE Flow

```
Browser                   Next.js               FastAPI              Provider
  │                          │                     │                     │
  │  click "Sign in with Google"                                          │
  │──────────────────────────►                                            │
  │                          │  GET /auth/oauth/google/authorize          │
  │                          │  ?code_challenge=<hash>                    │
  │                          │──────────────────►  │                      │
  │                          │  ◄── {state, authorize_url}               │
  │                          │                     │                      │
  │  generate code_verifier  │                     │                      │
  │  store in sessionStorage │                     │                      │
  │  redirect to authorize_url                      │                      │
  │──────────────────────────────────────────────────────────────────────►│
  │                          │                     │   user consents      │
  │◄── redirect to /auth/oauth/callback?code=X&state=Y ─────────────────  │
  │                          │                     │                      │
  │  POST /auth/oauth/callback                      │                      │
  │  {provider, code, state, code_verifier}         │                      │
  │──────────────────────────────────────────────►  │                      │
  │                          │                     │  validate state      │
  │                          │                     │  exchange code       │
  │                          │                     │─────────────────────►│
  │                          │                     │◄── {id_token, ...}   │
  │                          │                     │  decode id_token     │
  │                          │                     │  upsert user         │
  │                          │                     │  issue JWT pair      │
  │◄───────────────────────────────────────────── {access_token, refresh_token}
  │                          │                     │                      │
  │  setTokens() → localStorage                                           │
  │  router.replace("/")     │                     │                      │
```

---

## New API Endpoints

### `GET /auth/oauth/providers`

Lists OAuth providers that are currently enabled (non-empty credentials in
`backend/.env`).

```json
{ "providers": ["google", "facebook"] }
```

---

### `GET /auth/oauth/{provider}/authorize`

Generates a provider consent URL and a server-side CSRF `state` token.

**Query parameters**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `code_challenge` | Yes | Base64url SHA-256 of the PKCE `code_verifier` |

**Response** (`OAuthAuthorizeResponse`)

```json
{
  "state": "random-32-char-token",
  "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth?..."
}
```

---

### `POST /auth/oauth/callback`

Exchanges the provider's authorization code for our own JWT pair.

**Request body** (`OAuthCallbackRequest`)

```json
{
  "provider": "google",
  "code": "<auth-code-from-provider>",
  "state": "<state-from-/authorize>",
  "code_verifier": "<pkce-verifier-from-sessionstorage>"
}
```

**Response** — same `TokenResponse` shape as `POST /auth/login`:

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer"
}
```

---

## Database Schema Changes

Three nullable columns were added to the `auth.users` Iceberg table:

| Column | Type | Purpose |
|--------|------|---------|
| `oauth_provider` | StringType (nullable) | `"google"` / `"facebook"` / `None` for email accounts |
| `oauth_sub` | StringType (nullable) | Provider-specific user ID |
| `profile_picture_url` | StringType (nullable) | Avatar URL, refreshed on each SSO login |

**SSO users**: `hashed_password` is set to a random sentinel (`!sso_only_<hex>`)
so bcrypt verification always fails — SSO users cannot log in with a password.

**Email-password users linking OAuth**: on first SSO login with a matching
email, the three OAuth columns are filled in; the bcrypt password continues to
work.

### Running the migration

For existing deployments (before this feature was added):

```bash
cd ai-agent-ui
source ~/.ai-agent-ui/venv/bin/activate
python auth/migrate_users_table.py
```

The script is idempotent — safe to run multiple times.

---

## Configuration

Add to `backend/.env`:

```
GOOGLE_CLIENT_ID=<client-id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<client-secret>
FACEBOOK_APP_ID=<app-id>
FACEBOOK_APP_SECRET=<app-secret>
OAUTH_REDIRECT_URI=http://localhost:3000/auth/oauth/callback
```

### Google Cloud Console setup

1. Go to `console.cloud.google.com` → APIs & Services → Credentials.
2. Create an **OAuth 2.0 Client ID** (Web application type).
3. Under **Authorised redirect URIs** add `http://localhost:3000/auth/oauth/callback`.
4. Copy the Client ID and Client Secret into `backend/.env`.

### Facebook Developers setup

1. Go to `developers.facebook.com` → Create App → Consumer.
2. Add the **Facebook Login** product.
3. Under **Valid OAuth Redirect URIs** add `http://localhost:3000/auth/oauth/callback`.
4. Copy the App ID and App Secret into `backend/.env`.

---

## Edge Cases

| Scenario | Behaviour |
|----------|-----------|
| Same email via two providers | Merged into one account; `oauth_sub` updated to the latest provider |
| Existing email/password + same email via OAuth | Provider columns are linked on first SSO login |
| SSO user tries password login | Sentinel `hashed_password` always fails bcrypt; HTTP 401 |
| Facebook does not grant email | Falls back to `fb_{id}@facebook.local` |
| State token expired (> 10 min) | HTTP 400 — "Invalid or expired OAuth state token" |
| Provider credentials not configured | `GET /auth/oauth/{provider}/authorize` returns HTTP 503 |
| Admin (superuser) via SSO | Not possible — superuser role is only set via `scripts/seed_admin.py` |

---

## New Files & Changes

| File | Type | Description |
|------|------|-------------|
| `auth/oauth_service.py` | New | `OAuthService` — state management, authorize URL, code exchange |
| `auth/migrate_users_table.py` | New | Idempotent Iceberg schema migration |
| `frontend/lib/oauth.ts` | New | PKCE helpers (`generateCodeVerifier`, `generateCodeChallenge`) |
| `frontend/app/auth/oauth/callback/page.tsx` | New | OAuth callback page |
| `backend/config.py` | Modified | 5 new OAuth settings |
| `auth/create_tables.py` | Modified | 3 new nullable columns in `_users_schema()` |
| `auth/repository.py` | Modified | `get_by_oauth_sub()`, `get_or_create_by_oauth()`, updated PA schema |
| `auth/models.py` | Modified | `OAuthProvider`, `OAuthAuthorizeResponse`, `OAuthCallbackRequest` |
| `auth/api.py` | Modified | 3 new OAuth endpoints |
| `frontend/app/login/page.tsx` | Modified | Google + Facebook SSO buttons |
| `frontend/lib/auth.ts` | Modified | Re-exports PKCE helpers from `oauth.ts` |

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `httpx` | 0.28.x | HTTP client for provider token-exchange calls |
| `PyJWT` | 2.x | Decode Google's `id_token` (no signature verification) |

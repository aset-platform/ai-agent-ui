# Authentication & User Management

The auth module adds JWT-based authentication and role-based access control (RBAC) to all three surfaces: the **chat frontend** (Next.js), the **dashboard** (Plotly Dash), and the **FastAPI backend**.

Storage is Apache Iceberg via PyIceberg with a SQLite-backed SqlCatalog — no extra database server required.

---

## Quick Start

```bash
# 1. Generate a strong secret key
python -c "import secrets; print(secrets.token_hex(32))"

# 2. Add required env vars to .env at the project root
cat >> .env <<EOF
JWT_SECRET_KEY=<paste-output-above>
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=Admin1234
ADMIN_FULL_NAME=Admin User
EOF

# 3. Initialise Iceberg tables (one-time, idempotent)
python auth/create_tables.py

# 4. Seed the first superuser
python scripts/seed_admin.py

# 5. Start everything
./run.sh start
# → log in at http://localhost:3000/login
```

!!! note "Automatic init on `./run.sh start`"
    Steps 3 and 4 run automatically the first time you call `./run.sh start`,
    provided `JWT_SECRET_KEY`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD` are set.

---

## Architecture

```
auth/
├── __init__.py          Package init
├── create_tables.py     One-time Iceberg table setup (idempotent)
├── repository.py        IcebergUserRepository — CRUD + audit log
├── service.py           AuthService — bcrypt + JWT lifecycle
├── models.py            Pydantic request/response models
├── dependencies.py      FastAPI dependency functions
└── api.py               create_auth_router() — all endpoints

scripts/
└── seed_admin.py        Bootstrap first superuser from env vars
```

### Storage — Apache Iceberg

Two Iceberg tables backed by a SQLite catalog at `data/iceberg/catalog.db`.

| Table | Namespace | Purpose |
|---|---|---|
| `auth.users` | `auth` | User accounts and credentials |
| `auth.audit_log` | `auth` | Immutable event history |
| `auth.user_tickers` | `auth` | Per-user linked ticker watchlist |

The warehouse lives at `data/iceberg/warehouse/` and is gitignored.  The catalog config is read from `.pyiceberg.yaml` in the project root (gitignored; copy from `.pyiceberg.yaml.example`).

### Password hashing — bcrypt

`AuthService` uses `passlib[bcrypt]` with cost factor 12 (~250 ms per hash on a modern CPU).  Only the hash is stored; plaintext passwords never appear in logs.

### JWT tokens — HS256

| Token | Payload fields | TTL |
|---|---|---|
| Access | `sub`, `email`, `role`, `type="access"`, `jti`, `iat`, `exp` | 60 min (configurable) |
| Refresh | `sub`, `type="refresh"`, `jti`, `iat`, `exp` | 7 days (configurable) |

Refresh tokens are rotated on every `/auth/refresh` call — the old token is immediately revoked.  Logout adds the JTI to an in‑memory deny‑list (cleared on restart; acceptable for single‑server MVP).

### Roles

| Role | Permissions |
|---|---|
| `superuser` | Full access: all user CRUD + all `/admin/*` endpoints + scope=all on self-scoped admin routes |
| `pro` | Insights access + scoped Admin view: My Account, My Audit Log, My LLM Usage. Forced to `scope=self` on `/admin/audit-log`, `/admin/metrics`, `/admin/usage-stats`; all other `/admin/*` routes return 403. |
| `general` | Chat + dashboard + portfolio only. `/admin` frontend route redirects to `/dashboard`; `/admin/*` API returns 403. |

### Role ↔ subscription auto-sync

Role is tier-driven with one rule: **superuser is sticky**. The pinch
point is `auth/repo/user_writes.py::update()` — every `subscription_tier`
write flows through it.

| `subscription_tier` | Resulting role (for non-superusers) |
|---|---|
| `free` | `general` |
| `pro`, `premium` | `pro` |

Superusers are never auto-demoted. A role change fires either
`ROLE_PROMOTED` or `ROLE_DEMOTED` in the audit log with metadata
`{old_role, new_role, reason: "subscription_tier_change", new_tier}`.

### Dependency guards

| Guard | Use case | File |
|---|---|---|
| `get_current_user` | Any authenticated endpoint | `auth/dependencies.py:105` |
| `superuser_only` | ~45 admin endpoints (Users, Scheduler, Maintenance, Transactions, Recommendations, Backup Health, Data Health) | `auth/dependencies.py:162` |
| `require_role(*allowed)` | Factory for custom role sets | `auth/dependencies.py:198` |
| `pro_or_superuser` | Self-scoped admin endpoints (`/admin/audit-log`, `/admin/metrics`, `/admin/usage-stats`) | same file, alias |
| `require_tier(min_tier)` | Subscription-gated features | `auth/dependencies.py` |

### Self-scope query param pattern

Endpoints accessible to `pro` take `?scope=self|all`:

- `scope=self` — always allowed. Filters results to the caller.
- `scope=all` — superuser only. Returns 403 for `pro`.

Pro users default to `scope=self` if omitted; superusers default to
`scope=all`.

### Manual promotion

Superusers can change any user's role via
`PATCH /users/{user_id}` — role dropdown in the Users tab edit modal
offers General / Pro / Superuser. Audit metadata records `old_role`
and `new_role` on change.

---

## API Endpoints

Base URL: `http://127.0.0.1:8181`

### Auth

#### `POST /auth/login`

Authenticate with email + password.  Returns a JWT access + refresh token pair.

```http
POST /auth/login
Content-Type: application/json
```

```json
{ "email": "admin@example.com", "password": "Admin1234" }
```

**Response 200:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer"
}
```

**Error codes:** `401` invalid credentials / deactivated account.

---

#### `POST /auth/login/form`

OAuth2 form‑based login (used by the OpenAPI "Authorize" button at `/docs`).  Accepts `application/x-www-form-urlencoded` with `username` (email) and `password`.

---

#### `POST /auth/refresh`

Exchange a valid refresh token for a new access + refresh token pair.  The old refresh token is revoked immediately.

```json
{ "refresh_token": "eyJ..." }
```

**Error codes:** `401` expired / revoked / invalid token.

---

#### `POST /auth/logout`

Revoke the supplied refresh token.

```http
Authorization: Bearer <access_token>
```
```json
{ "refresh_token": "eyJ..." }
```

---

#### `POST /auth/password-reset/request`

Generate a single‑use 30‑minute password reset token for the authenticated user.  The caller may only reset their own password.

```http
Authorization: Bearer <access_token>
```
```json
{ "email": "admin@example.com" }
```

!!! warning "Development mode"
    The reset token is returned in the response body for development.
    In production, replace this with email delivery and omit the token from the response.

---

#### `POST /auth/password-reset/confirm`

Apply a new password using the reset token from the previous step.  The token is single‑use.

```http
Authorization: Bearer <access_token>
```
```json
{ "reset_token": "<uuid>", "new_password": "NewPass1" }
```

**Error codes:** `400` invalid/expired token or password too weak.

---

### Users (superuser only)

All user management endpoints require a superuser access token:

```http
Authorization: Bearer <superuser_access_token>
```

#### `GET /users`

List all user accounts.

**Response 200:** array of `UserResponse` objects.

---

#### `POST /users`

Create a new user account.

```json
{
  "email": "jane@example.com",
  "password": "Jane1234",
  "full_name": "Jane Doe",
  "role": "general"
}
```

**Error codes:** `400` password too weak, `409` email already in use.

---

#### `GET /users/{user_id}`

Get a single user by UUID.

**Error codes:** `404` user not found.

---

#### `PATCH /users/{user_id}`

Update a user's details.  All fields are optional; only supplied fields are changed.

```json
{
  "full_name": "Jane Smith",
  "email": "jsmith@example.com",
  "role": "superuser",
  "is_active": false
}
```

**Error codes:** `404` user not found, `409` email already in use.

---

#### `DELETE /users/{user_id}`

Soft‑delete a user (`is_active = false`).  Superusers cannot delete themselves.

**Error codes:** `400` self‑delete attempted, `404` user not found.

---

### Admin (superuser only)

#### `GET /admin/audit-log`

Return all audit log events, sorted newest‑first.

**Response 200:**
```json
{
  "events": [
    {
      "event_id": "...",
      "event_type": "LOGIN",
      "actor_user_id": "...",
      "target_user_id": "...",
      "event_timestamp": "2026-02-25T10:00:00",
      "metadata": "{\"stage\": \"confirm\"}"
    }
  ]
}
```

**Event types:** `USER_CREATED`, `USER_UPDATED`, `USER_DELETED`, `LOGIN`, `PASSWORD_RESET`.

---

### Ticker Management

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/users/me/tickers` | JWT | List the authenticated user's linked tickers |
| POST | `/users/me/tickers` | JWT | Link a ticker (`{"ticker": "AAPL", "source": "manual"}`) |
| DELETE | `/users/me/tickers/{ticker}` | JWT | Unlink a ticker from the user |

Tickers are also auto-linked when a user analyses a stock via the chat server. New users receive `RELIANCE.NS` as a default linked ticker.

---

### Admin Password Reset

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/users/{user_id}/reset-password` | Superuser | Reset any user's password |

Body: `{"new_password": "..."}` (min 8 chars). Clears pending reset tokens. Audit-logged as `ADMIN_PASSWORD_RESET`.

---

### OAuth / SSO

#### `GET /auth/oauth/providers`

Returns a list of enabled OAuth providers (e.g., `google`, `facebook`).

```json
["google", "facebook"]
```

---

#### `GET /auth/oauth/{provider}/authorize?code_challenge=<hash>`

Creates a one‑time state value, stores it in an in‑memory cache (10‑minute TTL), and returns the provider‑specific consent URL.

```json
{
  "state": "abcd1234",
  "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth?..."
}
```

---

#### `POST /auth/oauth/callback`

Exchanges the provider’s authorization `code` (and PKCE `code_verifier` for Google) for a JWT access/refresh pair and upserts the user in `auth.users`.

```json
{
  "provider": "google",
  "code": "4/0AY0e...",
  "state": "abcd1234",
  "code_verifier": "random-string"  // optional for Facebook
}
```

**Response 200:** same shape as `/auth/login` (`access_token`, `refresh_token`, `token_type`).

---

## Pydantic Models

Defined in `auth/models.py`:

| Model | Used by |
|---|---|
| `LoginRequest` | `POST /auth/login` body |
| `TokenResponse` | All token‑returning endpoints |
| `RefreshRequest` | `POST /auth/refresh` body |
| `LogoutRequest` | `POST /auth/logout` body |
| `PasswordResetRequestBody` | `POST /auth/password-reset/request` body |
| `PasswordResetConfirmBody` | `POST /auth/password-reset/confirm` body |
| `UserCreateRequest` | `POST /users` body |
| `UserUpdateRequest` | `PATCH /users/{id}` body |
| `UserContext` | Injected into routes by `get_current_user` dependency |
| `UserResponse` | All user‑returning endpoints (no sensitive fields) |
| `OAuthProvider` (enum) | OAuth provider identifier (`google`, `facebook`) |
| `OAuthAuthorizeResponse` | `GET /auth/oauth/{provider}/authorize` response |
| `OAuthCallbackRequest` | `POST /auth/oauth/callback` body |

---

## FastAPI Dependencies

Defined in `auth/dependencies.py`:

```python
# Require a valid access token; returns UserContext
get_current_user: Depends(oauth2_scheme) → UserContext

# Require superuser role; raises HTTP 403 otherwise
superuser_only: Depends(get_current_user) → UserContext

# Return the AuthService singleton (lru_cache)
get_auth_service: Callable → AuthService
```

---

## Configuration

Add these to `backend/.env` (or export as environment variables):

```bash
# Required
JWT_SECRET_KEY=<min-32-random-chars>

# Optional (shown with defaults)
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

# OAuth / SSO (new)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
FACEBOOK_APP_ID=...
FACEBOOK_APP_SECRET=...
OAUTH_REDIRECT_URI=http://localhost:3000/auth/oauth/callback
```

`backend/config.py` `Settings` reads all of the above automatically.

---

## Migration

A one‑time Iceberg schema migration adds three nullable columns to `auth.users`:

| Column | Type | Notes |
|---|---|---|
| `oauth_provider` | StringType (nullable) | `"google"` / `"facebook"` / `None` |
| `oauth_sub` | StringType (nullable) | Provider‑specific user ID |
| `profile_picture_url` | StringType (nullable) | Refreshed on each SSO login |

Run the migration after deploying the new code:

```bash
cd ai-agent-ui && source ~/.ai-agent-ui/venv/bin/activate
python auth/migrate_users_table.py
```

The script is idempotent – re‑running it will report the columns as already existing.

---

## Frontend Login Flow

### File structure

```
frontend/
├── app/
│   ├── login/
│   │   └── page.tsx      # Login page — email + password form + SSO buttons
│   └── page.tsx          # Main SPA — auth guard + logout + Admin nav item
└── lib/
    ├── auth.ts            # Core auth helpers (login, refresh, logout)
    └── oauth.ts           # PKCE helpers + sessionStorage helpers for SSO
```

The login page now fetches `/auth/oauth/providers` and renders a Google and/or Facebook button when the corresponding provider is enabled. Clicking a button triggers the PKCE flow, stores the temporary verifier in `sessionStorage`, and redirects the user to the provider consent screen. After consent, the callback page (`frontend/app/auth/oauth/callback/page.tsx`) completes the exchange and stores the JWT pair.

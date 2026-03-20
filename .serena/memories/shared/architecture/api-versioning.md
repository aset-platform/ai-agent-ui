# API Versioning

## Design

All API routes are mounted exclusively under `/v1/` prefix.
Root-level API routes were removed (ASETPLTFRM-20, Mar 13 2026).

### What stays at root

- **WebSocket**: `/ws/chat` — not versioned
- **Static files**: `/avatars/*` — served by StaticFiles middleware

### Route mounting

```python
# backend/routes.py
v1_router = APIRouter(prefix="/v1")
# Auth, ticker, admin routers included with prefix="/v1"
app.include_router(auth_router, prefix="/v1")
app.include_router(ticker_router, prefix="/v1")
app.include_router(admin_router, prefix="/v1")
```

## URL Constants

### Frontend (`frontend/lib/config.ts`)

| Constant | Value | Use for |
|----------|-------|---------|
| `BACKEND_URL` | `http://localhost:8181` | Avatar image URLs, WS URL derivation |
| `API_URL` | `${BACKEND_URL}/v1` | All API calls via `apiFetch` |
| `WS_URL` | `ws://localhost:8181` | WebSocket connection |

**Rule**: Use `API_URL` for all API calls. Use `BACKEND_URL` only for
static assets (avatars) and deriving WS URL.

Files using `BACKEND_URL` directly (avatar URLs only):
- `ChatHeader.tsx`, `EditProfileModal.tsx`

### Dashboard (`dashboard/callbacks/auth_utils.py`)

| Variable | Value | Use for |
|----------|-------|---------|
| `_BACKEND_HOST` | `http://127.0.0.1:8181` | Avatar image URLs |
| `_BACKEND_URL` | `http://127.0.0.1:8181/v1` | API calls |

Both derived from `BACKEND_URL` env var. The `_api_call()` helper
in `auth_utils.py` uses `_BACKEND_URL` for all API requests.

## Design Decision

Chose to add `API_URL` constant rather than modifying `BACKEND_URL`
to include `/v1`, because:
1. Avatar URLs need the bare host (no `/v1`)
2. WebSocket URL is derived from `BACKEND_URL` (http→ws replacement)
3. Changing `BACKEND_URL` would break these derivations

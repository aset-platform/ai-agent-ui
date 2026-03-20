# Test Isolation Gotchas

## 1. Test Ordering Pollution (auth rate limiter)

`test_auth_api.py` tests for admin password reset and health endpoint
fail when run in a full suite but pass in isolation. The `slowapi`
rate limiter state bleeds across test modules.

**Fix**: Use per-test `app.state.limiter` reset or run affected tests
in isolation. Known issue — not a code bug.

## 2. Starlette WebSocket Testing

Plain HTTP GET to a WebSocket endpoint returns **404** in Starlette
(not 403, 400, or 426 as you might expect). Do not test WS endpoints
with regular HTTP requests.

**Correct approach**: Inspect `app.routes` for the expected path:
```python
ws_paths = [getattr(r, "path", "") for r in client.app.routes]
assert "/ws/chat" in ws_paths
```

## 3. Dashboard Test Import Chain

Importing `dashboard.callbacks.observability_cbs` triggers
`dashboard.callbacks.__init__` which imports the full callback chain.
This requires the `holidays` package (and potentially other deps).

**Fix**: Replicate constants in the test file instead of importing
from the callback module. Or ensure test environment has all dashboard
dependencies installed.

## 4. Auth Dependency Patch Bleed

`auth.dependencies.get_auth_service` patches bleed across test modules
when using `scope="module"` fixtures.

**Fix**: Patch the specific function under test (e.g. `ws._validate_token`)
per-test instead of patching the shared dependency at module scope.

## 5. Patching at Source Module

Always patch where the symbol is **defined**, not where it's imported.
```python
# WRONG — patches the import, not the source
@patch("backend.routes.create_auth_router")

# RIGHT — patches the source
@patch("auth.api.create_auth_router")
```

Exception: When testing a specific module's behavior with a mock,
patch at the import site if the module caches the import at load time.

## 6. Empty Router Mounting

When testing API prefix mounting, patching `create_auth_router` with
an empty `APIRouter()` means no routes exist — requests return 404
even with correct prefix.

**Fix**: Create a test router with at least one dummy route:
```python
auth_router = APIRouter()
@auth_router.post("/auth/login")
async def _dummy_login():
    return {"ok": True}
```

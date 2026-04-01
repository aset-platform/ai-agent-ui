# Chat Session Recording — Auth & Persistence Gotchas

## Problem
Chat sessions silently fail to persist to Iceberg. Multiple root causes.

## Root Causes & Fixes

### 1. sendBeacon Cannot Send Auth Headers
`navigator.sendBeacon()` sends POST but cannot attach `Authorization: Bearer` header → 401.
**Fix**: Use `fetch()` + `keepalive: true` + manual auth header instead of sendBeacon.

### 2. apiFetch 401 Handler Races With Logout
`apiFetch` on 401 calls `clearTokens()` + redirects to /login. During logout flush, this races with the actual logout.
**Fix**: Use raw `fetch()` with `getAccessToken()` in the flush function, not apiFetch.

### 3. Missing flush() Calls
Sign-out and close-panel paths went straight to `clearTokens()` without flushing pending sessions.
**Fix**: Always `await chatContext.flush()` before `clearTokens()`.

### 4. PyArrow Timestamp Conversion
ISO timestamp strings (e.g., "2026-03-22T10:30:00Z") can't be directly inserted into `pa.timestamp("us")` columns. PyArrow says "str cannot be converted to int" (misleading — it's a timestamp field, not int).
**Fix**: Parse via `pd.Timestamp()` then `.to_pydatetime()` before inserting into PyArrow table.

### 5. Wrong localStorage Key
`beforeunload` handler read `access_token` but the actual key is `auth_access_token`.
**Fix**: Use the correct constant `ACCESS_KEY = "auth_access_token"`.

## Debugging Pattern
Check backend logs for: `POST /v1/audit/chat-sessions 401` or `save_chat_session failed`. The HTTP status code and error message are the breadcrumbs.
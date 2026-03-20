# Streaming Protocol

## HTTP Streaming

`POST /v1/chat/stream` — returns NDJSON (newline-delimited JSON).

### Event Types

| Event | Description |
|-------|-------------|
| `thinking` | LLM reasoning step |
| `tool_start` | Tool invocation beginning |
| `tool_done` | Tool result returned |
| `warning` | Non-fatal issue |
| `final` | Complete response |
| `error` | Fatal error |
| `timeout` | Agent timeout (configured via `AGENT_TIMEOUT_SECONDS`) |

### Frontend Consumption

Use `apiFetch` (not raw `fetch`) for all backend calls — it
auto-refreshes JWT tokens. Stream is consumed via `useSendMessage`
hook which prefers WebSocket when connected, falls back to HTTP NDJSON.

---

## WebSocket Streaming

`/ws/chat` — persistent bidirectional connection. Same event types
as HTTP NDJSON but over a single long-lived connection.

### Protocol

1. **Connect** to `ws://<host>/ws/chat`
2. **Auth-first**: Send `{"type":"auth","token":"<JWT>"}` as first message
3. Server validates JWT, responds with `{"type":"auth_ok"}` or closes
4. **Chat**: Send `{"type":"chat","message":"...","agent_type":"...","user_id":"..."}`
5. Server streams events (same types as NDJSON)
6. **Ping/pong**: Server sends keepalive every 30s (`ws_ping_interval_seconds`)
7. **Re-auth**: Supported mid-session for token refresh

### Close Codes

| Code | Meaning |
|------|---------|
| 4001 | Auth failed (invalid/expired JWT) |
| 4002 | Auth timeout (no auth message within `ws_auth_timeout_seconds`) |
| 4003 | Invalid message format |

### Concurrency Guard

Server rejects a second chat request while one is already in-flight
on the same connection.

### Key Files

- `backend/ws.py` — WebSocket endpoint handler
- `frontend/hooks/useWebSocket.ts` — Connection state machine hook
- `frontend/hooks/useSendMessage.ts` — WS-preferred + HTTP fallback

### Frontend State Machine

```
DISCONNECTED → CONNECTING → AUTHENTICATING → READY
     ↑                                         |
     └─── on close (exponential backoff) ──────┘
```

Backoff: 1s, 2s, 4s… max 30s. Uses `connectRef` pattern to avoid
circular `useCallback` dependency. Initial connect via `setTimeout(fn, 0)`
to avoid synchronous setState in useEffect.

### Config

| Setting | Default | Env Var |
|---------|---------|---------|
| Auth timeout | 10s | `WS_AUTH_TIMEOUT_SECONDS` |
| Ping interval | 30s | `WS_PING_INTERVAL_SECONDS` |

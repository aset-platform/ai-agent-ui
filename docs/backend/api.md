# API Reference

All API endpoints are served under the `/v1/` prefix. WebSocket and static-file mounts remain at root.

Base URL (development): `http://127.0.0.1:8181/v1`

!!! warning "Root endpoints removed"
    As of Mar 13, 2026 (ASETPLTFRM-20), root-mounted API routes (`/chat`, `/agents`, `/health`) have been removed. All API traffic must go through `/v1/`. WebSocket stays at `/ws/chat`; static files (e.g. `/avatars/`) stay at root.

---

## POST /v1/chat

Send a message to an agent and receive a complete response.

### Request

```http
POST /v1/chat
Content-Type: application/json
```

```json
{
  "message": "What time is it?",
  "history": [
    { "role": "user",      "content": "Hello" },
    { "role": "assistant", "content": "Hi! How can I help?" }
  ],
  "agent_id": "general"
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `message` | `string` | Yes | — | The user's latest message |
| `history` | `array` | No | `[]` | Prior conversation turns, oldest first |
| `agent_id` | `string` | No | `"general"` | ID of the agent to handle the request |

Each `history` item must have:

| Field | Type | Values |
|-------|------|--------|
| `role` | `string` | `"user"` or `"assistant"` |
| `content` | `string` | The message text |

Any history entry with a role other than `"user"` or `"assistant"` is silently ignored.

### Response — 200 OK

```json
{
  "response": "The current time is 2026-02-22 14:37:55.",
  "agent_id": "general"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `response` | `string` | The agent's natural-language reply |
| `agent_id` | `string` | Echoed from the request |

### Response — 404 Not Found

Returned when the requested `agent_id` is not registered.

```json
{
  "detail": "Agent 'unknown' not found"
}
```

### Response — 504 Gateway Timeout

Returned when the agentic loop does not complete within `agent_timeout_seconds` (default 900 s).

```json
{
  "detail": "Agent timed out"
}
```

### Response — 500 Internal Server Error

Returned when an unhandled exception occurs inside the agentic loop.

```json
{
  "detail": "Agent execution failed"
}
```

!!! note
    In all error cases the backend returns a proper `HTTPException`. Error details are never embedded in a `200` response body.

---

## POST /v1/chat/stream

Send a message to an agent and receive a live NDJSON stream of status events as the agentic loop progresses.  The frontend uses this endpoint so users see progress in real time rather than waiting in silence.

### Request

```http
POST /v1/chat/stream
Content-Type: application/json
```

The request body is identical to `POST /v1/chat`.

### Response — 200 OK (NDJSON stream)

```
Content-Type: application/x-ndjson
```

The response body is a stream of newline-delimited JSON objects, one per line:

```json
{"type": "thinking",   "iteration": 1}
{"type": "tool_start", "tool": "fetch_stock_data", "args": {"ticker": "AAPL"}}
{"type": "tool_done",  "tool": "fetch_stock_data", "preview": "Fetched 2516 rows..."}
{"type": "thinking",   "iteration": 2}
{"type": "tool_start", "tool": "analyse_stock_price", "args": {"ticker": "AAPL"}}
{"type": "tool_done",  "tool": "analyse_stock_price", "preview": "## AAPL Price Analysis..."}
{"type": "final",      "response": "Here is the full analysis...", "iterations": 2}
```

**Event types:**

| Type | Fields | When emitted |
|------|--------|-------------|
| `thinking` | `iteration` | Before each LLM invocation |
| `tool_start` | `tool`, `args` | Before each tool is called |
| `tool_done` | `tool`, `preview` (≤ 300 chars) | After each tool result |
| `warning` | `message` | When `MAX_ITERATIONS` is hit |
| `final` | `response`, `iterations` | Loop complete — full text response |
| `error` | `message` | An exception occurred inside the agent |
| `timeout` | `message` | The overall deadline was exceeded |

The stream always ends with either a `final`, `error`, or `timeout` event, then the connection closes.

### Response — 404 Not Found

Same as `POST /v1/chat`.

!!! tip "Consuming the stream (JavaScript)"
    ```javascript
    const res = await fetch(`${backendUrl}/v1/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history, agent_id }),
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.trim()) {
          const event = JSON.parse(line);
          // handle event.type === "thinking" | "tool_start" | etc.
        }
      }
    }
    ```

---

## GET /v1/agents

List all registered agents.

### Request

```http
GET /v1/agents
```

No request body or parameters.

### Response — 200 OK

```json
{
  "agents": [
    {
      "id": "general",
      "name": "General Agent",
      "description": "A general-purpose agent that can answer questions and search the web."
    }
  ]
}
```

Each agent object contains:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Agent ID used in `POST /v1/chat` `agent_id` field |
| `name` | `string` | Human-readable display name |
| `description` | `string` | One-sentence description of the agent's purpose |

---

## CORS

CORS is configured to allow all origins (`*`), all methods, and all headers. This is intentional for local development. Tighten the `allow_origins` list before deploying to production.

---

## Multi-Turn Conversations

The backend is stateless — it does not store conversation history between requests. The frontend is responsible for accumulating messages and sending the full `history` array with every request.

**Turn 1** — no history:
```json
{ "message": "Hello", "history": [] }
```

**Turn 2** — history includes Turn 1:
```json
{
  "message": "What time is it?",
  "history": [
    { "role": "user",      "content": "Hello" },
    { "role": "assistant", "content": "Hi! How can I help?" }
  ]
}
```

The backend converts each history entry to a LangChain `HumanMessage` or `AIMessage` and prepends them to the message array before invoking the LLM, giving the model full conversational context.

---

## Testing the API

Using curl:

```bash
# POST /v1/chat (synchronous)
curl -s -X POST http://127.0.0.1:8181/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What time is it?", "history": []}' | python3 -m json.tool

# POST /v1/chat/stream (NDJSON — prints events as they arrive)
curl -N -s -X POST http://127.0.0.1:8181/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "What time is it?", "history": []}'

# GET /v1/agents
curl -s http://127.0.0.1:8181/v1/agents | python3 -m json.tool

# GET /v1/health
curl -s http://127.0.0.1:8181/v1/health | python3 -m json.tool
```

FastAPI also generates interactive docs automatically:

- **Swagger UI**: [http://127.0.0.1:8181/docs](http://127.0.0.1:8181/docs)
- **ReDoc**: [http://127.0.0.1:8181/redoc](http://127.0.0.1:8181/redoc)

---

## Admin Endpoints (superuser only)

All admin endpoints require a valid JWT with `role: superuser` in the `Authorization: Bearer` header.

### GET /v1/admin/tier-health

Returns health status for each configured Groq LLM tier.

```json
{
  "tiers": [
    {
      "model": "llama-3.3-70b-versatile",
      "health": "healthy",
      "requests": 42,
      "successes": 42,
      "cascades": 0,
      "avg_latency_ms": 320.5,
      "p95_latency_ms": 580.0
    },
    {
      "model": "qwen/qwen3-32b",
      "health": "healthy",
      "requests": 10,
      "successes": 9,
      "cascades": 1,
      "avg_latency_ms": 380.4,
      "p95_latency_ms": 720.0
    }
  ],
  "summary": {
    "total": 4,
    "healthy": 2,
    "degraded": 1,
    "down": 0,
    "disabled": 1
  }
}
```

**Health classification** (5-minute sliding window):

| Status | Condition |
|--------|-----------|
| `healthy` | 0 failures in window |
| `degraded` | 1–3 failures in window |
| `down` | 4+ failures in window |
| `disabled` | Manually disabled via toggle |

### POST /v1/admin/tier-health/{model}/toggle

Enable or disable a specific Groq model tier. Disabled tiers are skipped during the cascade.

```bash
curl -X POST http://127.0.0.1:8181/v1/admin/tier-health/llama-3.3-70b-versatile/toggle \
  -H "Authorization: Bearer <superuser-jwt>"
```

```json
{
  "model": "llama-3.3-70b-versatile",
  "disabled": true
}
```

### GET /v1/admin/daily-budget

Returns aggregated daily Groq token usage across all models.

```json
{
  "date": "2026-04-01",
  "daily_limit": 2300000,
  "total_tokens": 55062,
  "remaining_tokens": 2244938,
  "usage_pct": 2.4,
  "by_model": {
    "llama-3.3-70b-versatile": {
      "total": 29497, "requests": 9, "limit": 100000
    },
    "qwen/qwen3-32b": {
      "total": 12451, "requests": 5, "limit": 500000
    }
  },
  "estimated_queries_remaining": 1250,
  "reset_time_utc": "2026-04-02T00:00:00+00:00"
}
```

---

## WebSocket — /ws/chat

The WebSocket endpoint remains at root (not under `/v1/`).

```
ws://127.0.0.1:8181/ws/chat
```

**Protocol:**

1. Client connects to `/ws/chat`
2. Client sends `{"type": "auth", "token": "<JWT>"}` within 10 s
3. Server replies `{"type": "auth_ok"}`
4. Client sends `{"type": "chat", "message": "...", "agent_id": "..."}` to stream
5. Server pushes `thinking`, `tool_start`, `tool_done`, `final`, `error`, `timeout` events
6. `ping` / `pong` keepalive supported at any time

**Close codes:** 4001 (auth failed), 4002 (auth timeout), 4003 (invalid format)

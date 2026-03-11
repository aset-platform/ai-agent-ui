# Streaming Protocol

## Endpoint

`POST /chat/stream` — returns NDJSON (newline-delimited JSON).

## Event Types

| Event | Description |
|-------|-------------|
| `thinking` | LLM reasoning step |
| `tool_start` | Tool invocation beginning |
| `tool_done` | Tool result returned |
| `warning` | Non-fatal issue |
| `final` | Complete response |
| `error` | Fatal error |

## Frontend Consumption

Use `apiFetch` (not raw `fetch`) for all backend calls — it
auto-refreshes JWT tokens.

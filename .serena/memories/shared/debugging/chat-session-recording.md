# Chat Session Recording Gotchas

## sendBeacon Cannot Send Custom Headers
`navigator.sendBeacon()` doesn't support `Authorization` headers → 401 on protected endpoints. Use `fetch()` with `keepalive: true` instead:
```typescript
fetch(url, {
  method: "POST",
  headers: { Authorization: `Bearer ${token}` },
  body: JSON.stringify(payload),
  keepalive: true,  // survives page unload
});
```

## PyArrow Timestamp Conversion
PyArrow can't convert ISO timestamp strings directly to `pa.timestamp("us")`. Error: "str cannot be converted to int" (misleading — it's a timestamp field).
Fix: parse with `datetime.fromisoformat()` before creating the PyArrow array, or use a `_parse_ts()` helper.

## Flush on All Exit Paths
Chat session data must be flushed on:
1. Sign out button click
2. Chat panel X (close) button
3. `beforeunload` event (page refresh/navigate away)
4. Tab/window close

Missing any path = lost session data. Audit all exit flows.

## apiFetch Race on Logout
`apiFetch` 401 handler calls `clearTokens()` + `window.location.href = "/login"`. If session flush races with logout, the flush request uses the cleared token → 401 → infinite loop.
Fix: use raw `fetch()` (not `apiFetch`) for the flush call.

## Groq LLM Tool-Forcing
Groq Llama 3.3 will hallucinate data instead of calling tools unless the system prompt is extremely forceful:
- "YOUR FIRST RESPONSE MUST ONLY be a tool call"
- "NEVER fabricate data — if unsure, call a tool"
- Currency rules: detect ₹/$ from ticker suffix (.NS/.BO = INR)
- Dynamic context: inject portfolio composition into system prompt

## Iceberg Epoch Dates
Corrupted ingestion can produce `date=1970-01-01` rows. TradingView charts crash on duplicate `time=0` values.
Fix: frontend date validation with regex `/^(19[89]\d|2\d{3})-/` to reject pre-1980 dates at aggregation input, filter output, and per-series data levels.
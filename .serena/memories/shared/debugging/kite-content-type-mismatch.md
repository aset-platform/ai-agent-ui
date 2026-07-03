# Kite kc.positions()/holdings() raises DataException on valid data

## Symptom
`kiteconnect.exceptions.DataException: Unknown Content-Type
(text/plain; charset=utf-8) with response: (b'{"status":"success",
"data":{"net": [], "day": []}}')`

Any UI/logic relying on `kc.positions()` or `kc.holdings()` silently
falls back to an empty/zero result whenever this fires — e.g. an
exposure figure showing ₹0 despite real open positions, or a
positions/holdings list rendering empty.

## Root cause
Kite's REST API occasionally serves a genuinely valid JSON response
body under `Content-Type: text/plain; charset=utf-8` instead of
`application/json`. The `kiteconnect` Python SDK's `_request()`
(`connect.py`) does a strict `"json" in content-type` sniff before
parsing, and raises `DataException` on any mismatch — discarding the
valid body, which it embeds verbatim (as a Python bytes-repr string)
in its own error message.

Any bare `kc.positions()` / `kc.holdings()` call (or
`asyncio.to_thread(kc.positions)`) wrapped only in a generic
`except Exception: return {}` silently drops good data on this path,
indistinguishable from a genuine API outage.

## Fix
`recover_from_kite_content_type_mismatch(exc)` +
`kite_call_tolerant(fn)` (sync) / `kite_call_tolerant_async(fn)`
(async) — parse the embedded bytes-repr via `ast.literal_eval` then
`json.loads`, return the recovered payload as if the call had
succeeded. Falls through to re-raising the original exception on ANY
parse failure or a genuine `"status": "error"` payload inside the
same message shape — never silently fabricates data on a real
failure, so existing fail-safe/fail-closed contracts at call sites
(e.g. "read failure → treat as UNKNOWN, do nothing") are preserved.

```python
def recover_from_kite_content_type_mismatch(exc: DataException) -> dict | None:
    msg = str(exc)
    marker = "with response: ("
    idx = msg.find(marker)
    if idx == -1:
        return None
    raw = msg[idx + len(marker):]
    if raw.endswith(")"):
        raw = raw[:-1]
    try:
        content_bytes = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(content_bytes, bytes):
        return None
    try:
        data = json.loads(content_bytes)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("status") != "success":
        return None
    return data.get("data")


def kite_call_tolerant(fn):
    try:
        return fn()
    except DataException as exc:
        recovered = recover_from_kite_content_type_mismatch(exc)
        if recovered is not None:
            return recovered
        raise


async def kite_call_tolerant_async(fn):
    try:
        return await asyncio.to_thread(fn)
    except DataException as exc:
        recovered = recover_from_kite_content_type_mismatch(exc)
        if recovered is not None:
            return recovered
        raise
```

Every `kc.positions()` / `kc.holdings()` call site MUST route through
one of these two wrappers instead of a bare call — never
`asyncio.to_thread(kc.positions)` or `kc.positions()` directly.

## Blast radius when auditing this class of bug
A single reported symptom (one broken display) does not mean one
broken call site. Grep the whole backend for the raw pattern
(`kc.positions`, `kc.holdings`, `_kc.positions()`, `_kc.holdings()`)
before considering a fix for this bug complete — it is easy for the
same unwrapped call to exist in several unrelated modules (routes,
runtime, kill-switch/emergency paths, position hydration). Pay
special attention to any site whose failure fallback is NOT a
fail-safe "treat as unknown, do nothing" — a fallback to an empty
list/dict on a Content-Type mismatch can silently understate or zero
out a real exposure figure used by a safety-critical decision (e.g.
an emergency flatten-all action computing a real position's
sell quantity as 0 and skipping it).

## Important
- `DataException` must be imported from `kiteconnect.exceptions` at
  the call site — a test double / stub module registered via
  `sys.modules.setdefault("kiteconnect.exceptions", ...)` in a
  conftest will only be picked up consistently if both the
  implementation and the test import it the normal way (`from
  kiteconnect.exceptions import DataException`), not via some
  alternate/lazy path.
- This is a genuinely intermittent, live-API behavior — not something
  reproducible via a stub in normal operation. Treat any fix as
  needing explicit end-to-end verification against the real API
  after deploying (log a distinct message on successful recovery vs.
  on failure, so you can confirm via logs post-deploy that recoveries
  are firing and failures have stopped).

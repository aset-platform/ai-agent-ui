# algo.events rows carry payload_json (string), not payload (dict)

## Symptom
A test asserting against `LiveRuntime`/paper runtime's in-memory
`self._events` list — the rows accumulated before an eventual
Iceberg flush — reports green (correctly finding zero matches for
some condition) even though the code under test never actually ran
the path being checked, or ran it and should have produced a match.

## Root cause
`backend/algo/backtest/event_writer.py::event_row()` returns:

```python
{
    "event_id": ..., "ts_ns": ..., "ts_date": ...,
    "session_id": ..., "user_id": ..., "strategy_id": ...,
    "mode": ..., "type": ...,
    "payload_json": json.dumps(payload, default=str),  # string!
    "written_at": ...,
}
```

There is no `payload` key on the row — the caller's `payload` dict
is JSON-serialized into `payload_json`. A filter like:

```python
e.get("payload", {}).get("reason") == "some_reason"
```

silently evaluates to `False` for every single row, because
`e.get("payload", {})` always returns the `{}` default (the key
doesn't exist) — `.get("reason")` on that is always `None`. This
does not raise, does not warn, and does not fail loudly: a test
asserting `assert not matches` on this filter PASSES regardless of
what the code actually did, because the filter never finds anything
to begin with.

## Fix
```python
import json

def _rejections(runtime, reason: str) -> list[dict]:
    out = []
    for e in runtime._events:
        if e.get("type") != "signal_rejected":
            continue
        payload = json.loads(e.get("payload_json") or "{}")
        if payload.get("reason") == reason:
            out.append(payload)
    return out
```

## How to apply
Any test inspecting `self._events` for a specific payload field
MUST `json.loads(e["payload_json"])` first. Never assert against a
bare `e.get("payload", ...)` on one of these rows — write a small
helper (as above) at the top of the test file rather than inlining
the `json.loads` at every assertion, and sanity-check a new
RED-phase test actually goes red for the reason you expect (a
broken filter that always returns `[]` will make almost any
"assert no X" test pass immediately, which looks identical to the
fix already working).

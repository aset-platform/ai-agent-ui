# PR2 — Move `live-ws` events out of Iceberg into Redis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop writing WS-lifecycle (`mode=live-ws`) events to the `algo.events` Iceberg table — route them to a per-user, TTL'd Redis sorted set and read the events panel from there — so the dominant `algo.events` writer (≈67% of files; 260k `ws_backpressure_drop` rows in one incident) no longer bloats the table.

**Architecture:** A new best-effort `ws_event_store` module wraps a per-user Redis sorted set `algo:ws-events:{user_id}` (score = `ts_ns`), capped to 1,000 entries with a 7-day TTL. `KiteWsMultiplexer._emit_ws_event` writes there instead of buffering Iceberg rows. The `GET /v1/algo/paper/events` endpoint, when `mode=live-ws`, reads from the store instead of querying Iceberg. Redis-absent / Redis-error is a silent no-op (these are observability records, not compliance data); `ws_disconnected` / `ws_auth_failed` keep their WARN logs for forensics.

**Tech Stack:** Python 3.12, redis-py (shared client via `auth.token_store.get_redis_client`), FastAPI, pytest. Tests run inside the backend container: `docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 python -m pytest <path> -v`.

---

## File Structure

- **Create** `backend/algo/broker/ws_event_store.py` — the Redis sorted-set store. Two public functions: `record_ws_event(...)` (write) and `read_ws_events(...)` (panel read). Owns the key schema, cap, TTL, and graceful no-op. One responsibility: persistence of WS-lifecycle events.
- **Create** `backend/algo/tests/test_ws_event_store.py` — unit tests with a fake Redis client (no live Redis needed).
- **Modify** `backend/algo/broker/ws_multiplexer.py` — repoint `_emit_ws_event` to `record_ws_event`; delete the Iceberg `_ws_events` buffer + `_flush_events`; keep the backpressure aggregation (PR1) and the WARN logs.
- **Modify** `backend/algo/tests/test_ws_backpressure.py` — the PR1 aggregation tests assert on `mux._ws_events` (removed by this PR); re-point them at a captured `record_ws_event`.
- **Modify** `backend/algo/routes/paper.py` — in `list_events`, branch `mode == "live-ws"` to `read_ws_events`.

> Note: the WARN-log assertion in `test_ws_backpressure.py::test_backpressure_emits_warning_log` is unaffected (the WARN comes from `_emit_backpressure_summary`, not the sink).

---

## Pre-flight (one-time verification, not a code change)

- [ ] **Confirm the shared Redis client decodes responses to `str`.**

Run:
```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 \
  python3 -c "import os; from auth.token_store import get_redis_client; c=get_redis_client(os.environ['REDIS_URL']); c.set('t:probe','x'); print(type(c.get('t:probe')))"
```
Expected: `<class 'str'>`. If it prints `<class 'bytes'>`, the `read_ws_events` code below already handles it (decodes `bytes` members before `json.loads`). No change needed either way — this just tells you which path runs.

---

## Task 1: `ws_event_store` — Redis sorted-set persistence

**Files:**
- Create: `backend/algo/broker/ws_event_store.py`
- Test: `backend/algo/tests/test_ws_event_store.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/algo/tests/test_ws_event_store.py`:
```python
"""Unit tests for the Redis-backed live-ws event store."""
from __future__ import annotations

import json
from uuid import uuid4

import backend.algo.broker.ws_event_store as store


class _FakeRedis:
    """Minimal in-memory stand-in for the sorted-set ops we use."""

    def __init__(self):
        self.z: dict[str, list[tuple[float, str]]] = {}
        self.ttl: dict[str, int] = {}

    def pipeline(self):
        return _FakePipe(self)

    def zadd(self, key, mapping):
        bucket = self.z.setdefault(key, [])
        for member, score in mapping.items():
            bucket.append((float(score), member))
        bucket.sort()

    def zremrangebyrank(self, key, start, end):
        bucket = self.z.get(key, [])
        n = len(bucket)
        if n == 0:
            return
        rng = set(range(start % n, (end % n) + 1))
        self.z[key] = [b for i, b in enumerate(bucket) if i not in rng]

    def expire(self, key, ttl):
        self.ttl[key] = ttl

    def zrevrange(self, key, start, end):
        bucket = sorted(self.z.get(key, []), reverse=True)
        return [m for _s, m in bucket[start:end + 1]]


class _FakePipe:
    def __init__(self, r):
        self.r = r
        self.ops = []

    def zadd(self, *a):
        self.ops.append(("zadd", a))
        return self

    def zremrangebyrank(self, *a):
        self.ops.append(("zremrangebyrank", a))
        return self

    def expire(self, *a):
        self.ops.append(("expire", a))
        return self

    def execute(self):
        for name, a in self.ops:
            getattr(self.r, name)(*a)
        self.ops = []


def test_record_then_read_roundtrip(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(store, "_client", lambda: fake)
    uid = uuid4()

    store.record_ws_event(
        user_id=uid, event_id="e1", ts_ns=1000, type_="ws_connected",
        strategy_id=None, payload={"a": 1},
    )
    store.record_ws_event(
        user_id=uid, event_id="e2", ts_ns=2000,
        type_="ws_backpressure_drop", strategy_id="s1",
        payload={"dropped": 5},
    )

    rows = store.read_ws_events(user_id=uid, limit=10)
    assert [r["event_id"] for r in rows] == ["e2", "e1"]  # newest first
    assert rows[0]["type"] == "ws_backpressure_drop"
    assert rows[0]["payload"] == {"dropped": 5}
    assert "ts_date" in rows[0]
    assert fake.ttl[f"algo:ws-events:{uid}"] == store._TTL_S


def test_read_filters_by_type(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(store, "_client", lambda: fake)
    uid = uuid4()
    store.record_ws_event(
        user_id=uid, event_id="e1", ts_ns=1, type_="ws_connected",
        strategy_id=None, payload={},
    )
    store.record_ws_event(
        user_id=uid, event_id="e2", ts_ns=2, type_="ws_disconnected",
        strategy_id=None, payload={},
    )
    rows = store.read_ws_events(user_id=uid, type_="ws_disconnected")
    assert [r["event_id"] for r in rows] == ["e2"]


def test_noop_when_redis_absent(monkeypatch):
    monkeypatch.setattr(store, "_client", lambda: None)
    uid = uuid4()
    assert store.record_ws_event(
        user_id=uid, event_id="e", ts_ns=1, type_="x",
        strategy_id=None, payload={},
    ) is False
    assert store.read_ws_events(user_id=uid) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 \
  python -m pytest backend/algo/tests/test_ws_event_store.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.algo.broker.ws_event_store'`.

- [ ] **Step 3: Write the module**

Create `backend/algo/broker/ws_event_store.py`:
```python
"""Redis-backed store for WS-lifecycle (``live-ws``) events.

ws_connected / ws_disconnected / ws_auth_failed / ws_gap_filled /
ws_backpressure_drop are 7-day observability noise. Writing them to the
``algo.events`` Iceberg log produced GBs of snapshot metadata for ~50 MB
of data (incident 2026-06-18). They now live in a per-user Redis sorted
set (score = ts_ns), capped + TTL'd; the events panel reads them here.
Best-effort: absent ``REDIS_URL`` or any Redis error is a silent no-op —
these are not compliance records.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

_logger = logging.getLogger(__name__)

_TTL_S = 7 * 24 * 3600          # 7-day retention (matches Iceberg policy)
_MAX_EVENTS = 1_000             # ring-buffer cap per user
_KEY = "algo:ws-events:{user_id}"


def _client():
    """Return the shared Redis client, or None when unavailable."""
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    try:
        from auth.token_store import get_redis_client

        return get_redis_client(url)
    except Exception:  # noqa: BLE001
        return None


def _ts_date(ts_ns: int) -> str:
    return (
        datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        .date()
        .isoformat()
    )


def record_ws_event(
    *,
    user_id: UUID,
    event_id: str,
    ts_ns: int,
    type_: str,
    strategy_id: str | None,
    payload: dict[str, Any],
) -> bool:
    """Append a WS-lifecycle event to the user's sorted set. Returns
    True if stored, False on no-op. Never raises."""
    client = _client()
    if client is None:
        return False
    key = _KEY.format(user_id=user_id)
    member = json.dumps(
        {
            "event_id": event_id,
            "ts_ns": int(ts_ns),
            "type": type_,
            "strategy_id": strategy_id,
            "payload": payload,
        },
        default=str,
    )
    try:
        pipe = client.pipeline()
        pipe.zadd(key, {member: int(ts_ns)})
        # Keep only the newest _MAX_EVENTS (drop the lowest-scored).
        pipe.zremrangebyrank(key, 0, -(_MAX_EVENTS + 1))
        pipe.expire(key, _TTL_S)
        pipe.execute()
        return True
    except Exception:  # noqa: BLE001
        _logger.warning("ws_event_store: record failed", exc_info=True)
        return False


def read_ws_events(
    *,
    user_id: UUID,
    limit: int = 100,
    offset: int = 0,
    type_: str | None = None,
    since_ts_ns: int | None = None,
) -> list[dict[str, Any]]:
    """Newest-first WS-lifecycle events, shaped like the Iceberg events
    endpoint. No-op → []."""
    client = _client()
    if client is None:
        return []
    key = _KEY.format(user_id=user_id)
    try:
        raw = client.zrevrange(key, 0, _MAX_EVENTS - 1)
    except Exception:  # noqa: BLE001
        _logger.warning("ws_event_store: read failed", exc_info=True)
        return []
    out: list[dict[str, Any]] = []
    for m in raw:
        if isinstance(m, bytes):  # client without decode_responses
            m = m.decode()
        try:
            ev = json.loads(m)
        except Exception:  # noqa: BLE001
            continue
        if type_ is not None and ev.get("type") != type_:
            continue
        ts = int(ev.get("ts_ns", 0))
        if since_ts_ns is not None and ts < since_ts_ns:
            continue
        out.append(
            {
                "event_id": ev.get("event_id"),
                "ts_ns": ts,
                "ts_date": _ts_date(ts),
                "strategy_id": ev.get("strategy_id"),
                "type": ev.get("type"),
                "payload": ev.get("payload", {}),
            }
        )
    return out[offset:offset + limit]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 \
  python -m pytest backend/algo/tests/test_ws_event_store.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Lint**

Run:
```bash
docker exec -i -w /app ai-agent-ui-backend-1 \
  flake8 backend/algo/broker/ws_event_store.py backend/algo/tests/test_ws_event_store.py
```
Expected: no output (clean).

- [ ] **Step 6: Commit**

```bash
git add backend/algo/broker/ws_event_store.py backend/algo/tests/test_ws_event_store.py
git commit -m "feat(algo): PR2.1 — Redis sorted-set store for live-ws events"
```

---

## Task 2: Route `_emit_ws_event` to Redis; drop the Iceberg buffer

**Files:**
- Modify: `backend/algo/broker/ws_multiplexer.py` (`_emit_ws_event`, `_flush_events`, `__init__`, `close`)
- Modify: `backend/algo/tests/test_ws_backpressure.py` (PR1 tests assert on the removed `_ws_events`)

- [ ] **Step 1: Update the PR1 tests to assert on the store**

In `backend/algo/tests/test_ws_backpressure.py`, add this helper after the imports:
```python
import backend.algo.broker.ws_event_store as _store


def _capture_ws_events(monkeypatch):
    """Capture record_ws_event calls into a list of payload dicts."""
    captured: list[dict] = []

    def _fake(*, user_id, event_id, ts_ns, type_, strategy_id, payload):
        captured.append(
            {"type": type_, "payload": payload, "ts_ns": ts_ns}
        )
        return True

    monkeypatch.setattr(_store, "record_ws_event", _fake)
    return captured
```
Replace the three PR1 aggregation tests with versions that capture via the store (they no longer use `mux._ws_events` or `json.loads`):
```python
def test_backpressure_aggregates_within_window(monkeypatch):
    mux = _make_mux()
    captured = _capture_ws_events(monkeypatch)
    sid = uuid4()
    for _ in range(500):
        mux._record_backpressure_event(strategy_id=sid, token=111)
    bp = [e for e in captured if e["type"] == "ws_backpressure_drop"]
    assert len(bp) == 1
    assert bp[0]["payload"]["dropped"] == 1
    assert mux._bp_drops[sid] == 499


def test_backpressure_summary_carries_count_on_window_roll(monkeypatch):
    mux = _make_mux()
    captured = _capture_ws_events(monkeypatch)
    sid = uuid4()
    for _ in range(500):
        mux._record_backpressure_event(strategy_id=sid, token=111)
    mux._bp_last_emit_ns[sid] -= _BP_AGG_WINDOW_NS + 1_000_000_000
    mux._record_backpressure_event(strategy_id=sid, token=111)
    bp = [e for e in captured if e["type"] == "ws_backpressure_drop"]
    assert len(bp) == 2
    assert bp[1]["payload"]["dropped"] == 500
    assert bp[1]["payload"]["window_s"] == _BP_AGG_WINDOW_S
    assert sid not in mux._bp_drops


def test_backpressure_residual_flushed_on_close(monkeypatch):
    mux = _make_mux()
    captured = _capture_ws_events(monkeypatch)
    sid = uuid4()
    for _ in range(10):
        mux._record_backpressure_event(strategy_id=sid, token=111)
    assert mux._bp_drops[sid] == 9
    mux._flush_backpressure_residual()
    bp = [e for e in captured if e["type"] == "ws_backpressure_drop"]
    assert len(bp) == 2
    assert bp[1]["payload"]["dropped"] == 9
    assert not mux._bp_drops
```
Delete `test_backpressure_records_event` (it asserts on `mux._ws_events`, which this PR removes; coverage moves to `test_emit_routes_to_store_not_iceberg` in Step 4). Remove the now-unused `import json` if nothing else uses it.

- [ ] **Step 2: Run the backpressure tests to verify they fail**

Run:
```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 \
  python -m pytest backend/algo/tests/test_ws_backpressure.py -v
```
Expected: FAIL — `captured` is empty because `_emit_ws_event` still buffers Iceberg rows (not yet wired to `record_ws_event`).

- [ ] **Step 3: Repoint `_emit_ws_event` and remove the Iceberg buffer**

In `backend/algo/broker/ws_multiplexer.py`:

(a) Replace `_emit_ws_event` (builds an `event_row`, appends to `self._ws_events`) with:
```python
    def _emit_ws_event(
        self,
        type_: str,
        payload: dict[str, Any],
    ) -> None:
        """Persist a WS-lifecycle event to the per-user Redis store.

        7-day observability records — NOT written to the algo.events
        Iceberg log (incident 2026-06-18). Best-effort: a Redis failure
        is swallowed inside record_ws_event."""
        from uuid import uuid4

        from backend.algo.broker.ws_event_store import record_ws_event

        ts_ns = int(time.time() * 1_000_000_000)
        record_ws_event(
            user_id=self._user_id,
            event_id=str(uuid4()),
            ts_ns=ts_ns,
            type_=type_,
            strategy_id=payload.get("strategy_id"),
            payload=payload,
        )
```

(b) Delete the entire `_flush_events` method.

(c) In `__init__`, delete the line `self._ws_events: list[dict[str, Any]] = []`.

(d) In `close`, replace:
```python
        # Flush any pending backpressure counts, then all WS events.
        self._flush_backpressure_residual()
        if self._ws_events:
            self._flush_events()
```
with:
```python
        # Flush any pending backpressure counts (→ Redis store).
        self._flush_backpressure_residual()
```

- [ ] **Step 4: Add a routing test**

Append to `backend/algo/tests/test_ws_backpressure.py`:
```python
def test_emit_routes_to_store_not_iceberg(monkeypatch):
    """_emit_ws_event persists via record_ws_event; the multiplexer
    keeps no Iceberg buffer."""
    mux = _make_mux()
    captured = _capture_ws_events(monkeypatch)
    mux._emit_ws_event("ws_connected", {"strategy_id": None})
    assert len(captured) == 1
    assert captured[0]["type"] == "ws_connected"
    assert not hasattr(mux, "_ws_events")
```

- [ ] **Step 5: Run the backpressure tests to verify they pass**

Run:
```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 \
  python -m pytest backend/algo/tests/test_ws_backpressure.py -v
```
Expected: all pass (`test_backpressure_drop_oldest_on_overflow`, `test_backpressure_emits_warning_log`, `test_normal_throughput_does_not_drop`, the 3 aggregation tests, `test_emit_routes_to_store_not_iceberg`).

- [ ] **Step 6: Lint + commit**

```bash
docker exec -i -w /app ai-agent-ui-backend-1 \
  flake8 backend/algo/broker/ws_multiplexer.py backend/algo/tests/test_ws_backpressure.py
git add backend/algo/broker/ws_multiplexer.py backend/algo/tests/test_ws_backpressure.py
git commit -m "feat(algo): PR2.2 — multiplexer writes live-ws events to Redis, not Iceberg"
```

---

## Task 3: Read the panel from Redis for `mode=live-ws`

**Files:**
- Modify: `backend/algo/routes/paper.py` (`list_events`, ~line 252)
- Test: `backend/algo/tests/test_live_ws_events_panel.py` (create)

- [ ] **Step 1: Write the shape-contract test**

Create `backend/algo/tests/test_live_ws_events_panel.py`:
```python
"""read_ws_events returns the panel dict shape the route depends on."""
from __future__ import annotations

import json
from uuid import uuid4

import backend.algo.broker.ws_event_store as store


def test_read_ws_events_shape(monkeypatch):
    class _Fake:
        def zrevrange(self, key, a, b):
            return [json.dumps({
                "event_id": "e1", "ts_ns": 5, "type": "ws_connected",
                "strategy_id": None, "payload": {"k": "v"},
            })]

    monkeypatch.setattr(store, "_client", lambda: _Fake())
    rows = store.read_ws_events(user_id=uuid4(), limit=10)
    assert rows == [{
        "event_id": "e1", "ts_ns": 5,
        "ts_date": store._ts_date(5),
        "strategy_id": None, "type": "ws_connected",
        "payload": {"k": "v"},
    }]
```

- [ ] **Step 2: Run it (passes — pins the contract the route relies on)**

Run:
```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 \
  python -m pytest backend/algo/tests/test_live_ws_events_panel.py -v
```
Expected: PASS (fails only if `read_ws_events`'s output shape drifts from the route's `out` dicts).

- [ ] **Step 3: Branch the endpoint on `mode=live-ws`**

In `backend/algo/routes/paper.py::list_events`, immediately after `user_id_str = str(UUID(user.user_id))`, insert:
```python
        if mode == "live-ws":
            from backend.algo.broker.ws_event_store import read_ws_events

            evs = read_ws_events(
                user_id=UUID(user.user_id),
                limit=limit,
                offset=offset,
                type_=type,
            )
            response.headers["X-Total-Count"] = str(len(evs))
            response.headers["Access-Control-Expose-Headers"] = (
                "X-Total-Count"
            )
            return evs
```
(`since_date` is intentionally not applied — the store is already 7-day-bounded and capped; live-ws panels don't use it.)

- [ ] **Step 4: Run the panel + store tests**

Run:
```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 \
  python -m pytest backend/algo/tests/test_live_ws_events_panel.py backend/algo/tests/test_ws_event_store.py -v
```
Expected: all pass.

- [ ] **Step 5: Lint + commit**

```bash
docker exec -i -w /app ai-agent-ui-backend-1 \
  flake8 backend/algo/routes/paper.py backend/algo/tests/test_live_ws_events_panel.py
git add backend/algo/routes/paper.py backend/algo/tests/test_live_ws_events_panel.py
git commit -m "feat(algo): PR2.3 — events panel reads live-ws from Redis"
```

---

## Task 4: Integration verification (no new Iceberg writes)

**Files:** none (verification + restart).

- [ ] **Step 1: Restart backend to load the multiplexer + route changes**

Run `./run.sh restart backend` and wait for `Application startup complete`.
(Required: the running process caches the old `ws_multiplexer` / route modules — §6.2.)

- [ ] **Step 2: After a ~2-min live run, assert zero new Iceberg live-ws rows**

```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 python3 -c "
from backend.db.duckdb_engine import query_iceberg_table, invalidate_metadata
import time
invalidate_metadata('algo.events')
since=int(time.time()*1e9)-5*60*10**9
r=query_iceberg_table('algo.events',\"SELECT COUNT(*) n FROM events WHERE mode='live-ws' AND ts_ns>?\",[since])
print('new live-ws Iceberg rows (last 5 min):', r[0]['n'])
"
```
Expected: `0`.

- [ ] **Step 3: Assert live-ws events landed in Redis**

```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 python3 -c "
import os; from auth.token_store import get_redis_client
c=get_redis_client(os.environ['REDIS_URL'])
keys=[k for k in c.scan_iter('algo:ws-events:*')]
print('ws-event keys:', keys)
print('sample zcard:', c.zcard(keys[0]) if keys else 0)
"
```
Expected: at least one `algo:ws-events:{uuid}` key with non-zero `zcard`.

- [ ] **Step 4: Mark PR2 done + commit docs**

Edit `docs/plans/2026-06-18-algo-events-bloat-redesign.md` PR 2 section → **DONE** with the PR2.1/2.2/2.3 commit shas.
```bash
git add docs/plans/2026-06-18-algo-events-bloat-redesign.md
git commit -m "docs(algo): mark PR2 (live-ws -> Redis) done"
```

---

## Self-Review

**1. Spec coverage** (PR 2 in `docs/plans/2026-06-18-algo-events-bloat-redesign.md`):
- "Route `mode=live-ws` events to a Redis sorted set `algo:ws-events:{user_id}` (score=ts_ns), 7-day TTL" → Task 1 (`record_ws_event`, `_TTL_S`, `_KEY`). ✓
- "no-op when `REDIS_URL` empty" → `_client()` returns None → both functions no-op; `test_noop_when_redis_absent`. ✓
- "Repoint the events-panel read path for live-ws mode" → Task 3. ✓
- "Keep `ws_disconnected` / `ws_auth_failed` as WARN logs" → those WARN logs live at their call sites (`on_close`, `on_error`, auth-fail path), untouched by this PR — only `_emit_ws_event`'s sink changes. Task 2 review must confirm no WARN log was deleted. ✓
- "Acceptance: zero live-ws rows in algo.events; panel renders from Redis" → Task 4 Steps 2–3. ✓

**2. Placeholder scan:** none — every code step is complete and runnable. (Earlier draft's `_FakeRedis.zremrangebyrank` had a vestigial line; the version above is clean.)

**3. Type consistency:** `record_ws_event` / `read_ws_events` keyword-only signatures are identical at the module, the multiplexer call site (Task 2 Step 3), the route call site (Task 3 Step 3), and all tests. Output keys (`event_id, ts_ns, ts_date, strategy_id, type, payload`) match `paper.py::list_events` (lines 330-338). `_BP_AGG_WINDOW_NS`/`_BP_AGG_WINDOW_S` are already imported in `test_ws_backpressure.py` (from PR1).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-18-pr2-live-ws-redis.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with checkpoints for review.

Which approach?

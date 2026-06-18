"""Unit tests for the Redis-backed live-ws event store."""
from __future__ import annotations

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
        # Resolve negative indices the same way Redis does: -1 = last.
        s = start if start >= 0 else max(0, n + start)
        e = end if end >= 0 else n + end
        if s > e or s >= n:
            return
        rng = set(range(s, min(e, n - 1) + 1))
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

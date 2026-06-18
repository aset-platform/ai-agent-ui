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

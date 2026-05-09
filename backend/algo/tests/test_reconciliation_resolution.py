"""Tests for drift resolution logic in reconcile_user (V2-3).

When a previously-drifting symbol is resolved (broker now agrees),
exactly one ``drift_resolved`` event is emitted — not zero, not two.
After resolution, if drift re-appears, a new ``position_drift_detected``
event fires.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

os.environ.setdefault(
    "BYO_SECRET_KEY",
    "Q3RZ8h3tQq2c5rVH0hWv0cHXh2OtdJv6f4M6Y9pQ8mE=",
)


@pytest.mark.asyncio
async def test_drift_resolved_emits_event_once():
    """Drift clears → exactly one drift_resolved event."""
    user_id = uuid4()
    symbol = "RELIANCE.NS"

    drift_repo = AsyncMock()
    drift_repo.get_drift_threshold = AsyncMock(return_value=0)
    # Open drift exists from previous run
    drift_repo.get_open_drifts = AsyncMock(
        return_value=[{"symbol": symbol}],
    )
    drift_repo.upsert_drift = AsyncMock(return_value=1)
    drift_repo.resolve_drift = AsyncMock(return_value=True)

    flushed: list = []

    with (
        patch(
            "backend.algo.live.reconciliation.DriftRepo",
            return_value=drift_repo,
        ),
        patch(
            "backend.algo.live.reconciliation."
            "_fetch_our_positions",
            new=AsyncMock(return_value={symbol: 100}),
        ),
        # Broker now agrees — no drift
        patch(
            "backend.algo.live.reconciliation."
            "_fetch_broker_positions",
            new=AsyncMock(return_value={symbol: 100}),
        ),
        patch(
            "backend.algo.live.reconciliation.flush_events",
            side_effect=lambda rows: flushed.extend(rows),
        ),
    ):
        from backend.algo.live.reconciliation import reconcile_user
        result = await reconcile_user(user_id)

    import json as _json
    assert result["events_emitted"] == 1
    assert len(flushed) == 1
    assert flushed[0]["type"] == "drift_resolved"
    payload = _json.loads(flushed[0]["payload_json"])
    assert payload["symbol"] == symbol
    drift_repo.resolve_drift.assert_awaited_once_with(
        user_id, symbol,
    )


@pytest.mark.asyncio
async def test_drift_resolved_only_once_not_twice():
    """resolve_drift returning False → no drift_resolved event."""
    user_id = uuid4()
    symbol = "INFY.NS"

    drift_repo = AsyncMock()
    drift_repo.get_drift_threshold = AsyncMock(return_value=0)
    drift_repo.get_open_drifts = AsyncMock(
        return_value=[{"symbol": symbol}],
    )
    drift_repo.upsert_drift = AsyncMock(return_value=1)
    # Already resolved — repo returns False
    drift_repo.resolve_drift = AsyncMock(return_value=False)

    flushed: list = []

    with (
        patch(
            "backend.algo.live.reconciliation.DriftRepo",
            return_value=drift_repo,
        ),
        patch(
            "backend.algo.live.reconciliation."
            "_fetch_our_positions",
            new=AsyncMock(return_value={symbol: 100}),
        ),
        patch(
            "backend.algo.live.reconciliation."
            "_fetch_broker_positions",
            new=AsyncMock(return_value={symbol: 100}),
        ),
        patch(
            "backend.algo.live.reconciliation.flush_events",
            side_effect=lambda rows: flushed.extend(rows),
        ),
    ):
        from backend.algo.live.reconciliation import reconcile_user
        result = await reconcile_user(user_id)

    assert result["events_emitted"] == 0
    assert len(flushed) == 0


@pytest.mark.asyncio
async def test_new_drift_after_resolution_emits_detected_again():
    """After drift is resolved, a re-appearing drift emits detected."""
    user_id = uuid4()
    symbol = "WIPRO.NS"

    drift_repo = AsyncMock()
    drift_repo.get_drift_threshold = AsyncMock(return_value=0)
    drift_repo.resolve_drift = AsyncMock(return_value=True)

    flushed: list = []

    with (
        patch(
            "backend.algo.live.reconciliation.DriftRepo",
            return_value=drift_repo,
        ),
        patch(
            "backend.algo.live.reconciliation.flush_events",
            side_effect=lambda rows: flushed.extend(rows),
        ),
    ):
        from backend.algo.live.reconciliation import reconcile_user

        # Phase 1: drift appears
        drift_repo.get_open_drifts = AsyncMock(return_value=[])
        drift_repo.upsert_drift = AsyncMock(return_value=1)
        with (
            patch(
                "backend.algo.live.reconciliation."
                "_fetch_our_positions",
                new=AsyncMock(return_value={symbol: 50}),
            ),
            patch(
                "backend.algo.live.reconciliation."
                "_fetch_broker_positions",
                new=AsyncMock(return_value={symbol: 100}),
            ),
        ):
            r1 = await reconcile_user(user_id)
        assert r1["events_emitted"] == 1
        assert flushed[-1]["type"] == "position_drift_detected"

        # Phase 2: drift resolves
        drift_repo.get_open_drifts = AsyncMock(
            return_value=[{"symbol": symbol}],
        )
        with (
            patch(
                "backend.algo.live.reconciliation."
                "_fetch_our_positions",
                new=AsyncMock(return_value={symbol: 100}),
            ),
            patch(
                "backend.algo.live.reconciliation."
                "_fetch_broker_positions",
                new=AsyncMock(return_value={symbol: 100}),
            ),
        ):
            r2 = await reconcile_user(user_id)
        assert r2["events_emitted"] == 1
        assert flushed[-1]["type"] == "drift_resolved"

        # Phase 3: drift re-appears (not in open_drifts since resolved)
        drift_repo.get_open_drifts = AsyncMock(return_value=[])
        drift_repo.upsert_drift = AsyncMock(return_value=1)
        with (
            patch(
                "backend.algo.live.reconciliation."
                "_fetch_our_positions",
                new=AsyncMock(return_value={symbol: 50}),
            ),
            patch(
                "backend.algo.live.reconciliation."
                "_fetch_broker_positions",
                new=AsyncMock(return_value={symbol: 100}),
            ),
        ):
            r3 = await reconcile_user(user_id)
        assert r3["events_emitted"] == 1
        assert flushed[-1]["type"] == "position_drift_detected"


@pytest.mark.asyncio
async def test_multiple_symbols_partial_resolution():
    """2 drifts: 1 resolves, 1 persists → 1 resolved event only."""
    user_id = uuid4()

    drift_repo = AsyncMock()
    drift_repo.get_drift_threshold = AsyncMock(return_value=0)
    # Both were drifting
    drift_repo.get_open_drifts = AsyncMock(
        return_value=[
            {"symbol": "RELIANCE.NS"},
            {"symbol": "INFY.NS"},
        ],
    )
    drift_repo.upsert_drift = AsyncMock(return_value=2)
    drift_repo.resolve_drift = AsyncMock(return_value=True)

    flushed: list = []

    with (
        patch(
            "backend.algo.live.reconciliation.DriftRepo",
            return_value=drift_repo,
        ),
        patch(
            "backend.algo.live.reconciliation."
            "_fetch_our_positions",
            new=AsyncMock(
                return_value={"RELIANCE.NS": 50, "INFY.NS": 100},
            ),
        ),
        # Broker: RELIANCE.NS resolved, INFY.NS still drifting
        patch(
            "backend.algo.live.reconciliation."
            "_fetch_broker_positions",
            new=AsyncMock(
                return_value={"RELIANCE.NS": 50, "INFY.NS": 200},
            ),
        ),
        patch(
            "backend.algo.live.reconciliation.flush_events",
            side_effect=lambda rows: flushed.extend(rows),
        ),
    ):
        from backend.algo.live.reconciliation import reconcile_user
        result = await reconcile_user(user_id)

    # 1 drift_resolved (RELIANCE.NS), 0 detected (INFY.NS was already open)
    types = [e["type"] for e in flushed]
    assert types.count("drift_resolved") == 1
    assert types.count("position_drift_detected") == 0
    assert result["events_emitted"] == 1

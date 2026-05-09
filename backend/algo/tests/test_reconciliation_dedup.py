"""Tests for the dedup logic in reconcile_user (V2-3).

Same diff on consecutive runs emits exactly one
``position_drift_detected`` event, not one per run.
The consecutive_runs counter is bumped but no second event fires.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

os.environ.setdefault(
    "BYO_SECRET_KEY",
    "Q3RZ8h3tQq2c5rVH0hWv0cHXh2OtdJv6f4M6Y9pQ8mE=",
)


@pytest.mark.asyncio
async def test_first_drift_emits_event():
    """Initial detection → exactly one position_drift_detected."""
    user_id = uuid4()
    symbol = "RELIANCE.NS"

    drift_repo = AsyncMock()
    drift_repo.get_drift_threshold = AsyncMock(return_value=0)
    drift_repo.get_open_drifts = AsyncMock(return_value=[])
    drift_repo.upsert_drift = AsyncMock(return_value=1)
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
            new=AsyncMock(
                return_value={symbol: 50},
            ),
        ),
        patch(
            "backend.algo.live.reconciliation."
            "_fetch_broker_positions",
            new=AsyncMock(
                return_value={symbol: 100},
            ),
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
    assert flushed[0]["type"] == "position_drift_detected"
    payload = _json.loads(flushed[0]["payload_json"])
    assert payload["symbol"] == symbol


@pytest.mark.asyncio
async def test_second_identical_run_bumps_counter_no_event():
    """Second run with same drift → counter bumped, zero new events."""
    user_id = uuid4()
    symbol = "INFY.NS"

    # Simulate open drift already exists
    drift_repo = AsyncMock()
    drift_repo.get_drift_threshold = AsyncMock(return_value=0)
    drift_repo.get_open_drifts = AsyncMock(
        return_value=[{"symbol": symbol}],
    )
    drift_repo.upsert_drift = AsyncMock(return_value=2)
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
            new=AsyncMock(
                return_value={symbol: 50},
            ),
        ),
        patch(
            "backend.algo.live.reconciliation."
            "_fetch_broker_positions",
            new=AsyncMock(
                return_value={symbol: 100},
            ),
        ),
        patch(
            "backend.algo.live.reconciliation.flush_events",
            side_effect=lambda rows: flushed.extend(rows),
        ),
    ):
        from backend.algo.live.reconciliation import reconcile_user
        result = await reconcile_user(user_id)

    # upsert_drift was called to bump counter
    drift_repo.upsert_drift.assert_awaited_once()
    # No new event because drift was already in open_rows
    assert result["events_emitted"] == 0
    assert len(flushed) == 0


@pytest.mark.asyncio
async def test_consecutive_runs_tracked():
    """Three consecutive identical runs → counter = 3."""
    user_id = uuid4()
    symbol = "WIPRO.NS"
    call_count = [0]

    drift_repo = AsyncMock()
    drift_repo.get_drift_threshold = AsyncMock(return_value=0)
    drift_repo.resolve_drift = AsyncMock(return_value=False)

    async def _mock_upsert(uid, sym, payload):
        call_count[0] += 1
        return call_count[0]

    drift_repo.upsert_drift = _mock_upsert

    # Run 1: no prior open drifts
    drift_repo.get_open_drifts = AsyncMock(return_value=[])

    flushed: list = []

    ctx = {
        "backend.algo.live.reconciliation.DriftRepo": drift_repo,
        "backend.algo.live.reconciliation."
        "_fetch_our_positions": AsyncMock(
            return_value={symbol: 10},
        ),
        "backend.algo.live.reconciliation."
        "_fetch_broker_positions": AsyncMock(
            return_value={symbol: 20},
        ),
        "backend.algo.live.reconciliation.flush_events": (
            lambda rows: flushed.extend(rows)
        ),
    }

    with (
        patch(
            "backend.algo.live.reconciliation.DriftRepo",
            return_value=drift_repo,
        ),
        patch(
            "backend.algo.live.reconciliation."
            "_fetch_our_positions",
            new=AsyncMock(return_value={symbol: 10}),
        ),
        patch(
            "backend.algo.live.reconciliation."
            "_fetch_broker_positions",
            new=AsyncMock(return_value={symbol: 20}),
        ),
        patch(
            "backend.algo.live.reconciliation.flush_events",
            side_effect=lambda rows: flushed.extend(rows),
        ),
    ):
        from backend.algo.live.reconciliation import reconcile_user

        # Run 1: first detection
        r1 = await reconcile_user(user_id)
        assert r1["events_emitted"] == 1

        # Run 2: drift already open
        drift_repo.get_open_drifts = AsyncMock(
            return_value=[{"symbol": symbol}],
        )
        r2 = await reconcile_user(user_id)
        assert r2["events_emitted"] == 0

        # Run 3: still open
        r3 = await reconcile_user(user_id)
        assert r3["events_emitted"] == 0

    # upsert called 3 times
    assert call_count[0] == 3

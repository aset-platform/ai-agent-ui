"""Tests for reconciliation.py — Tasks 4.1 + 4.3.

Coverage:
  (a) Both fetchers (_fetch_our_positions, _fetch_broker_positions) use
      disposable_pg_session and do NOT call the cached get_session_factory.
  (b) kite.get_positions runs via asyncio.to_thread under asyncio.wait_for;
      a timeout path logs WARNING and returns empty dict (no crash).
  (c) broker_qty > 0 & our_qty == 0 → emits position_drift_untracked
      with severity="high" (in addition to / instead of generic).
  (d) Benign non-zero / non-untracked drift still emits
      position_drift_detected.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_ID = uuid4()


def _make_drift_repo(
    *,
    threshold: int = 0,
    open_drifts: list[dict] | None = None,
) -> MagicMock:
    """Return a MagicMock DriftRepo that returns canned data."""
    repo = MagicMock()
    repo.get_drift_threshold = AsyncMock(return_value=threshold)
    repo.get_open_drifts = AsyncMock(return_value=open_drifts or [])
    repo.upsert_drift = AsyncMock(return_value=1)
    repo.resolve_drift = AsyncMock(return_value=True)
    return repo


# ---------------------------------------------------------------------------
# (a) Both fetchers use disposable_pg_session, NOT get_session_factory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_our_positions_uses_disposable_pg_session():
    """_fetch_our_positions must use disposable_pg_session.
    get_session_factory is not imported by the module (removed), so
    calling disposable_pg_session once is sufficient proof."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[]))
    )

    with patch(
        "backend.algo.live.reconciliation.disposable_pg_session",
    ) as mock_disposable:
        mock_disposable.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        mock_disposable.return_value.__aexit__ = AsyncMock(return_value=False)

        from backend.algo.live.reconciliation import _fetch_our_positions
        result = await _fetch_our_positions(_USER_ID)

    mock_disposable.assert_called_once()
    assert result == {}


@pytest.mark.asyncio
async def test_fetch_broker_positions_uses_disposable_pg_session():
    """_fetch_broker_positions must use disposable_pg_session.
    get_session_factory is not imported by the module (removed), so
    calling disposable_pg_session once is sufficient proof."""
    mock_session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.load = AsyncMock(return_value=None)  # no creds → early exit

    with (
        patch(
            "backend.algo.live.reconciliation.disposable_pg_session",
        ) as mock_disposable,
        patch(
            "backend.algo.live.reconciliation.BrokerCredentialsRepo",
            return_value=mock_repo,
        ),
    ):
        mock_disposable.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        mock_disposable.return_value.__aexit__ = AsyncMock(return_value=False)

        from backend.algo.live.reconciliation import _fetch_broker_positions
        result = await _fetch_broker_positions(_USER_ID)

    mock_disposable.assert_called_once()
    assert result == {}


# ---------------------------------------------------------------------------
# (b) kite.get_positions runs via to_thread under wait_for
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kite_get_positions_wrapped_in_wait_for():
    """get_positions must be called via asyncio.to_thread inside
    asyncio.wait_for — not directly on the event loop."""
    mock_session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.load = AsyncMock(return_value={
        "api_key": "key123",
        "access_token": "tok456",
        "access_token_expired": False,
    })
    mock_kite = MagicMock()
    mock_kite.get_positions.return_value = [
        {"tradingsymbol": "INFY", "quantity": 10},
    ]

    wait_for_called = []

    async def fake_wait_for(coro, timeout):
        """Record that wait_for was called, skip executing the
        coroutine."""
        wait_for_called.append(timeout)
        coro.close()
        return mock_kite.get_positions()

    with (
        patch(
            "backend.algo.live.reconciliation.disposable_pg_session",
        ) as mock_disposable,
        patch(
            "backend.algo.live.reconciliation.BrokerCredentialsRepo",
            return_value=mock_repo,
        ),
        patch(
            "backend.algo.live.reconciliation.KiteClient",
            return_value=mock_kite,
        ),
        patch(
            "backend.algo.live.reconciliation.asyncio.wait_for",
            side_effect=fake_wait_for,
        ),
    ):
        mock_disposable.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        mock_disposable.return_value.__aexit__ = AsyncMock(return_value=False)

        from backend.algo.live.reconciliation import _fetch_broker_positions
        result = await _fetch_broker_positions(_USER_ID)

    assert wait_for_called, "asyncio.wait_for was not called"
    assert result == {"INFY": 10}


@pytest.mark.asyncio
async def test_kite_timeout_logs_warning_returns_empty(caplog):
    """When kite.get_positions times out, log WARNING and return {} —
    do not raise or crash the reconcile tick."""
    mock_session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.load = AsyncMock(return_value={
        "api_key": "key123",
        "access_token": "tok456",
        "access_token_expired": False,
    })
    mock_kite = MagicMock()

    async def fake_wait_for(coro, timeout):
        """Close the coroutine and raise TimeoutError."""
        coro.close()
        raise asyncio.TimeoutError

    with (
        patch(
            "backend.algo.live.reconciliation.disposable_pg_session",
        ) as mock_disposable,
        patch(
            "backend.algo.live.reconciliation.BrokerCredentialsRepo",
            return_value=mock_repo,
        ),
        patch(
            "backend.algo.live.reconciliation.KiteClient",
            return_value=mock_kite,
        ),
        patch(
            "backend.algo.live.reconciliation.asyncio.wait_for",
            side_effect=fake_wait_for,
        ),
        caplog.at_level(
            logging.WARNING,
            logger="backend.algo.live.reconciliation",
        ),
    ):
        mock_disposable.return_value.__aenter__ = AsyncMock(
            return_value=mock_session
        )
        mock_disposable.return_value.__aexit__ = AsyncMock(return_value=False)

        from backend.algo.live.reconciliation import _fetch_broker_positions
        result = await _fetch_broker_positions(_USER_ID)

    assert result == {}, "TimeoutError must yield empty dict, not raise"
    assert any(
        "timeout" in r.message.lower() for r in caplog.records
    ), "Expected a WARNING log mentioning 'timeout'"


# ---------------------------------------------------------------------------
# (c) broker_qty > 0, our_qty == 0 → position_drift_untracked (HIGH)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_untracked_broker_position_emits_high_severity_event():
    """broker_qty > 0 and our_qty == 0 must emit position_drift_untracked
    with severity='high' in the payload."""
    our = {}               # we track nothing
    broker = {"INFY": 50}  # broker holds INFY — untracked by us

    emitted: list[dict] = []

    with (
        patch(
            "backend.algo.live.reconciliation.DriftRepo",
            return_value=_make_drift_repo(threshold=0, open_drifts=[]),
        ),
        patch(
            "backend.algo.live.reconciliation._fetch_our_positions",
            AsyncMock(return_value=our),
        ),
        patch(
            "backend.algo.live.reconciliation._fetch_broker_positions",
            AsyncMock(return_value=broker),
        ),
        patch(
            "backend.algo.live.reconciliation.flush_events",
            side_effect=lambda evts: emitted.extend(evts),
        ),
    ):
        from backend.algo.live import reconciliation as recon
        await recon.reconcile_user(_USER_ID)

    untracked = [
        e for e in emitted
        if e.get("type") == "position_drift_untracked"
    ]
    assert untracked, (
        "Expected position_drift_untracked event; "
        f"got event types: {[e.get('type') for e in emitted]}"
    )
    payload = untracked[0]["payload_json"]
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    assert payload["severity"] == "high"
    assert payload["symbol"] == "INFY"
    assert payload["broker_qty"] == 50
    assert payload["our_qty"] == 0


# ---------------------------------------------------------------------------
# (d) Normal drift (both sides non-zero) → position_drift_detected only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_drift_emits_position_drift_detected_not_untracked():
    """A drift where both our_qty and broker_qty are non-zero must emit
    position_drift_detected — NOT position_drift_untracked."""
    our = {"INFY": 10}
    broker = {"INFY": 20}  # 10-share gap, both sides non-zero

    emitted: list[dict] = []

    with (
        patch(
            "backend.algo.live.reconciliation.DriftRepo",
            return_value=_make_drift_repo(threshold=0, open_drifts=[]),
        ),
        patch(
            "backend.algo.live.reconciliation._fetch_our_positions",
            AsyncMock(return_value=our),
        ),
        patch(
            "backend.algo.live.reconciliation._fetch_broker_positions",
            AsyncMock(return_value=broker),
        ),
        patch(
            "backend.algo.live.reconciliation.flush_events",
            side_effect=lambda evts: emitted.extend(evts),
        ),
    ):
        from backend.algo.live import reconciliation as recon
        await recon.reconcile_user(_USER_ID)

    detected = [
        e for e in emitted
        if e.get("type") == "position_drift_detected"
    ]
    assert detected, (
        "Expected position_drift_detected for a benign qty mismatch; "
        f"got: {[e.get('type') for e in emitted]}"
    )
    import json
    det_payload = detected[0]["payload_json"]
    if isinstance(det_payload, str):
        det_payload = json.loads(det_payload)
    assert det_payload["symbol"] == "INFY"

    untracked = [
        e for e in emitted
        if e.get("type") == "position_drift_untracked"
    ]
    assert not untracked, (
        "Benign drift (both sides non-zero) must NOT emit "
        "position_drift_untracked"
    )

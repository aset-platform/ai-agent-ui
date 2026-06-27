"""Tests for backend.algo._iceberg_retry.retry_iceberg_op.

Item A: Verify retry behaviour and that the commit lock is released
during backoff sleep so competing writers can make progress.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from pyiceberg.exceptions import CommitFailedException

from backend.algo._iceberg_retry import _commit_lock, retry_iceberg_op


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _commit_exc(
    msg: str = "branch main has changed",
) -> CommitFailedException:
    return CommitFailedException(msg)


# ---------------------------------------------------------------------------
# Test 1: operation succeeds on first attempt — no sleep
# ---------------------------------------------------------------------------

def test_success_on_first_attempt():
    calls: list[int] = []

    def op() -> str:
        calls.append(1)
        return "ok"

    with patch("backend.algo._iceberg_retry.time.sleep") as mock_sleep:
        result = retry_iceberg_op("test.table", op)

    assert result == "ok"
    assert len(calls) == 1
    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: two failures then success — retries work, sleep called twice
# ---------------------------------------------------------------------------

def test_two_failures_then_success():
    """Operation fails twice then succeeds; called 3 times total."""
    attempt = [0]

    def op() -> int:
        attempt[0] += 1
        if attempt[0] < 3:
            raise _commit_exc()
        return attempt[0]

    sleep_calls: list[float] = []
    with patch(
        "backend.algo._iceberg_retry.time.sleep",
        side_effect=lambda d: sleep_calls.append(d),
    ):
        result = retry_iceberg_op("test.table", op)

    assert result == 3
    assert attempt[0] == 3
    assert len(sleep_calls) == 2
    # Backoff sequence is 0.5, 1.0, 2.0 — first two used.
    assert sleep_calls[0] == pytest.approx(0.5)
    assert sleep_calls[1] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Test 3: all retries exhausted — CommitFailedException re-raised
# ---------------------------------------------------------------------------

def test_all_retries_exhausted_raises():
    def op() -> None:
        raise _commit_exc("permanent conflict")

    with patch("backend.algo._iceberg_retry.time.sleep"):
        with pytest.raises(CommitFailedException):
            retry_iceberg_op("test.table", op)


# ---------------------------------------------------------------------------
# Test 4: lock is NOT held during sleep (key correctness guarantee)
# ---------------------------------------------------------------------------

def test_lock_released_during_sleep():
    """The commit lock must be released while time.sleep() runs.

    Strategy: monkeypatch time.sleep to attempt a non-blocking
    _commit_lock.acquire() inside the sleep callback. If the lock is
    held during sleep, acquire(blocking=False) returns False and we
    record that. The new implementation releases the lock before
    sleeping, so acquire() must succeed from within sleep.
    """
    lock_held_during_sleep: list[bool] = []

    attempt = [0]

    def op() -> str:
        attempt[0] += 1
        if attempt[0] == 1:
            raise _commit_exc()
        return "done"

    def fake_sleep(delay: float) -> None:  # noqa: ARG001
        # Try to acquire the lock from within the sleep call.
        # If the lock has been released before sleep(), acquire succeeds.
        acquired = _commit_lock.acquire(blocking=False)
        lock_held_during_sleep.append(not acquired)
        if acquired:
            _commit_lock.release()

    with patch(
        "backend.algo._iceberg_retry.time.sleep",
        side_effect=fake_sleep,
    ):
        result = retry_iceberg_op("test.table", op)

    assert result == "done"
    # The lock should NOT have been held during sleep.
    assert lock_held_during_sleep == [False], (
        "Lock was still held during backoff sleep: %s"
        % lock_held_during_sleep
    )

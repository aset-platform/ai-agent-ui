"""Tests for dashboard.callbacks.refresh_state.RefreshManager."""

import time
from concurrent.futures import Future

import pytest

from dashboard.callbacks.refresh_state import RefreshManager


class TestRefreshManager:
    """Unit tests for the thread-safe RefreshManager."""

    @pytest.fixture()
    def mgr(self):
        """Return a fresh RefreshManager."""
        return RefreshManager(max_workers=2)

    def test_submit_if_idle_returns_true(self, mgr):
        """First submit for a ticker succeeds."""
        assert mgr.submit_if_idle("AAPL", time.sleep, 0)

    def test_submit_if_idle_rejects_duplicate(self, mgr):
        """Second submit while first is in-flight is rejected."""
        mgr.submit_if_idle("AAPL", time.sleep, 10)
        assert mgr.submit_if_idle("AAPL", time.sleep, 0) is False

    def test_get_returns_future(self, mgr):
        """get() returns a Future after submit."""
        mgr.submit_if_idle("AAPL", time.sleep, 0)
        fut = mgr.get("AAPL")
        assert isinstance(fut, Future)

    def test_get_returns_none_for_unknown(self, mgr):
        """get() returns None for unknown ticker."""
        assert mgr.get("AAPL") is None

    def test_harvest_done_returns_completed(self, mgr):
        """harvest_done() returns and removes done futures."""
        mgr.submit_if_idle("AAPL", lambda: "ok")
        # Wait for completion.
        mgr.get("AAPL").result(timeout=5)

        done = mgr.harvest_done()
        assert len(done) == 1
        assert done[0][0] == "AAPL"
        assert done[0][1].result() == "ok"

        # Harvested — no longer tracked.
        assert mgr.get("AAPL") is None

    def test_harvest_done_skips_running(self, mgr):
        """harvest_done() does not return in-flight futures."""
        mgr.submit_if_idle("SLOW", time.sleep, 10)
        assert mgr.harvest_done() == []

    def test_pop_removes_future(self, mgr):
        """pop() removes and returns the future."""
        mgr.submit_if_idle("AAPL", lambda: "ok")
        fut = mgr.pop("AAPL")
        assert isinstance(fut, Future)
        assert mgr.get("AAPL") is None

    def test_pop_returns_none_for_unknown(self, mgr):
        """pop() returns None for unknown ticker."""
        assert mgr.pop("AAPL") is None

    def test_submit_after_done_replaces(self, mgr):
        """Can re-submit after previous future completed."""
        mgr.submit_if_idle("AAPL", lambda: 1)
        mgr.get("AAPL").result(timeout=5)
        assert mgr.submit_if_idle("AAPL", lambda: 2)

"""Tests for RoundRobinPool and get_token_budget singleton."""

import threading
import time

from token_budget import (
    ModelLimits,
    RoundRobinPool,
    TokenBudget,
    get_token_budget,
)


class TestRoundRobinPool:
    """Verify round-robin rotation and thread safety."""

    def test_rotates_correctly(self):
        """Three calls cycle through all start positions."""
        pool = RoundRobinPool("test", ["A", "B", "C"])

        r1 = pool.ordered_models()
        r2 = pool.ordered_models()
        r3 = pool.ordered_models()

        assert r1 == ["A", "B", "C"]
        assert r2 == ["B", "C", "A"]
        assert r3 == ["C", "A", "B"]

    def test_wraps_around(self):
        """Fourth call wraps back to first position."""
        pool = RoundRobinPool("wrap", ["X", "Y"])

        pool.ordered_models()  # X, Y
        pool.ordered_models()  # Y, X
        r3 = pool.ordered_models()  # X, Y again

        assert r3 == ["X", "Y"]

    def test_single_model(self):
        """Single-model pool always returns same."""
        pool = RoundRobinPool("one", ["Z"])

        assert pool.ordered_models() == ["Z"]
        assert pool.ordered_models() == ["Z"]

    def test_empty_pool(self):
        """Empty pool returns empty list."""
        pool = RoundRobinPool("empty", [])
        assert pool.ordered_models() == []

    def test_thread_safety(self):
        """50 concurrent calls produce valid rotations."""
        pool = RoundRobinPool(
            "safe", ["A", "B", "C"],
        )
        results: list[list[str]] = []
        lock = threading.Lock()

        def call():
            r = pool.ordered_models()
            with lock:
                results.append(r)

        threads = [
            threading.Thread(target=call)
            for _ in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 50
        # Every result must be a valid rotation
        valid = {
            ("A", "B", "C"),
            ("B", "C", "A"),
            ("C", "A", "B"),
        }
        for r in results:
            assert tuple(r) in valid


class TestTokenBudgetSingleton:
    """Verify get_token_budget returns shared instance."""

    def test_returns_same_instance(self):
        """Two calls return identical object."""
        b1 = get_token_budget()
        b2 = get_token_budget()
        assert b1 is b2

    def test_is_token_budget(self):
        """Singleton is a TokenBudget instance."""
        assert isinstance(get_token_budget(), TokenBudget)


class TestPoolRegistration:
    """Verify pool registration on TokenBudget."""

    def test_register_and_get(self):
        """register_pool creates retrievable pool."""
        tb = TokenBudget()
        pool = tb.register_pool(
            "test:0", ["A", "B"],
        )
        assert pool.models == ["A", "B"]
        assert tb.get_pool("test:0") is pool

    def test_idempotent(self):
        """Re-registering same name returns same pool."""
        tb = TokenBudget()
        p1 = tb.register_pool("x", ["A"])
        p2 = tb.register_pool("x", ["B"])
        assert p1 is p2
        assert p1.models == ["A"]  # original

    def test_get_missing(self):
        """get_pool returns None for unknown name."""
        tb = TokenBudget()
        assert tb.get_pool("nope") is None


class TestReserveRelease:
    """Verify reserve/release keeps counters non-negative."""

    def _make_budget(self) -> TokenBudget:
        return TokenBudget(
            limits={
                "test-model": ModelLimits(
                    rpm=30, tpm=8000,
                    rpd=1000, tpd=200000,
                ),
            },
        )

    def test_release_zeroes_usage(self):
        """reserve + release → usage back to 0."""
        tb = self._make_budget()
        assert tb.reserve("test-model", 1000)
        tb.release("test-model", 1000)

        status = tb.get_status()["test-model"]
        # TPM/RPM should be 0 after release
        assert status["tpm"] == "0/8000"
        assert status["rpm"] == "0/30"

    def test_release_no_negative_after_expiry(self):
        """After deque entries expire, totals stay >= 0.

        Regression test: old release() only decremented
        running totals without compensating deque entries,
        causing permanent negative values after expiry.
        """
        tb = self._make_budget()
        assert tb.reserve("test-model", 1500)
        tb.release("test-model", 1500)

        # Simulate minute-window expiry by advancing
        # all deque timestamps into the past.
        state = tb._get_state("test-model")
        past = time.monotonic() - 120  # 2 min ago
        with state.lock:
            for dq in (
                state.minute_tokens,
                state.minute_requests,
            ):
                for i in range(len(dq)):
                    _, val = dq[i]
                    dq[i] = (past, val)

        status = tb.get_status()["test-model"]
        # Must NOT be negative
        assert status["tpm"] == "0/8000"
        assert status["rpm"] == "0/30"

    def test_daily_budget_non_negative(self):
        """get_daily_budget clamps negative to 0."""
        tb = self._make_budget()
        assert tb.reserve("test-model", 500)
        tb.release("test-model", 500)

        daily = tb.get_daily_budget()
        model_info = daily["by_model"]["test-model"]
        assert model_info["total"] >= 0
        assert model_info["requests"] >= 0

"""Tests for RoundRobinPool and get_token_budget singleton."""

import threading

from token_budget import (
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

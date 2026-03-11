"""Tests for Story 3.2 — TokenBudget running-total optimization."""

import time

from token_budget import TokenBudget, _ModelState


def test_running_totals_match_deque_sum():
    """Running total should match manual deque sum."""
    budget = TokenBudget()
    model = "llama-3.3-70b-versatile"
    for i in range(10):
        budget.record(model, 100 + i)

    state = budget._get_state(model)
    deque_sum = sum(c for _, c in state.minute_tokens)
    assert state.minute_tokens_total == deque_sum
    assert state.day_tokens_total == deque_sum


def test_pre_allocated_model_state():
    """All known models should have pre-allocated state."""
    budget = TokenBudget()
    for model in budget._limits:
        assert model in budget._state
        assert isinstance(budget._state[model], _ModelState)


def test_can_afford_with_running_totals():
    """can_afford should work correctly with running totals."""
    budget = TokenBudget()
    model = "llama-3.3-70b-versatile"
    assert budget.can_afford(model, 100)

    # Record enough to approach the TPM limit.
    for _ in range(90):
        budget.record(model, 100)

    # Should be near limit now.
    assert not budget.can_afford(model, 5000)


def test_record_updates_all_totals():
    """record() should update all four running totals."""
    budget = TokenBudget()
    model = "llama-3.3-70b-versatile"
    budget.record(model, 500)

    state = budget._get_state(model)
    assert state.minute_tokens_total == 500
    assert state.minute_requests_total == 1
    assert state.day_tokens_total == 500
    assert state.day_requests_total == 1


def test_window_total_prunes_and_updates():
    """_window_total should prune expired entries."""
    from collections import deque

    now = time.monotonic()
    log = deque()
    # Add an expired entry.
    log.append((now - 120, 100))
    # Add a current entry.
    log.append((now - 5, 200))

    total, expired = TokenBudget._window_total(
        log,
        60,
        now,
        300,
    )
    assert total == 200
    assert expired == 100
    assert len(log) == 1

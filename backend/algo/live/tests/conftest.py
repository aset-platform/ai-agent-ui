"""Shared fixtures for the live-runtime test package.

Autouse: short-circuit ``budget_reserve`` / ``budget_transition``
inside ``backend.algo.live.runtime`` so tests that exercise
``LiveRuntime._submit_order`` do not write to the real
``algo.budget_reservations`` table (which causes event-loop
crosstalk when the cached session factory is bound to a prior
test's loop).

Tests that target the budget subsystem directly mock at
``backend.algo.live.budget_reconciliation.*`` /
``backend.algo.live.budget.*``; those names are NOT affected here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def _mock_runtime_budget_calls(request):
    """Patch the names imported into ``runtime.py`` only.

    Skipped for tests in ``test_budget_reconciliation.py`` and
    ``test_budget*.py`` modules, which already do their own
    targeted mocking.
    """
    modname = request.node.module.__name__
    if "test_budget" in modname:
        yield
        return

    from unittest.mock import patch

    with (
        patch(
            "backend.algo.live.runtime.budget_reserve",
            new=AsyncMock(return_value=uuid4()),
        ),
        patch(
            "backend.algo.live.runtime.budget_transition",
            new=AsyncMock(),
        ),
    ):
        yield

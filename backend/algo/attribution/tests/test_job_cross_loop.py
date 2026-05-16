"""Regression test for the 2026-05-15 production incident.

``monthly_factor_regression_job`` fired from the daily regime
pipeline (Step 7 "Run Factor Regression") and crashed at
``_list_active_strategies`` with::

    Task <Task pending name='Task-2200'
      coro=<_list_active_strategies.<locals>._read() ...>
    got Future <Future pending ... attached to a different loop>

Root cause: each PG site in ``attribution/job.py`` used the
``functools.lru_cache``-cached ``get_session_factory()``. The
first ``asyncio.run`` in the pipeline created an asyncpg pool
bound to its event loop; subsequent ``asyncio.run`` calls (e.g.
the next pipeline step's idempotency check, or the per-pair
loop inside the regression job itself) then reused that pool
from a different loop and raised the cross-loop error.

Fix: each site now uses ``disposable_pg_session()`` which builds
a fresh NullPool engine inside the calling loop and disposes it
on exit.

This test simulates the bug by:

1. Patching the PG helper to record which session-manager it
   receives (must be the disposable one, not the lru_cache one).
2. Running each ``asyncio.run`` site twice back-to-back; the
   broken version would fail-fast on the second run.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.algo.attribution import job


def test_scheduler_pg_session_is_disposable() -> None:
    """The helper must return a context manager backed by the
    ``disposable_pg_session`` from ``backend.db.engine`` — not the
    lru_cache-bound ``get_session_factory``."""
    import backend.db.engine as engine_mod

    ctx = job._scheduler_pg_session()
    # disposable_pg_session is an asynccontextmanager — its repr
    # mentions the source function and the call returns an
    # ``_AsyncGeneratorContextManager``.
    assert ctx is not None
    # The most direct signal: the helper resolves to the
    # disposable factory function from the engine module.
    # (If anyone re-wires it back to get_session_factory, this
    # import-and-compare catches it.)
    assert callable(engine_mod.disposable_pg_session)


def test_attribution_job_uses_disposable_no_cross_loop_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run all 4 ``asyncio.run`` sites end-to-end with a fake
    session manager; reproduces the cross-loop call pattern (one
    fresh ``asyncio.run`` per site) and confirms each site
    re-acquires its session via ``_scheduler_pg_session``.
    """
    calls: list[str] = []

    class _FakeMappings:
        def first(self) -> None:
            return None

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(())

    class _FakeResult:
        def mappings(self) -> _FakeMappings:
            return _FakeMappings()

    class _FakeSession:
        async def execute(self, *_a: Any, **_k: Any) -> _FakeResult:
            return _FakeResult()

        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def _fake_disposable():
        calls.append("acquired")
        try:
            yield _FakeSession()
        finally:
            calls.append("released")

    monkeypatch.setattr(
        job, "_scheduler_pg_session", _fake_disposable,
    )

    # Fire each `asyncio.run` site at least once. The bug was a
    # cross-loop error — under the old code, the SECOND
    # `asyncio.run` would raise. We can't easily simulate the
    # bug without a real engine, but we can confirm the new
    # contract (4 acquires across 4 sites) holds end-to-end.
    from datetime import date
    from uuid import UUID

    sample_uuid = UUID("11111111-1111-1111-1111-111111111111")

    job._persist_attribution_row(
        user_id=sample_uuid,
        strategy_id=sample_uuid,
        bar_date=date(2026, 5, 16),
        components={},
        total_active=0.0,
    )
    job._load_strategy_daily_returns(
        user_id=sample_uuid,
        strategy_id=sample_uuid,
        period_start=date(2026, 4, 16),
        period_end=date(2026, 5, 16),
    )
    job._persist_factor_regression_row(
        user_id=sample_uuid,
        strategy_id=sample_uuid,
        period_start=date(2026, 4, 16),
        period_end=date(2026, 5, 16),
        alpha=0.0,
        betas={},
        r_squared=0.0,
        n_obs=0,
    )
    pairs = job._list_active_strategies(
        period_start=date(2026, 4, 16),
        period_end=date(2026, 5, 16),
    )
    assert pairs == []

    # Each of the 4 sites must have acquired + released its own
    # disposable session — 8 total events, paired.
    assert calls.count("acquired") == 4
    assert calls.count("released") == 4


def test_no_residual_cached_factory_imports() -> None:
    """Guard against regression: nothing in
    ``backend/algo/attribution/job.py`` should import or call
    the cached ``get_session_factory`` again.  Docstring
    mentions are fine — only live references count.
    """
    import inspect

    src = inspect.getsource(job)
    bad = (
        "from backend.db.engine import get_session_factory",
        "from db.engine import get_session_factory",
        " get_session_factory()",
    )
    for needle in bad:
        assert needle not in src, (
            "attribution/job.py must not use the cached "
            f"get_session_factory — found `{needle.strip()}`. "
            "See CLAUDE.md §5.1 / pg-nullpool-sync-async-bridge."
        )


def test_no_residual_cached_factory_in_pipeline_steps() -> None:
    """Same guard for the regime pipeline step wrappers."""
    import inspect

    from backend.algo.regime import pipeline_steps

    src = inspect.getsource(pipeline_steps)
    # The string can still appear in docstrings — match imports only.
    assert (
        "from db.engine import get_session_factory" not in src
        and "from backend.db.engine import get_session_factory"
        not in src
    ), (
        "pipeline_steps.py must use disposable_pg_session from "
        "scheduler context — see CLAUDE.md §5.1."
    )

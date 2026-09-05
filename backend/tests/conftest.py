"""Shared fixtures for backend/tests (async PG-repo tests).

``pg_session`` is a real-Postgres async session, not a SQLite
stand-in: it targets the live ``portfolio_closed_positions``
table created by the Task 1 migration.

Why not SAVEPOINT-rollback teardown? Same finding as
``backend/algo/live/tests/test_budget_repo.py`` /
``auth/repo/tests/test_ticker_repo_bulk.py`` — under
``AsyncSession`` driving asyncpg through
``disposable_pg_session()``, ``await session.commit()`` (called
inside the repo functions under test) commits the *outermost*
transaction, so a later ``session.rollback()`` in fixture
teardown is a no-op and test rows leak into the real table.

Instead of per-test bookkeeping, this fixture wraps
``session.add`` to record the ``id`` of every
``PortfolioClosedPosition`` a test inserts, then deletes exactly
those rows at teardown — no cooperation required from the test
body.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.db.models.portfolio_close import PortfolioClosedPosition
from db.engine import disposable_pg_session


@pytest.fixture
async def pg_session():
    """Real-Postgres async session with auto-tracked teardown."""
    written_ids: list[str] = []

    async with disposable_pg_session() as session:
        _orig_add = session.add

        def _tracked_add(obj, *a, **kw):
            if isinstance(obj, PortfolioClosedPosition):
                written_ids.append(obj.id)
            return _orig_add(obj, *a, **kw)

        session.add = _tracked_add
        try:
            yield session
        finally:
            if written_ids:
                await session.execute(
                    text(
                        "DELETE FROM portfolio_closed_positions "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": written_ids},
                )
                await session.commit()

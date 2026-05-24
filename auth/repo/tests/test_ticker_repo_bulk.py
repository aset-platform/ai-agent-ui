"""Tests for ticker repo bulk helpers.

Uses ``disposable_pg_session()`` (NullPool) plus an explicit
teardown that DELETEs the ``auth.user_tickers`` + ``auth.users``
rows the test wrote — same pattern as
``backend/algo/live/tests/test_budget_repo.py`` (savepoint
rollback leaks under AsyncSession + asyncpg). The
``db_session`` fixture referenced in the plan does not exist
in this project's auth tests package.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from auth.repo import ticker_repo
from db.engine import disposable_pg_session


@pytest.fixture
async def db_session():
    """NullPool async session + tracked-teardown.

    Tests append every ``user_id`` they create to
    ``tracked_user_ids``; teardown cascades the delete to
    ``auth.user_tickers`` via the FK ``ON DELETE CASCADE``.
    """
    tracked_user_ids: list[str] = []
    async with disposable_pg_session() as s:
        s.tracked_user_ids = tracked_user_ids  # type: ignore[attr-defined]
        try:
            yield s
        finally:
            for uid in tracked_user_ids:
                # user_tickers has ON DELETE CASCADE on user_id.
                await s.execute(
                    text(
                        "DELETE FROM users WHERE user_id = :u"
                    ),
                    {"u": uid},
                )
            await s.commit()


async def _add_user(session, uid: str, label: str) -> None:
    """Insert a minimal ``auth.users`` row for FK satisfaction."""
    from backend.db.models.user import User

    session.add(
        User(
            user_id=uid,
            email=f"u-{uid}@example.com",
            hashed_password="x",
            full_name=label,
        ),
    )
    await session.commit()
    session.tracked_user_ids.append(uid)


@pytest.mark.asyncio
async def test_bulk_link_tickers_inserts_new(db_session):
    uid = str(uuid4())
    await _add_user(db_session, uid, "Test")

    added, already_linked = await ticker_repo.bulk_link_tickers(
        db_session,
        user_id=uid,
        tickers=["AAPL", "MSFT"],
        source="bulk_csv",
    )
    assert sorted(added) == ["AAPL", "MSFT"]
    assert already_linked == []


@pytest.mark.asyncio
async def test_bulk_link_tickers_splits_added_vs_already_linked(
    db_session,
):
    uid = str(uuid4())
    await _add_user(db_session, uid, "Test")

    # Pre-link MSFT.
    await ticker_repo.link_ticker(
        db_session, uid, "MSFT", source="manual",
    )

    added, already_linked = await ticker_repo.bulk_link_tickers(
        db_session,
        user_id=uid,
        tickers=["AAPL", "MSFT", "GOOG"],
        source="bulk_csv",
    )
    assert sorted(added) == ["AAPL", "GOOG"]
    assert already_linked == ["MSFT"]


@pytest.mark.asyncio
async def test_unlink_all_tickers_returns_row_count(
    db_session,
):
    uid = str(uuid4())
    other = str(uuid4())
    await _add_user(db_session, uid, "A")
    await _add_user(db_session, other, "B")

    for t in ["AAPL", "MSFT", "GOOG", "TSLA"]:
        await ticker_repo.link_ticker(db_session, uid, t)
    await ticker_repo.link_ticker(db_session, other, "NVDA")
    await ticker_repo.link_ticker(db_session, other, "AMZN")

    removed = await ticker_repo.unlink_all_tickers(
        db_session, uid,
    )
    assert removed == 4

    # Other user's rows untouched.
    others = await ticker_repo.get_user_tickers(
        db_session, other,
    )
    assert sorted(others) == ["AMZN", "NVDA"]

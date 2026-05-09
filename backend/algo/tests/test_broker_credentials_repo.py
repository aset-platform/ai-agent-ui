"""Round-trip + per-user-isolation tests for broker_credentials_repo.

Uses the in-memory ``_StubSession`` pattern from the Slice 4
strategies-route tests. Real DB writes covered indirectly by
the Alembic migration smoke (Session 1 Task 1) + the route
smokes in Task 4 of this session.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

# Ensure Fernet is set up for the test process. The host CI runs
# may not export BYO_SECRET_KEY — set a deterministic dev key.
os.environ.setdefault(
    "BYO_SECRET_KEY",
    # Static test key — NEVER use in prod.
    "Q3RZ8h3tQq2c5rVH0hWv0cHXh2OtdJv6f4M6Y9pQ8mE=",
)

from backend.algo.broker.credentials_repo import (  # noqa: E402
    BrokerCredentialsRepo,
)


class _StubSession:
    """In-memory async-session stub mirroring the strategy-repo style."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.last_sql = ""
        self.last_params: dict | None = None

    async def execute(self, q, params=None):
        self.last_sql = str(q)
        self.last_params = params or {}

        class _Res:
            def __init__(self, items):
                self._items = items

            def mappings(self):
                return self

            def first(self):
                return self._items[0] if self._items else None

            @property
            def rowcount(self):
                return len(self._items)

        if "INSERT INTO algo.broker_credentials" in self.last_sql:
            existing = [
                r for r in self.rows
                if str(r["user_id"]) == str(params["user_id"])
            ]
            if existing:
                # ON CONFLICT update path
                existing[0].update(params)
                return _Res(existing)
            self.rows.append(dict(params))
            return _Res([self.rows[-1]])

        if "SELECT" in self.last_sql:
            hit = [
                r for r in self.rows
                if str(r["user_id"]) == str(params["user_id"])
            ]
            return _Res(hit)

        if "UPDATE algo.broker_credentials" in self.last_sql:
            hit = [
                r for r in self.rows
                if str(r["user_id"]) == str(params["user_id"])
            ]
            for h in hit:
                if "access_token_fernet" in params:
                    h["access_token_fernet"] = (
                        params["access_token_fernet"]
                    )
                if "access_token_expires_at" in params:
                    h["access_token_expires_at"] = (
                        params["access_token_expires_at"]
                    )
                if "kite_user_id" in params:
                    h["kite_user_id"] = params["kite_user_id"]
                if "last_login_at" in params:
                    h["last_login_at"] = params["last_login_at"]
            return _Res(hit)

        if "DELETE FROM algo.broker_credentials" in self.last_sql:
            before = len(self.rows)
            self.rows = [
                r for r in self.rows
                if str(r["user_id"]) != str(params["user_id"])
            ]
            return _Res([None] * (before - len(self.rows)))

        return _Res([])

    async def commit(self):
        return None


@pytest.fixture
def repo() -> BrokerCredentialsRepo:
    return BrokerCredentialsRepo()


@pytest.mark.asyncio
async def test_save_and_load_api_key_round_trip(repo):
    sess = _StubSession()
    user_id = uuid4()
    await repo.save_api_key(sess, user_id, "test_kite_api_key_12345")
    loaded = await repo.load_api_key(sess, user_id)
    assert loaded == "test_kite_api_key_12345"


@pytest.mark.asyncio
async def test_save_and_load_access_token_round_trip(repo):
    sess = _StubSession()
    user_id = uuid4()
    await repo.save_api_key(sess, user_id, "api_key_xyz")
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    await repo.save_access_token(
        sess, user_id, "access_token_abc", expires, "AB1234",
    )
    state = await repo.load(sess, user_id)
    assert state is not None
    assert state["api_key"] == "api_key_xyz"
    assert state["access_token"] == "access_token_abc"
    assert state["kite_user_id"] == "AB1234"


@pytest.mark.asyncio
async def test_load_returns_none_for_unknown_user(repo):
    sess = _StubSession()
    state = await repo.load(sess, uuid4())
    assert state is None


@pytest.mark.asyncio
async def test_per_user_isolation(repo):
    sess = _StubSession()
    u1, u2 = uuid4(), uuid4()
    await repo.save_api_key(sess, u1, "u1_key")
    await repo.save_api_key(sess, u2, "u2_key")
    assert (await repo.load_api_key(sess, u1)) == "u1_key"
    assert (await repo.load_api_key(sess, u2)) == "u2_key"


@pytest.mark.asyncio
async def test_delete_removes_row(repo):
    sess = _StubSession()
    user_id = uuid4()
    await repo.save_api_key(sess, user_id, "k")
    await repo.delete(sess, user_id)
    assert (await repo.load(sess, user_id)) is None


@pytest.mark.asyncio
async def test_token_expiry_predicate(repo):
    sess = _StubSession()
    user_id = uuid4()
    await repo.save_api_key(sess, user_id, "k")
    # Past expiry
    expired = datetime.now(timezone.utc) - timedelta(minutes=5)
    await repo.save_access_token(
        sess, user_id, "tok", expired, "AB1234",
    )
    state = await repo.load(sess, user_id)
    assert state["access_token_expired"] is True

    # Fresh expiry
    fresh = datetime.now(timezone.utc) + timedelta(hours=2)
    await repo.save_access_token(
        sess, user_id, "tok2", fresh, "AB1234",
    )
    state = await repo.load(sess, user_id)
    assert state["access_token_expired"] is False

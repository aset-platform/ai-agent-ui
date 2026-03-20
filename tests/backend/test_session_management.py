"""Tests for session management (ASETPLTFRM-10).

Covers AuthService session methods and the
``/auth/sessions`` API endpoints.
"""

import pytest

from auth.service import AuthService
from auth.token_store import InMemoryTokenStore

# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture()
def store():
    """Return a fresh InMemoryTokenStore."""
    return InMemoryTokenStore()


@pytest.fixture()
def service(store):
    """Return an AuthService with in-memory store."""
    return AuthService(
        secret_key="a" * 32,
        access_expire_minutes=60,
        refresh_expire_days=7,
        token_store=store,
    )


@pytest.fixture()
def user_id():
    """Return a test user UUID."""
    return "test-user-uuid-1234"


@pytest.fixture()
def refresh_token(service, user_id):
    """Create a refresh token for the test user."""
    return service.create_refresh_token(user_id)


# ── AuthService session unit tests ───────────────────────


class TestListActiveSessions:
    """GET /auth/sessions returns list."""

    def test_returns_sessions(self, service, user_id, refresh_token):
        """Registered session appears in list."""
        service.register_session(
            user_id=user_id,
            refresh_token=refresh_token,
            ip_address="127.0.0.1",
            user_agent="TestBrowser/1.0",
        )
        sessions = service.list_sessions(user_id)
        assert len(sessions) == 1
        s = sessions[0]
        assert s["user_id"] == user_id
        assert s["ip_address"] == "127.0.0.1"
        assert s["user_agent"] == "TestBrowser/1.0"
        assert "created_at" in s
        assert "session_id" in s


class TestRevokeSession:
    """DELETE /auth/sessions/:id revokes token."""

    def test_revoke_removes_session(self, service, user_id, refresh_token):
        """Revoking a session removes it from the list."""
        service.register_session(
            user_id=user_id,
            refresh_token=refresh_token,
            ip_address="10.0.0.1",
            user_agent="Chrome",
        )
        sessions = service.list_sessions(user_id)
        session_id = sessions[0]["session_id"]

        result = service.revoke_session(user_id, session_id)
        assert result is True

        remaining = service.list_sessions(user_id)
        assert len(remaining) == 0

    def test_revoke_nonexistent_returns_false(self, service, user_id):
        """Revoking unknown session returns False."""
        result = service.revoke_session(user_id, "nonexistent-jti")
        assert result is False


class TestRevokeAllSessions:
    """POST /auth/sessions/revoke-all clears all."""

    def test_revoke_all_clears(self, service, user_id):
        """Revoking all sessions clears the list."""
        # Create 3 sessions.
        for _ in range(3):
            token = service.create_refresh_token(user_id)
            service.register_session(
                user_id=user_id,
                refresh_token=token,
                ip_address="10.0.0.1",
                user_agent="Chrome",
            )
        assert len(service.list_sessions(user_id)) == 3

        count = service.revoke_all_sessions(user_id)
        assert count == 3
        assert len(service.list_sessions(user_id)) == 0


class TestSessionTracksDeviceInfo:
    """Session stores user-agent and IP."""

    def test_device_info_stored(self, service, user_id, refresh_token):
        """Session metadata includes device info."""
        service.register_session(
            user_id=user_id,
            refresh_token=refresh_token,
            ip_address="192.168.1.100",
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X)"),
        )
        sessions = service.list_sessions(user_id)
        s = sessions[0]
        assert s["ip_address"] == "192.168.1.100"
        assert "Macintosh" in s["user_agent"]


# ── Token store add_json/get_json/keys tests ─────────────


class TestInMemoryTokenStoreJSON:
    """InMemoryTokenStore JSON value support."""

    def test_add_and_get_json(self, store):
        """add_json stores value retrievable by get_json."""
        store.add_json("k1", '{"a": 1}', 3600)
        assert store.get_json("k1") == '{"a": 1}'

    def test_get_json_expired_returns_none(self, store):
        """Expired key returns None."""
        store.add_json("k2", '"val"', 1)
        # Backdate expiry.
        store._store["k2"] = 0
        assert store.get_json("k2") is None

    def test_keys_by_prefix(self, store):
        """keys_by_prefix returns matching keys."""
        store.add_json("session:u1:a", "1", 3600)
        store.add_json("session:u1:b", "2", 3600)
        store.add_json("session:u2:c", "3", 3600)
        keys = store.keys_by_prefix("session:u1:")
        assert sorted(keys) == [
            "session:u1:a",
            "session:u1:b",
        ]

    def test_remove_clears_value(self, store):
        """remove() also clears the JSON value."""
        store.add_json("k3", '"data"', 3600)
        store.remove("k3")
        assert store.get_json("k3") is None


# ── API endpoint integration tests ───────────────────────


class TestSessionAPI:
    """Integration tests via TestClient."""

    @pytest.fixture()
    def client(self, monkeypatch):
        """Create a test client with auth routes."""
        monkeypatch.setenv(
            "JWT_SECRET_KEY",
            "a" * 32,
        )

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from auth.endpoints import create_auth_router

        app = FastAPI()
        app.include_router(create_auth_router())
        return TestClient(app)

    def test_list_sessions_requires_auth(self, client):
        """GET /auth/sessions without token → 401."""
        r = client.get("/auth/sessions")
        assert r.status_code == 401

    def test_revoke_session_requires_auth(self, client):
        """DELETE /auth/sessions/x without token → 401."""
        r = client.delete("/auth/sessions/fake-id")
        assert r.status_code == 401

    def test_revoke_all_requires_auth(self, client):
        """POST /auth/sessions/revoke-all w/o token → 401."""
        r = client.post("/auth/sessions/revoke-all")
        assert r.status_code == 401

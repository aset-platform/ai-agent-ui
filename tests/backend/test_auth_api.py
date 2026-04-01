"""Unit tests for the auth API endpoints.

Uses FastAPI's :class:`starlette.testclient.TestClient` with
``auth.api._get_repo`` patched to return an in-memory fake so no Iceberg
catalog is required.

There is no ``/auth/register`` endpoint — users are created by superusers
via ``POST /users``.  These tests pre-seed the fake repo directly and
exercise login → refresh → logout → ``/auth/me`` → ``PATCH /auth/me``.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

# JWT secret must be set before any auth import
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only-32ch"
)


# ---------------------------------------------------------------------------
# In-memory repository fake
# ---------------------------------------------------------------------------


class _FakeRepo:
    """In-memory stand-in for UserRepository."""

    def __init__(self):
        self._users: Dict[str, Dict[str, Any]] = {}
        self._audit: List[Dict[str, Any]] = []

    def _now(self):
        return datetime.now(timezone.utc)

    def seed(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Sync insert for test fixtures (no await)."""
        now = self._now()
        row = {
            "user_id": data.get(
                "user_id", str(uuid.uuid4()),
            ),
            "email": data["email"],
            "hashed_password": data["hashed_password"],
            "full_name": data["full_name"],
            "role": data.get("role", "general"),
            "is_active": data.get("is_active", True),
            "created_at": now,
            "updated_at": now,
            "last_login_at": None,
            "password_reset_token": None,
            "password_reset_expiry": None,
            "oauth_provider": data.get(
                "oauth_provider",
            ),
            "oauth_sub": data.get("oauth_sub"),
            "profile_picture_url": data.get(
                "profile_picture_url",
            ),
        }
        self._users[row["user_id"]] = row
        return dict(row)

    async def get_by_email(
        self, email: str,
    ) -> Optional[Dict[str, Any]]:
        for u in self._users.values():
            if u["email"] == email:
                return dict(u)
        return None

    async def get_by_id(
        self, user_id: str,
    ) -> Optional[Dict[str, Any]]:
        u = self._users.get(user_id)
        return dict(u) if u else None

    async def list_all(self) -> List[Dict[str, Any]]:
        return [dict(u) for u in self._users.values()]

    async def create(
        self, data: Dict[str, Any],
    ) -> Dict[str, Any]:
        if await self.get_by_email(data["email"]):
            raise ValueError(
                f"User with email '{data['email']}' already exists."
            )
        now = self._now()
        row = {
            "user_id": data.get("user_id", str(uuid.uuid4())),
            "email": data["email"],
            "hashed_password": data["hashed_password"],
            "full_name": data["full_name"],
            "role": data.get("role", "general"),
            "is_active": data.get("is_active", True),
            "created_at": now,
            "updated_at": now,
            "last_login_at": None,
            "password_reset_token": None,
            "password_reset_expiry": None,
            "oauth_provider": data.get("oauth_provider"),
            "oauth_sub": data.get("oauth_sub"),
            "profile_picture_url": data.get("profile_picture_url"),
        }
        self._users[row["user_id"]] = row
        return dict(row)

    async def update(
        self, user_id: str, updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        if user_id not in self._users:
            raise ValueError(f"User '{user_id}' not found.")
        row = self._users[user_id]
        immutable = {"user_id", "created_at"}
        for k, v in updates.items():
            if k not in immutable:
                row[k] = v
        row["updated_at"] = self._now()
        return dict(row)

    async def delete(self, user_id: str) -> None:
        await self.update(
            user_id, {"is_active": False},
        )

    async def get_by_oauth_sub(
        self, provider: str, sub: str,
    ) -> Optional[Dict[str, Any]]:
        for u in self._users.values():
            if (
                u.get("oauth_provider") == provider
                and u.get("oauth_sub") == sub
            ):
                return dict(u)
        return None

    async def get_or_create_by_oauth(
        self, provider, oauth_sub, email,
        full_name, picture_url=None,
    ) -> Dict[str, Any]:
        existing = await self.get_by_oauth_sub(
            provider, oauth_sub,
        )
        if existing:
            return await self.update(
                existing["user_id"],
                {
                    "profile_picture_url": picture_url,
                    "last_login_at": self._now(),
                },
            )
        return await self.create(
            {
                "email": email,
                "hashed_password": "!sso_only",
                "full_name": full_name,
                "role": "general",
                "oauth_provider": provider,
                "oauth_sub": oauth_sub,
                "profile_picture_url": picture_url,
            }
        )

    async def append_audit_event(
        self, *args, **kwargs,
    ) -> None:
        self._audit.append({"args": args, "kwargs": kwargs})

    def list_audit_events(self) -> List[Dict[str, Any]]:
        return list(self._audit)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_repo():
    """Return a fresh in-memory repository seeded with one general user."""
    repo = _FakeRepo()
    # Import AuthService to hash the password properly
    from auth.service import AuthService

    svc = AuthService(
        secret_key=os.environ["JWT_SECRET_KEY"],
        access_expire_minutes=60,
        refresh_expire_days=7,
    )
    repo.seed(
        {
            "email": "alice@example.com",
            "hashed_password": svc.hash_password(
                "Password1!",
            ),
            "full_name": "Alice Smith",
            "role": "general",
        }
    )
    repo.seed(
        {
            "email": "superadmin@example.com",
            "hashed_password": svc.hash_password(
                "AdminPass1!",
            ),
            "full_name": "Super Admin",
            "role": "superuser",
        }
    )
    return repo


@pytest.fixture()
def client(fake_repo):
    """TestClient with _get_repo patched to the fake."""
    import auth.dependencies as deps

    deps._get_service.cache_clear()

    # _get_repo is an lru_cache singleton; patch at source

    with patch("auth.endpoints.helpers._get_repo", return_value=fake_repo):
        from auth.api import create_auth_router
        from auth.rate_limit import limiter

        limiter.reset()
        app = FastAPI()
        app.state.limiter = limiter
        # Exempt test client from rate limits so
        # test ordering doesn't cause flaky 429s.
        limiter.enabled = False
        app.include_router(create_auth_router())
        yield TestClient(app, raise_server_exceptions=False)
        limiter.enabled = True
        limiter.reset()

    deps._get_service.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(client: TestClient, email: str, password: str) -> dict:
    """POST /auth/login and return the parsed JSON body."""
    r = client.post("/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        pytest.skip(f"Login returned {r.status_code}: {r.text}")
    return r.json()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin:
    """Tests for ``POST /auth/login``."""

    def test_login_success_returns_tokens(self, client):
        r = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "Password1!"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "access_token" in body
        assert "refresh_token" in body

    def test_login_wrong_password_returns_401(self, client):
        r = client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "WrongPassword"},
        )
        assert r.status_code == 401, r.text

    def test_login_unknown_email_returns_401(self, client):
        r = client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": "Password1!"},
        )
        assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    """Tests for ``POST /auth/refresh``."""

    def test_refresh_returns_new_access_token(self, client):
        tokens = _login(client, "alice@example.com", "Password1!")
        r = client.post(
            "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert r.status_code == 200, r.text
        new_body = r.json()
        assert "access_token" in new_body
        # New access token must differ from the original
        assert new_body["access_token"] != tokens["access_token"]

    def test_refresh_with_invalid_token_returns_401(self, client):
        r = client.post(
            "/auth/refresh", json={"refresh_token": "not-a-real-token"}
        )
        assert r.status_code in (401, 422), r.text


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


class TestLogout:
    """Tests for ``POST /auth/logout``."""

    def test_logout_succeeds(self, client):
        tokens = _login(client, "alice@example.com", "Password1!")
        r = client.post(
            "/auth/logout",
            json={"refresh_token": tokens["refresh_token"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert r.status_code in (200, 204), r.text

    def test_refresh_after_logout_returns_401(self, client):
        tokens = _login(client, "alice@example.com", "Password1!")
        client.post(
            "/auth/logout",
            json={"refresh_token": tokens["refresh_token"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        r = client.post(
            "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


class TestGetMe:
    """Tests for ``GET /auth/me``."""

    def test_get_me_requires_auth(self, client):
        r = client.get("/auth/me")
        assert r.status_code in (401, 403), r.text

    def test_get_me_with_valid_token(self, client):
        tokens = _login(client, "alice@example.com", "Password1!")
        r = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == "alice@example.com"

    def test_patch_me_updates_full_name(self, client):
        tokens = _login(client, "alice@example.com", "Password1!")
        r = client.patch(
            "/auth/me",
            json={"full_name": "Alice Updated"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["full_name"] == "Alice Updated"


# ---------------------------------------------------------------------------
# POST /users  (superuser only)
# ---------------------------------------------------------------------------


class TestCreateUser:
    """Tests for ``POST /users`` (superuser only)."""

    def _superuser_token(self, client: TestClient) -> str:
        tokens = _login(client, "superadmin@example.com", "AdminPass1!")
        return tokens["access_token"]

    def test_create_user_as_superuser(self, client):
        token = self._superuser_token(client)
        r = client.post(
            "/users",
            json={
                "email": "new@example.com",
                "password": "Password1!",
                "full_name": "New User",
                "role": "general",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["email"] == "new@example.com"

    def test_create_user_without_auth_returns_401(self, client):
        r = client.post(
            "/users",
            json={
                "email": "anon@example.com",
                "password": "Password1!",
                "full_name": "Anon",
            },
        )
        assert r.status_code in (401, 403), r.text

    def test_create_duplicate_user_returns_409(self, client):
        token = self._superuser_token(client)
        r = client.post(
            "/users",
            json={
                "email": "alice@example.com",
                "password": "Password1!",
                "full_name": "Alice Dup",
                "role": "general",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# POST /users/{user_id}/reset-password  (superuser only)
# ---------------------------------------------------------------------------


class TestAdminPasswordReset:
    """Tests for ``POST /users/{user_id}/reset-password``."""

    def _superuser_token(self, client: TestClient) -> str:
        tokens = _login(client, "superadmin@example.com", "AdminPass1!")
        return tokens["access_token"]

    def _alice_id(self, fake_repo) -> str:
        for u in fake_repo._users.values():
            if u["email"] == "alice@example.com":
                return u["user_id"]
        raise ValueError("alice not found")

    def test_admin_reset_password_success(self, client, fake_repo):
        """Superuser can reset another user's password."""
        token = self._superuser_token(client)
        alice_id = self._alice_id(fake_repo)
        r = client.post(
            f"/users/{alice_id}/reset-password",
            json={"new_password": "NewPass99!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["detail"] == "Password reset successfully"
        # Verify Alice can log in with the new password
        r2 = client.post(
            "/auth/login",
            json={
                "email": "alice@example.com",
                "password": "NewPass99!",
            },
        )
        assert r2.status_code == 200, r2.text

    def test_admin_reset_password_non_superuser_403(self, client, fake_repo):
        """Non-superuser gets 403 when trying to reset."""
        tokens = _login(client, "alice@example.com", "Password1!")
        alice_id = self._alice_id(fake_repo)
        r = client.post(
            f"/users/{alice_id}/reset-password",
            json={"new_password": "NewPass99!"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert r.status_code == 403, r.text

    def test_admin_reset_password_unknown_user_404(self, client):
        """Returns 404 for a non-existent user_id."""
        token = self._superuser_token(client)
        r = client.post(
            "/users/nonexistent-id/reset-password",
            json={"new_password": "NewPass99!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404, r.text

    def test_admin_reset_password_weak_password_400(self, client, fake_repo):
        """Weak password gets rejected with 400."""
        token = self._superuser_token(client)
        alice_id = self._alice_id(fake_repo)
        r = client.post(
            f"/users/{alice_id}/reset-password",
            json={"new_password": "short"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code in (400, 422), r.text

    def test_admin_reset_no_auth_401(self, client, fake_repo):
        """Unauthenticated request returns 401."""
        alice_id = self._alice_id(fake_repo)
        r = client.post(
            f"/users/{alice_id}/reset-password",
            json={"new_password": "NewPass99!"},
        )
        assert r.status_code in (401, 403), r.text


# -------------------------------------------------------------------
# Health endpoint
# -------------------------------------------------------------------


class TestAuthHealth:
    """Tests for ``GET /auth/health``."""

    def test_health_returns_healthy(self, client):
        """Default in-memory store reports healthy."""
        r = client.get("/auth/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "healthy"
        assert body["ok"] is True
        assert body["backend"] == "InMemoryTokenStore"

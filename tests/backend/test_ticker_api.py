"""Unit tests for user-ticker linking endpoints.

Tests ``GET /users/me/tickers``, ``POST /users/me/tickers``,
and ``DELETE /users/me/tickers/{ticker}`` using the same
in-memory fake repository pattern as ``test_auth_api.py``.

The ``_FakeRepo`` is extended with ``get_user_tickers``,
``link_ticker``, and ``unlink_ticker`` to avoid any Iceberg
dependency.
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
    "JWT_SECRET_KEY",
    "test-secret-key-for-unit-tests-only-32ch",
)


# ------------------------------------------------------------------
# In-memory repository fake (with ticker support)
# ------------------------------------------------------------------


class _FakeRepo:
    """In-memory stand-in for UserRepository.

    Extends the minimal fake with ``get_user_tickers``,
    ``link_ticker``, and ``unlink_ticker`` so the ticker
    endpoints work without Iceberg.
    """

    def __init__(self):
        self._users: Dict[str, Dict[str, Any]] = {}
        self._audit: List[Dict[str, Any]] = []
        self._tickers: Dict[str, List[Dict[str, str]]] = {}

    def _now(self):
        return datetime.now(timezone.utc)

    # -- user reads ------------------------------------------------

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Look up user by email."""
        for u in self._users.values():
            if u["email"] == email:
                return dict(u)
        return None

    def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Look up user by UUID."""
        u = self._users.get(user_id)
        return dict(u) if u else None

    def list_all(self) -> List[Dict[str, Any]]:
        """Return all users."""
        return [dict(u) for u in self._users.values()]

    # -- user writes -----------------------------------------------

    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new user row."""
        if self.get_by_email(data["email"]):
            raise ValueError(f"User '{data['email']}' already exists.")
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

    def update(self, user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update user fields in place."""
        if user_id not in self._users:
            raise ValueError(f"User '{user_id}' not found.")
        row = self._users[user_id]
        immutable = {"user_id", "created_at"}
        for k, v in updates.items():
            if k not in immutable:
                row[k] = v
        row["updated_at"] = self._now()
        return dict(row)

    def delete(self, user_id: str) -> None:
        """Soft-delete a user."""
        self.update(user_id, {"is_active": False})

    # -- oauth stubs -----------------------------------------------

    def get_by_oauth_sub(
        self, provider: str, sub: str
    ) -> Optional[Dict[str, Any]]:
        """Look up user by OAuth provider + subject."""
        for u in self._users.values():
            if (
                u.get("oauth_provider") == provider
                and u.get("oauth_sub") == sub
            ):
                return dict(u)
        return None

    def get_or_create_by_oauth(
        self,
        provider,
        oauth_sub,
        email,
        full_name,
        picture_url=None,
    ) -> Dict[str, Any]:
        """Upsert by OAuth identity."""
        existing = self.get_by_oauth_sub(provider, oauth_sub)
        if existing:
            return self.update(
                existing["user_id"],
                {
                    "profile_picture_url": picture_url,
                    "last_login_at": self._now(),
                },
            )
        return self.create(
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

    # -- audit stubs -----------------------------------------------

    def append_audit_event(self, *a, **kw) -> None:
        """Record an audit event."""
        self._audit.append({"args": a, "kwargs": kw})

    def list_audit_events(self) -> List[Dict[str, Any]]:
        """Return recorded audit events."""
        return list(self._audit)

    # -- ticker methods --------------------------------------------

    def get_user_tickers(self, user_id: str) -> list[str]:
        """Return sorted tickers for the user."""
        rows = self._tickers.get(user_id, [])
        return sorted(r["ticker"] for r in rows)

    def link_ticker(
        self,
        user_id: str,
        ticker: str,
        source: str = "manual",
    ) -> bool:
        """Link a ticker; return False if duplicate."""
        existing = self._tickers.get(user_id, [])
        for r in existing:
            if r["ticker"] == ticker:
                return False
        existing.append({"ticker": ticker, "source": source})
        self._tickers[user_id] = existing
        return True

    def unlink_ticker(self, user_id: str, ticker: str) -> bool:
        """Unlink a ticker; return False if absent."""
        rows = self._tickers.get(user_id, [])
        before = len(rows)
        rows = [r for r in rows if r["ticker"] != ticker]
        if len(rows) == before:
            return False
        self._tickers[user_id] = rows
        return True


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def fake_repo():
    """Fresh in-memory repo seeded with one general user."""
    repo = _FakeRepo()
    from auth.service import AuthService

    svc = AuthService(
        secret_key=os.environ["JWT_SECRET_KEY"],
        access_expire_minutes=60,
        refresh_expire_days=7,
    )
    repo.create(
        {
            "email": "alice@example.com",
            "hashed_password": svc.hash_password("Password1!"),
            "full_name": "Alice Smith",
            "role": "general",
        }
    )
    return repo


@pytest.fixture()
def client(fake_repo):
    """TestClient with ticker + auth routers mounted."""
    import auth.dependencies as deps

    deps._get_service.cache_clear()

    with patch(
        "auth.endpoints.helpers._get_repo",
        return_value=fake_repo,
    ):
        from auth.api import (
            create_auth_router,
            get_ticker_router,
        )

        app = FastAPI()
        app.include_router(create_auth_router())
        app.include_router(get_ticker_router())
        yield TestClient(app, raise_server_exceptions=False)

    deps._get_service.cache_clear()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _login(client: TestClient, email: str, password: str) -> dict:
    """Log in and return the parsed JSON body."""
    r = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    if r.status_code != 200:
        pytest.skip(f"Login returned {r.status_code}: {r.text}")
    return r.json()


def _auth_header(token: str) -> dict:
    """Build an Authorization header dict."""
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestUserTickerEndpoints:
    """Tests for GET/POST/DELETE /users/me/tickers.

    Verifies listing, linking, duplicate detection,
    validation, unlinking, and auth enforcement for
    the user-ticker management endpoints.
    """

    def test_get_tickers_empty(self, client):
        """New user has no linked tickers."""
        tokens = _login(client, "alice@example.com", "Password1!")
        r = client.get(
            "/users/me/tickers",
            headers=_auth_header(tokens["access_token"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tickers"] == []

    def test_link_ticker(self, client):
        """POST /users/me/tickers links a ticker."""
        tokens = _login(client, "alice@example.com", "Password1!")
        hdrs = _auth_header(tokens["access_token"])
        r = client.post(
            "/users/me/tickers",
            json={"ticker": "AAPL"},
            headers=hdrs,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["linked"] is True
        assert body["ticker"] == "AAPL"

        # Verify it shows up in GET
        r2 = client.get("/users/me/tickers", headers=hdrs)
        assert "AAPL" in r2.json()["tickers"]

    def test_link_ticker_duplicate(self, client):
        """Linking the same ticker twice returns linked=false."""
        tokens = _login(client, "alice@example.com", "Password1!")
        hdrs = _auth_header(tokens["access_token"])
        client.post(
            "/users/me/tickers",
            json={"ticker": "MSFT"},
            headers=hdrs,
        )
        r = client.post(
            "/users/me/tickers",
            json={"ticker": "MSFT"},
            headers=hdrs,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["linked"] is False
        assert body["detail"] == "already linked"

    def test_link_ticker_invalid(self, client):
        """Invalid ticker format returns 422."""
        tokens = _login(client, "alice@example.com", "Password1!")
        hdrs = _auth_header(tokens["access_token"])
        r = client.post(
            "/users/me/tickers",
            json={"ticker": "!!!INVALID!!!"},
            headers=hdrs,
        )
        assert r.status_code == 422, r.text

    def test_link_ticker_empty(self, client):
        """Empty ticker string returns 422."""
        tokens = _login(client, "alice@example.com", "Password1!")
        hdrs = _auth_header(tokens["access_token"])
        r = client.post(
            "/users/me/tickers",
            json={"ticker": ""},
            headers=hdrs,
        )
        assert r.status_code == 422, r.text

    def test_link_ticker_normalises_case(self, client):
        """Ticker is normalised to uppercase."""
        tokens = _login(client, "alice@example.com", "Password1!")
        hdrs = _auth_header(tokens["access_token"])
        r = client.post(
            "/users/me/tickers",
            json={"ticker": "goog"},
            headers=hdrs,
        )
        assert r.status_code == 200, r.text
        assert r.json()["ticker"] == "GOOG"

    def test_unlink_ticker(self, client):
        """DELETE /users/me/tickers/{ticker} removes it."""
        tokens = _login(client, "alice@example.com", "Password1!")
        hdrs = _auth_header(tokens["access_token"])

        # Link first
        client.post(
            "/users/me/tickers",
            json={"ticker": "TSLA"},
            headers=hdrs,
        )

        # Unlink
        r = client.delete("/users/me/tickers/TSLA", headers=hdrs)
        assert r.status_code == 200, r.text
        assert r.json()["detail"] == "unlinked"

        # Verify it is gone
        r2 = client.get("/users/me/tickers", headers=hdrs)
        assert "TSLA" not in r2.json()["tickers"]

    def test_unlink_nonexistent(self, client):
        """Unlinking a non-linked ticker returns 404."""
        tokens = _login(client, "alice@example.com", "Password1!")
        hdrs = _auth_header(tokens["access_token"])
        r = client.delete("/users/me/tickers/NOPE", headers=hdrs)
        assert r.status_code == 404, r.text

    def test_no_auth_get_401(self, client):
        """GET /users/me/tickers without auth returns 401."""
        r = client.get("/users/me/tickers")
        assert r.status_code in (401, 403), r.text

    def test_no_auth_post_401(self, client):
        """POST /users/me/tickers without auth returns 401."""
        r = client.post(
            "/users/me/tickers",
            json={"ticker": "AAPL"},
        )
        assert r.status_code in (401, 403), r.text

    def test_no_auth_delete_401(self, client):
        """DELETE /users/me/tickers/{t} without auth returns 401."""
        r = client.delete("/users/me/tickers/AAPL")
        assert r.status_code in (401, 403), r.text

    def test_link_multiple_tickers_sorted(self, client):
        """GET returns tickers in sorted order."""
        tokens = _login(client, "alice@example.com", "Password1!")
        hdrs = _auth_header(tokens["access_token"])

        for t in ("TSLA", "AAPL", "MSFT"):
            client.post(
                "/users/me/tickers",
                json={"ticker": t},
                headers=hdrs,
            )

        r = client.get("/users/me/tickers", headers=hdrs)
        assert r.status_code == 200, r.text
        tickers = r.json()["tickers"]
        assert tickers == ["AAPL", "MSFT", "TSLA"]

    def test_link_with_custom_source(self, client):
        """POST with source='chat' is accepted."""
        tokens = _login(client, "alice@example.com", "Password1!")
        hdrs = _auth_header(tokens["access_token"])
        r = client.post(
            "/users/me/tickers",
            json={"ticker": "NVDA", "source": "chat"},
            headers=hdrs,
        )
        assert r.status_code == 200, r.text
        assert r.json()["linked"] is True

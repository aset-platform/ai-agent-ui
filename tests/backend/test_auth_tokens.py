"""Unit tests for auth.tokens — JWT creation, decode, and revocation."""

import time

import pytest

from auth.token_store import InMemoryTokenStore

_SECRET = "test-secret-key-for-tokens"


def _fresh_store():
    """Return a new InMemoryTokenStore for each test."""
    return InMemoryTokenStore()


class TestCreateAccessToken:
    """Tests for :func:`auth.tokens.create_access_token`."""

    def test_returns_non_empty_string(self):
        from auth.tokens import create_access_token

        token = create_access_token(
            user_id="u1",
            email="u@test.com",
            role="general",
            secret_key=_SECRET,
            expire_minutes=15,
        )
        assert isinstance(token, str)
        assert len(token) > 10

    def test_payload_contains_expected_claims(self):
        from auth.tokens import (
            create_access_token,
            decode_token,
        )

        token = create_access_token(
            user_id="u1",
            email="u@test.com",
            role="general",
            secret_key=_SECRET,
            expire_minutes=15,
        )
        payload = decode_token(
            token,
            _SECRET,
            _fresh_store(),
            expected_type="access",
        )
        assert payload["sub"] == "u1"
        assert payload["email"] == "u@test.com"
        assert payload["role"] == "general"
        assert payload["type"] == "access"

    def test_expiry_is_in_the_future(self):
        from auth.tokens import (
            create_access_token,
            decode_token,
        )

        token = create_access_token(
            user_id="u1",
            email="u@test.com",
            role="general",
            secret_key=_SECRET,
            expire_minutes=15,
        )
        payload = decode_token(
            token,
            _SECRET,
            _fresh_store(),
            expected_type="access",
        )
        exp = payload["exp"]
        assert exp > time.time()


class TestCreateRefreshToken:
    """Tests for :func:`auth.tokens.create_refresh_token`."""

    def test_returns_string(self):
        from auth.tokens import create_refresh_token

        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        assert isinstance(token, str)

    def test_type_is_refresh(self):
        from auth.tokens import (
            create_refresh_token,
            decode_token,
        )

        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        payload = decode_token(
            token,
            _SECRET,
            _fresh_store(),
            expected_type="refresh",
        )
        assert payload["type"] == "refresh"
        assert payload["sub"] == "u1"


class TestDecodeToken:
    """Tests for :func:`auth.tokens.decode_token`."""

    def test_wrong_secret_raises(self):
        from fastapi import HTTPException

        from auth.tokens import (
            create_access_token,
            decode_token,
        )

        token = create_access_token(
            user_id="u1",
            email="u@test.com",
            role="general",
            secret_key=_SECRET,
            expire_minutes=15,
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_token(
                token,
                "wrong-secret",
                _fresh_store(),
                expected_type="access",
            )
        assert exc_info.value.status_code == 401

    def test_wrong_type_raises(self):
        from fastapi import HTTPException

        from auth.tokens import (
            create_access_token,
            decode_token,
        )

        token = create_access_token(
            user_id="u1",
            email="u@test.com",
            role="general",
            secret_key=_SECRET,
            expire_minutes=15,
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_token(
                token,
                _SECRET,
                _fresh_store(),
                expected_type="refresh",
            )
        assert exc_info.value.status_code == 401

    def test_expired_token_raises(self):
        from fastapi import HTTPException

        from auth.tokens import (
            create_access_token,
            decode_token,
        )

        token = create_access_token(
            user_id="u1",
            email="u@test.com",
            role="general",
            secret_key=_SECRET,
            expire_minutes=-1,
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_token(
                token,
                _SECRET,
                _fresh_store(),
                expected_type="access",
            )
        assert exc_info.value.status_code == 401


class TestRevokeAndDenyList:
    """Tests for revoke and is_token_revoked with TokenStore."""

    def test_token_not_revoked_initially(self):
        from auth.tokens import (
            create_refresh_token,
            is_token_revoked,
        )

        store = _fresh_store()
        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        assert is_token_revoked(
            token, _SECRET, store,
        ) is False

    def test_revoked_token_is_revoked(self):
        from auth.tokens import (
            create_refresh_token,
            is_token_revoked,
            revoke_refresh_token,
        )

        store = _fresh_store()
        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        revoke_refresh_token(token, _SECRET, store)
        assert is_token_revoked(
            token, _SECRET, store,
        ) is True

    def test_decode_revoked_token_raises(self):
        from fastapi import HTTPException

        from auth.tokens import (
            create_refresh_token,
            decode_token,
            revoke_refresh_token,
        )

        store = _fresh_store()
        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        revoke_refresh_token(token, _SECRET, store)
        with pytest.raises(HTTPException) as exc_info:
            decode_token(
                token,
                _SECRET,
                store,
                expected_type="refresh",
            )
        assert exc_info.value.status_code == 401

    def test_revoke_adds_jti_to_store(self):
        from auth.tokens import (
            create_refresh_token,
            revoke_refresh_token,
        )

        store = _fresh_store()
        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        revoke_refresh_token(token, _SECRET, store)
        # InMemoryTokenStore keeps entries in _store dict.
        assert len(store._store) == 1

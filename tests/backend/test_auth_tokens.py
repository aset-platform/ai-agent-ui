"""Unit tests for auth.tokens — JWT creation, decode, and revocation."""

import time
from datetime import datetime, timedelta, timezone

import pytest

_SECRET = "test-secret-key-for-tokens"
_DENY_LIST = set()


def _fresh_deny_list():
    """Return a new empty deny-list set for each test."""
    return set()


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
        from auth.tokens import create_access_token, decode_token

        token = create_access_token(
            user_id="u1",
            email="u@test.com",
            role="general",
            secret_key=_SECRET,
            expire_minutes=15,
        )
        payload = decode_token(
            token, _SECRET, _fresh_deny_list(), expected_type="access"
        )
        assert payload["sub"] == "u1"
        assert payload["email"] == "u@test.com"
        assert payload["role"] == "general"
        assert payload["type"] == "access"

    def test_expiry_is_in_the_future(self):
        from auth.tokens import create_access_token, decode_token

        token = create_access_token(
            user_id="u1",
            email="u@test.com",
            role="general",
            secret_key=_SECRET,
            expire_minutes=15,
        )
        payload = decode_token(
            token, _SECRET, _fresh_deny_list(), expected_type="access"
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
        from auth.tokens import create_refresh_token, decode_token

        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        payload = decode_token(
            token, _SECRET, _fresh_deny_list(), expected_type="refresh"
        )
        assert payload["type"] == "refresh"
        assert payload["sub"] == "u1"


class TestDecodeToken:
    """Tests for :func:`auth.tokens.decode_token`."""

    def test_wrong_secret_raises(self):
        from fastapi import HTTPException

        from auth.tokens import create_access_token, decode_token

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
                _fresh_deny_list(),
                expected_type="access",
            )
        assert exc_info.value.status_code == 401

    def test_wrong_type_raises(self):
        from fastapi import HTTPException

        from auth.tokens import create_access_token, decode_token

        token = create_access_token(
            user_id="u1",
            email="u@test.com",
            role="general",
            secret_key=_SECRET,
            expire_minutes=15,
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_token(
                token, _SECRET, _fresh_deny_list(), expected_type="refresh"
            )
        assert exc_info.value.status_code == 401

    def test_expired_token_raises(self):
        from fastapi import HTTPException

        from auth.tokens import create_access_token, decode_token

        token = create_access_token(
            user_id="u1",
            email="u@test.com",
            role="general",
            secret_key=_SECRET,
            expire_minutes=-1,  # already expired
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_token(
                token, _SECRET, _fresh_deny_list(), expected_type="access"
            )
        assert exc_info.value.status_code == 401


class TestRevokeAndDenyList:
    """Tests for :func:`auth.tokens.revoke_refresh_token` and
    :func:`auth.tokens.is_token_revoked`."""

    def test_token_not_revoked_initially(self):
        from auth.tokens import create_refresh_token, is_token_revoked

        deny_list = _fresh_deny_list()
        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        assert is_token_revoked(token, _SECRET, deny_list) is False

    def test_revoked_token_is_revoked(self):
        from auth.tokens import (
            create_refresh_token,
            is_token_revoked,
            revoke_refresh_token,
        )

        deny_list = _fresh_deny_list()
        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        revoke_refresh_token(token, _SECRET, deny_list)
        assert is_token_revoked(token, _SECRET, deny_list) is True

    def test_decode_revoked_token_raises(self):
        from fastapi import HTTPException

        from auth.tokens import (
            create_refresh_token,
            decode_token,
            revoke_refresh_token,
        )

        deny_list = _fresh_deny_list()
        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        revoke_refresh_token(token, _SECRET, deny_list)
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token, _SECRET, deny_list, expected_type="refresh")
        assert exc_info.value.status_code == 401

    def test_revoke_updates_deny_list_in_place(self):
        from auth.tokens import create_refresh_token, revoke_refresh_token

        deny_list = _fresh_deny_list()
        assert len(deny_list) == 0
        token = create_refresh_token(
            user_id="u1",
            secret_key=_SECRET,
            expire_days=7,
        )
        revoke_refresh_token(token, _SECRET, deny_list)
        assert len(deny_list) == 1

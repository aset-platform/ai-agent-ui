"""Tests for Story 1.4 — Google JWKS signature verification."""

from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest


@pytest.fixture()
def _mock_settings():
    """Return a mock settings object with Google OAuth fields."""
    s = MagicMock()
    s.google_client_id = "test-client-id"
    s.google_client_secret = "test-secret"
    s.oauth_redirect_uri = "http://localhost:3000/cb"
    return s


def test_exchange_google_code_verifies_jwks(_mock_settings):
    """exchange_google_code should call PyJWKClient for sig verification."""
    from auth.oauth_service import OAuthService

    svc = OAuthService(_mock_settings)

    fake_token_resp = MagicMock()
    fake_token_resp.json.return_value = {
        "id_token": "fake.jwt.token",
    }
    fake_token_resp.raise_for_status = MagicMock()

    mock_key = MagicMock()
    mock_key.key = "fake-key"

    with (
        patch("auth.oauth_service.httpx.Client") as mc,
        patch("auth.oauth_service._jwks_client") as jwks,
        patch("auth.oauth_service.jwt.decode") as dec,
    ):
        mc.return_value.__enter__ = MagicMock(
            return_value=MagicMock(
                post=MagicMock(return_value=fake_token_resp),
            ),
        )
        mc.return_value.__exit__ = MagicMock(
            return_value=False,
        )
        jwks.get_signing_key_from_jwt.return_value = mock_key
        dec.return_value = {
            "sub": "123",
            "email": "a@b.com",
            "name": "Test",
            "picture": None,
        }

        result = svc.exchange_google_code("code", "verifier")

    jwks.get_signing_key_from_jwt.assert_called_once_with(
        "fake.jwt.token",
    )
    dec.assert_called_once_with(
        "fake.jwt.token",
        "fake-key",
        algorithms=["RS256"],
        audience="test-client-id",
    )
    assert result["provider"] == "google"
    assert result["sub"] == "123"


def test_expired_token_raises_value_error(_mock_settings):
    """Expired ID token should raise ValueError."""
    from auth.oauth_service import OAuthService

    svc = OAuthService(_mock_settings)

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"id_token": "expired"}
    fake_resp.raise_for_status = MagicMock()

    with (
        patch("auth.oauth_service.httpx.Client") as mc,
        patch("auth.oauth_service._jwks_client") as jwks,
        patch(
            "auth.oauth_service.jwt.decode",
            side_effect=pyjwt.ExpiredSignatureError(),
        ),
    ):
        mc.return_value.__enter__ = MagicMock(
            return_value=MagicMock(
                post=MagicMock(return_value=fake_resp),
            ),
        )
        mc.return_value.__exit__ = MagicMock(
            return_value=False,
        )
        jwks.get_signing_key_from_jwt.return_value = MagicMock(key="k")

        with pytest.raises(ValueError, match="expired"):
            svc.exchange_google_code("code", "v")


def test_invalid_token_raises_value_error(_mock_settings):
    """Invalid ID token should raise ValueError."""
    from auth.oauth_service import OAuthService

    svc = OAuthService(_mock_settings)

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"id_token": "bad"}
    fake_resp.raise_for_status = MagicMock()

    with (
        patch("auth.oauth_service.httpx.Client") as mc,
        patch("auth.oauth_service._jwks_client") as jwks,
        patch(
            "auth.oauth_service.jwt.decode",
            side_effect=pyjwt.InvalidTokenError("bad"),
        ),
    ):
        mc.return_value.__enter__ = MagicMock(
            return_value=MagicMock(
                post=MagicMock(return_value=fake_resp),
            ),
        )
        mc.return_value.__exit__ = MagicMock(
            return_value=False,
        )
        jwks.get_signing_key_from_jwt.return_value = MagicMock(key="k")

        with pytest.raises(ValueError, match="Invalid"):
            svc.exchange_google_code("code", "v")

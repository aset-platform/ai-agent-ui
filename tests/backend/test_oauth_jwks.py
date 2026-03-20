"""Tests for JWKS key rotation and caching in OAuth service.

Covers :class:`CachedJWKSClient` TTL behaviour and stale-key
retry logic, plus integration with
:meth:`OAuthService.exchange_google_code`.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest

from auth.oauth_service import (
    CachedJWKSClient,
    OAuthService,
)

# ── Helpers ───────────────────────────────────────────────


def _make_settings(**overrides):
    """Return minimal settings for OAuthService."""
    defaults = {
        "google_client_id": "test-client-id",
        "google_client_secret": "test-secret",
        "oauth_redirect_uri": "http://localhost/cb",
        "google_jwks_cache_ttl": 3600,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_http_post(id_token="fake.jwt.token"):
    """Return a patched httpx.Client context manager."""
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "id_token": id_token,
    }
    fake_resp.raise_for_status = MagicMock()
    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(
        return_value=MagicMock(
            post=MagicMock(return_value=fake_resp),
        ),
    )
    mock_http.__exit__ = MagicMock(return_value=False)
    return mock_http


# ── CachedJWKSClient: cache TTL ──────────────────────────


class TestJWKSCacheReturns:
    """Second call uses cached JWKS, no re-fetch."""

    def test_cache_hit(self):
        """Consecutive calls reuse the same client."""
        client = CachedJWKSClient("https://example.com/jwks", cache_ttl=3600)
        # Pre-set cache timestamp so cache isn't stale.
        client._cached_at = time.monotonic()
        mock_key = MagicMock()
        with patch.object(client, "_client") as inner:
            inner.get_signing_key_from_jwt.return_value = mock_key
            k1 = client.get_signing_key_from_jwt("t")
            k2 = client.get_signing_key_from_jwt("t")

        assert k1 is mock_key
        assert k2 is mock_key
        assert inner.get_signing_key_from_jwt.call_count == 2


class TestJWKSCacheExpiry:
    """Expired cache triggers fresh fetch."""

    def test_stale_cache_refreshes(self):
        """Cache past TTL creates a new PyJWKClient."""
        client = CachedJWKSClient("https://example.com/jwks", cache_ttl=1)
        # Pre-set cache so first call doesn't trigger refresh.
        client._cached_at = time.monotonic()
        mock_key = MagicMock()

        with patch.object(client, "_client") as inner:
            inner.get_signing_key_from_jwt.return_value = mock_key
            client.get_signing_key_from_jwt("t")

        # Backdate cache to force expiry.
        client._cached_at = time.monotonic() - 10

        with patch("auth.oauth_service.PyJWKClient") as cls:
            new_inner = MagicMock()
            cls.return_value = new_inner
            new_inner.get_signing_key_from_jwt.return_value = mock_key
            key = client.get_signing_key_from_jwt("t")

        cls.assert_called_once()
        assert key is mock_key


class TestJWKSCacheTTLProperty:
    """cache_ttl property returns configured value."""

    def test_ttl_value(self):
        """Property matches constructor arg."""
        client = CachedJWKSClient("https://example.com/jwks", cache_ttl=7200)
        assert client.cache_ttl == 7200


# ── CachedJWKSClient: stale-key retry ────────────────────


class TestJWKSRetryOnFailure:
    """Stale key triggers re-fetch + retry."""

    def test_retry_succeeds(self):
        """First lookup fails, refresh + retry works."""
        client = CachedJWKSClient("https://example.com/jwks", cache_ttl=3600)
        mock_key = MagicMock()

        with patch.object(client, "_client") as inner:
            inner.get_signing_key_from_jwt.side_effect = (
                pyjwt.PyJWKClientError("no key")
            )
            with patch("auth.oauth_service.PyJWKClient") as cls:
                new_inner = MagicMock()
                cls.return_value = new_inner
                new_inner.get_signing_key_from_jwt.return_value = mock_key
                key = client.get_signing_key_with_retry("t")

        assert key is mock_key
        cls.assert_called_once()


class TestJWKSFetchFailureRaises:
    """Network error on both attempts raises."""

    def test_double_failure_raises(self):
        """Both original and retry fail → exception."""
        client = CachedJWKSClient("https://example.com/jwks", cache_ttl=3600)
        err = pyjwt.PyJWKClientError("network error")

        with patch.object(client, "_client") as inner:
            inner.get_signing_key_from_jwt.side_effect = err
            with patch("auth.oauth_service.PyJWKClient") as cls:
                new_inner = MagicMock()
                cls.return_value = new_inner
                new_inner.get_signing_key_from_jwt.side_effect = err
                with pytest.raises(pyjwt.PyJWKClientError):
                    client.get_signing_key_with_retry("t")


# ── OAuthService integration ─────────────────────────────


class TestOAuthServiceJWKSWiring:
    """OAuthService wiring of CachedJWKSClient."""

    def test_default_client_uses_settings_ttl(self):
        """OAuthService creates client with config TTL."""
        svc = OAuthService(_make_settings(google_jwks_cache_ttl=1800))
        assert svc._jwks_client.cache_ttl == 1800

    def test_custom_client_injected(self):
        """Custom CachedJWKSClient is used when passed."""
        custom = CachedJWKSClient("https://custom.example.com/jwks", 999)
        svc = OAuthService(_make_settings(), jwks_client=custom)
        assert svc._jwks_client is custom
        assert svc._jwks_client.cache_ttl == 999


class TestExchangeGoogleCodeJWKS:
    """exchange_google_code uses retry-capable JWKS."""

    def test_exchange_calls_retry_method(self):
        """exchange_google_code calls get_signing_key_with_retry."""
        mock_jwks = MagicMock(spec=CachedJWKSClient)
        mock_jwks.cache_ttl = 3600
        mock_key = MagicMock()
        mock_key.key = "test-key"
        mock_jwks.get_signing_key_with_retry.return_value = mock_key

        svc = OAuthService(_make_settings(), jwks_client=mock_jwks)

        with (
            patch(
                "auth.oauth_service.httpx.Client",
                return_value=_mock_http_post(),
            ),
            patch("auth.oauth_service.jwt.decode") as dec,
        ):
            dec.return_value = {
                "sub": "123",
                "email": "a@b.com",
                "name": "Test",
                "picture": None,
            }
            result = svc.exchange_google_code("code", "verifier")

        mock_jwks.get_signing_key_with_retry.assert_called_once_with(
            "fake.jwt.token"
        )
        assert result["provider"] == "google"
        assert result["sub"] == "123"


class TestExchangeExpiredToken:
    """Expired ID token raises ValueError."""

    def test_expired_raises(self):
        """jwt.ExpiredSignatureError → ValueError."""
        mock_jwks = MagicMock(spec=CachedJWKSClient)
        mock_jwks.cache_ttl = 3600
        mock_key = MagicMock()
        mock_key.key = "k"
        mock_jwks.get_signing_key_with_retry.return_value = mock_key

        svc = OAuthService(_make_settings(), jwks_client=mock_jwks)

        with (
            patch(
                "auth.oauth_service.httpx.Client",
                return_value=_mock_http_post(),
            ),
            patch(
                "auth.oauth_service.jwt.decode",
                side_effect=pyjwt.ExpiredSignatureError(),
            ),
        ):
            with pytest.raises(ValueError, match="expired"):
                svc.exchange_google_code("code", "v")


class TestExchangeInvalidToken:
    """Invalid ID token raises ValueError."""

    def test_invalid_raises(self):
        """jwt.InvalidTokenError → ValueError."""
        mock_jwks = MagicMock(spec=CachedJWKSClient)
        mock_jwks.cache_ttl = 3600
        mock_key = MagicMock()
        mock_key.key = "k"
        mock_jwks.get_signing_key_with_retry.return_value = mock_key

        svc = OAuthService(_make_settings(), jwks_client=mock_jwks)

        with (
            patch(
                "auth.oauth_service.httpx.Client",
                return_value=_mock_http_post(),
            ),
            patch(
                "auth.oauth_service.jwt.decode",
                side_effect=pyjwt.InvalidTokenError("bad"),
            ),
        ):
            with pytest.raises(ValueError, match="Invalid"):
                svc.exchange_google_code("code", "v")

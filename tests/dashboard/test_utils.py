"""Unit tests for pure helper functions in dashboard.callbacks.utils and
dashboard.callbacks.auth_utils.

All tests are pure Python (no Dash server, no Iceberg catalog).
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


class TestGetMarketHelper:
    """Tests for dashboard.callbacks.utils._get_market."""

    def test_nse_ticker_is_india(self):
        """NSE tickers ending in .NS must map to india."""
        from dashboard.callbacks.utils import _get_market

        assert _get_market("RELIANCE.NS") == "india"

    def test_bse_ticker_is_india(self):
        """BSE tickers ending in .BO must map to india."""
        from dashboard.callbacks.utils import _get_market

        assert _get_market("TCS.BO") == "india"

    def test_us_ticker_is_us(self):
        """Tickers without .NS/.BO suffix must map to us."""
        from dashboard.callbacks.utils import _get_market

        assert _get_market("AAPL") == "us"

    def test_case_insensitive(self):
        """Market detection must be case-insensitive."""
        from dashboard.callbacks.utils import _get_market

        assert _get_market("reliance.ns") == "india"


class TestIsValidEmail:
    """Tests for dashboard.callbacks.utils._is_valid_email."""

    def test_valid_email(self):
        """Well-formed email addresses must return True."""
        from dashboard.callbacks.utils import _is_valid_email

        assert _is_valid_email("user@example.com") is True

    def test_no_at_sign(self):
        """Strings without @ must return False."""
        from dashboard.callbacks.utils import _is_valid_email

        assert _is_valid_email("notanemail") is False

    def test_missing_domain(self):
        """Addresses missing the domain part must return False."""
        from dashboard.callbacks.utils import _is_valid_email

        assert _is_valid_email("user@") is False


class TestCheckInputSafety:
    """Tests for dashboard.callbacks.utils._check_input_safety."""

    def test_safe_input_returns_none(self):
        """Safe inputs must return None (no error)."""
        from dashboard.callbacks.utils import _check_input_safety

        assert _check_input_safety("hello world", "field") is None

    def test_too_long_returns_error(self):
        """Inputs exceeding max_len must return an error string."""
        from dashboard.callbacks.utils import _check_input_safety

        result = _check_input_safety("a" * 201, "field", max_len=200)
        assert result is not None
        assert isinstance(result, str)

    def test_sql_injection_returns_error(self):
        """SQL injection patterns must be rejected."""
        from dashboard.callbacks.utils import _check_input_safety

        result = _check_input_safety("' OR 1=1", "field")
        assert result is not None

    def test_xss_returns_error(self):
        """XSS-style payloads must be rejected."""
        from dashboard.callbacks.utils import _check_input_safety

        result = _check_input_safety("javascript:alert(1)", "field")
        assert result is not None


class TestValidateTokenHelper:
    """Tests for dashboard.callbacks.auth_utils._validate_token."""

    def _make_token(self, payload: dict, secret: str = "test-secret") -> str:
        """Encode a JWT with the given payload and secret."""
        from jose import jwt

        return jwt.encode(payload, secret, algorithm="HS256")

    def test_none_returns_none(self):
        """None input must return None without raising."""
        from dashboard.callbacks.auth_utils import _validate_token

        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _validate_token(None) is None

    def test_valid_access_token(self):
        """A valid access token must return the decoded payload."""
        from dashboard.callbacks.auth_utils import _validate_token

        exp = int(
            (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        )
        token = self._make_token({"sub": "u1", "type": "access", "exp": exp})
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            result = _validate_token(token)
        assert result is not None
        assert result["sub"] == "u1"

    def test_refresh_token_rejected(self):
        """Tokens of type refresh must be rejected even if valid otherwise."""
        from dashboard.callbacks.auth_utils import _validate_token

        exp = int(
            (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        )
        token = self._make_token({"sub": "u1", "type": "refresh", "exp": exp})
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _validate_token(token) is None

    def test_expired_token_returns_none(self):
        """Expired tokens must be rejected and return None."""
        from dashboard.callbacks.auth_utils import _validate_token

        exp = int(
            (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
        )
        token = self._make_token({"sub": "u1", "type": "access", "exp": exp})
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            assert _validate_token(token) is None

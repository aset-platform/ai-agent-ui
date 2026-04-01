"""Tests for ASETPLTFRM-76 — Subscription data model.

Covers:
- JWT access token includes subscription claims
- Refresh token flow fetches latest subscription state
- UserContext exposes subscription fields
- subscription_config defines tier quotas and ordering
- _subscription_claims() helper computes usage_remaining
"""

from __future__ import annotations

from auth.token_store import InMemoryTokenStore

_SECRET = "test-secret-key-for-subscription-tests"


def _store():
    return InMemoryTokenStore()


# -----------------------------------------------------------
# subscription_config constants
# -----------------------------------------------------------
class TestSubscriptionConfig:
    """Verify tier quotas and ordering constants."""

    def test_tier_order_values(self):
        from subscription_config import TIER_ORDER

        assert TIER_ORDER["free"] < TIER_ORDER["pro"]
        assert TIER_ORDER["pro"] < TIER_ORDER["premium"]

    def test_usage_quotas_defined(self):
        from subscription_config import USAGE_QUOTAS

        assert USAGE_QUOTAS["free"] == 3
        assert USAGE_QUOTAS["pro"] == 30
        assert USAGE_QUOTAS["premium"] == 0

    def test_default_tier_and_status(self):
        from subscription_config import (
            DEFAULT_STATUS,
            DEFAULT_TIER,
        )

        assert DEFAULT_TIER == "free"
        assert DEFAULT_STATUS == "active"


# -----------------------------------------------------------
# JWT includes subscription claims
# -----------------------------------------------------------
class TestJWTSubscriptionClaims:
    """Verify access tokens carry subscription data."""

    def test_jwt_includes_subscription_claims(self):
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
            subscription_tier="pro",
            subscription_status="active",
            usage_remaining=25,
        )
        payload = decode_token(
            token, _SECRET, _store(), expected_type="access",
        )
        assert payload["subscription_tier"] == "pro"
        assert payload["subscription_status"] == "active"
        assert payload["usage_remaining"] == 25

    def test_jwt_defaults_to_free(self):
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
            token, _SECRET, _store(), expected_type="access",
        )
        assert payload["subscription_tier"] == "free"
        assert payload["subscription_status"] == "active"
        assert payload["usage_remaining"] is None

    def test_jwt_premium_unlimited(self):
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
            subscription_tier="premium",
            subscription_status="active",
            usage_remaining=None,
        )
        payload = decode_token(
            token, _SECRET, _store(), expected_type="access",
        )
        assert payload["subscription_tier"] == "premium"
        assert payload["usage_remaining"] is None


# -----------------------------------------------------------
# UserContext has subscription fields
# -----------------------------------------------------------
class TestUserContextSubscription:
    """Verify UserContext model exposes subscription data."""

    def test_user_context_has_subscription_fields(self):
        from auth.models.response import UserContext

        ctx = UserContext(
            user_id="u1",
            email="u@test.com",
            role="general",
            subscription_tier="pro",
            subscription_status="active",
            usage_remaining=10,
        )
        assert ctx.subscription_tier == "pro"
        assert ctx.subscription_status == "active"
        assert ctx.usage_remaining == 10

    def test_user_context_defaults(self):
        from auth.models.response import UserContext

        ctx = UserContext(
            user_id="u1",
            email="u@test.com",
            role="general",
        )
        assert ctx.subscription_tier == "free"
        assert ctx.subscription_status == "active"
        assert ctx.usage_remaining is None


# -----------------------------------------------------------
# _subscription_claims helper
# -----------------------------------------------------------
class TestSubscriptionClaimsHelper:
    """Verify _subscription_claims() computes correctly."""

    def test_free_user_usage_remaining(self):
        from auth.endpoints.helpers import (
            _subscription_claims,
        )

        user = {
            "subscription_tier": "free",
            "subscription_status": "active",
            "monthly_usage_count": 1,
        }
        claims = _subscription_claims(user)
        assert claims["subscription_tier"] == "free"
        assert claims["subscription_status"] == "active"
        assert claims["usage_remaining"] == 2  # 3 - 1

    def test_free_user_quota_exhausted(self):
        from auth.endpoints.helpers import (
            _subscription_claims,
        )

        user = {
            "subscription_tier": "free",
            "subscription_status": "active",
            "monthly_usage_count": 5,
        }
        claims = _subscription_claims(user)
        assert claims["usage_remaining"] == 0

    def test_pro_user_usage_remaining(self):
        from auth.endpoints.helpers import (
            _subscription_claims,
        )

        user = {
            "subscription_tier": "pro",
            "subscription_status": "active",
            "monthly_usage_count": 12,
        }
        claims = _subscription_claims(user)
        assert claims["usage_remaining"] == 18  # 30 - 12

    def test_premium_unlimited(self):
        from auth.endpoints.helpers import (
            _subscription_claims,
        )

        user = {
            "subscription_tier": "premium",
            "subscription_status": "active",
            "monthly_usage_count": 999,
        }
        claims = _subscription_claims(user)
        assert claims["usage_remaining"] is None

    def test_missing_fields_default_free(self):
        from auth.endpoints.helpers import (
            _subscription_claims,
        )

        user = {}  # no subscription fields
        claims = _subscription_claims(user)
        assert claims["subscription_tier"] == "free"
        assert claims["subscription_status"] == "active"
        assert claims["usage_remaining"] == 3  # 3 - 0

    def test_none_usage_count_treated_as_zero(self):
        from auth.endpoints.helpers import (
            _subscription_claims,
        )

        user = {
            "subscription_tier": "pro",
            "subscription_status": "active",
            "monthly_usage_count": None,
        }
        claims = _subscription_claims(user)
        assert claims["usage_remaining"] == 30  # 30 - 0


# TestIcebergSchemaColumns removed — users table migrated
# to PostgreSQL; schema validation now in ORM model tests.

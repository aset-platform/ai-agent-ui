"""Tests for ASETPLTFRM-78 — Razorpay sandbox integration.

Covers:
- Signature verification
- Plan-to-tier mapping
- User lookup (sub_id priority over cust_id)
- Webhook charged: activates tier, ignores stale subs
- Webhook cancelled: resets to free + clears sub_id
- Webhook payment failed: sets past_due
- Idempotent webhook processing
"""

from __future__ import annotations

import hashlib
import hmac
import os
from unittest.mock import MagicMock, patch

from auth.endpoints.subscription_routes import (
    _find_user_by_razorpay,
    _handle_cancelled,
    _handle_charged,
    _handle_payment_failed,
    _plan_id_to_tier,
    verify_razorpay_signature,
)


# -----------------------------------------------------------
# Signature verification
# -----------------------------------------------------------
class TestRazorpaySignature:
    """Verify HMAC-SHA256 webhook signature."""

    def test_valid_signature_accepted(self):
        secret = "test_webhook_secret"
        body = b'{"event":"subscription.charged"}'
        sig = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        assert verify_razorpay_signature(
            body, sig, secret,
        ) is True

    def test_invalid_signature_rejected(self):
        assert verify_razorpay_signature(
            b"body", "bad_sig", "secret",
        ) is False

    def test_empty_signature_rejected(self):
        assert verify_razorpay_signature(
            b"body", "", "secret",
        ) is False


# -----------------------------------------------------------
# Plan-to-tier mapping
# -----------------------------------------------------------
class TestPlanIdToTier:
    """Verify plan ID -> tier mapping."""

    @patch.dict(
        "os.environ",
        {"RAZORPAY_PLAN_PREMIUM": "plan_premium_123"},
    )
    def test_premium_plan_maps_to_premium(self):
        assert _plan_id_to_tier(
            "plan_premium_123",
        ) == "premium"

    @patch.dict(
        "os.environ",
        {"RAZORPAY_PLAN_PREMIUM": "plan_premium_123"},
    )
    def test_unknown_plan_returns_none(self):
        assert _plan_id_to_tier(
            "plan_unknown_456",
        ) is None

    def test_pro_plan_maps_correctly(self):
        import os

        with patch.dict(
            os.environ,
            {"RAZORPAY_PLAN_PRO": "plan_pro_456"},
        ):
            assert _plan_id_to_tier(
                "plan_pro_456",
            ) == "pro"


# -----------------------------------------------------------
# Find user by Razorpay IDs
# -----------------------------------------------------------
class TestFindUserByRazorpay:
    """Verify user lookup with priority."""

    def test_find_by_subscription_id(self):
        repo = MagicMock()
        repo.list_all.return_value = [
            {
                "user_id": "u1",
                "razorpay_subscription_id": "sub_abc",
                "razorpay_customer_id": "cust_xyz",
            },
        ]
        user = _find_user_by_razorpay(
            repo, "sub_abc", "",
        )
        assert user["user_id"] == "u1"

    def test_find_by_customer_id_fallback(self):
        repo = MagicMock()
        repo.list_all.return_value = [
            {
                "user_id": "u2",
                "razorpay_subscription_id": None,
                "razorpay_customer_id": "cust_xyz",
            },
        ]
        user = _find_user_by_razorpay(
            repo, "sub_unknown", "cust_xyz",
        )
        assert user["user_id"] == "u2"

    def test_sub_id_prioritised_over_cust_id(self):
        """sub_id match wins even if cust_id matches
        a different user."""
        repo = MagicMock()
        repo.list_all.return_value = [
            {
                "user_id": "u1",
                "razorpay_subscription_id": "sub_old",
                "razorpay_customer_id": "cust_shared",
            },
            {
                "user_id": "u2",
                "razorpay_subscription_id": "sub_new",
                "razorpay_customer_id": "cust_shared",
            },
        ]
        user = _find_user_by_razorpay(
            repo, "sub_new", "cust_shared",
        )
        assert user["user_id"] == "u2"

    def test_not_found_returns_none(self):
        repo = MagicMock()
        repo.list_all.return_value = []
        assert _find_user_by_razorpay(
            repo, "sub_x", "cust_x",
        ) is None


# -----------------------------------------------------------
# Webhook event handlers
# -----------------------------------------------------------
@patch.dict(
    os.environ,
    {"RAZORPAY_PLAN_PRO": "plan_pro"},
)
class TestWebhookHandlers:
    """Verify webhook event processing."""

    @patch.dict(
        "os.environ",
        {"RAZORPAY_PLAN_PREMIUM": "plan_prem"},
    )
    def test_handle_charged_activates_tier(self):
        mock_repo = MagicMock()
        mock_repo.list_all.return_value = [
            {
                "user_id": "u1",
                "razorpay_subscription_id": "sub_1",
                "razorpay_customer_id": "cust_1",
            },
        ]
        with patch(
            "auth.endpoints.subscription_routes"
            "._helpers._get_repo",
            return_value=mock_repo,
        ):
            _handle_charged({
                "id": "sub_1",
                "plan_id": "plan_pro",
                "customer_id": "cust_1",
            })

        mock_repo.update.assert_called_once()
        args = mock_repo.update.call_args
        assert args[0][0] == "u1"
        updates = args[0][1]
        assert updates["subscription_tier"] == "pro"
        assert updates["subscription_status"] == "active"

    def test_handle_charged_ignores_stale_sub(self):
        """Webhook for old sub_id is ignored."""
        mock_repo = MagicMock()
        mock_repo.list_all.return_value = [
            {
                "user_id": "u1",
                "razorpay_subscription_id": "sub_new",
                "razorpay_customer_id": "cust_1",
            },
        ]
        with patch(
            "auth.endpoints.subscription_routes"
            "._helpers._get_repo",
            return_value=mock_repo,
        ):
            _handle_charged({
                "id": "sub_old",
                "plan_id": "plan_pro",
                "customer_id": "cust_1",
            })

        # Should NOT update — sub_old != sub_new
        mock_repo.update.assert_not_called()

    def test_handle_cancelled_resets_to_free(self):
        mock_repo = MagicMock()
        mock_repo.list_all.return_value = [
            {
                "user_id": "u1",
                "razorpay_subscription_id": "sub_1",
                "razorpay_customer_id": "cust_1",
            },
        ]
        with patch(
            "auth.endpoints.subscription_routes"
            "._helpers._get_repo",
            return_value=mock_repo,
        ):
            _handle_cancelled({
                "id": "sub_1",
                "customer_id": "cust_1",
            })

        args = mock_repo.update.call_args
        updates = args[0][1]
        assert updates["subscription_tier"] == "free"
        assert updates["subscription_status"] == "cancelled"
        assert updates["razorpay_subscription_id"] is None

    def test_handle_cancelled_ignores_stale_sub(self):
        mock_repo = MagicMock()
        mock_repo.list_all.return_value = [
            {
                "user_id": "u1",
                "razorpay_subscription_id": "sub_new",
                "razorpay_customer_id": "cust_1",
            },
        ]
        with patch(
            "auth.endpoints.subscription_routes"
            "._helpers._get_repo",
            return_value=mock_repo,
        ):
            _handle_cancelled({
                "id": "sub_old",
                "customer_id": "cust_1",
            })

        mock_repo.update.assert_not_called()

    def test_handle_payment_failed_sets_past_due(self):
        mock_repo = MagicMock()
        mock_repo.list_all.return_value = [
            {
                "user_id": "u1",
                "razorpay_subscription_id": "sub_1",
                "razorpay_customer_id": "cust_1",
            },
        ]
        with patch(
            "auth.endpoints.subscription_routes"
            "._helpers._get_repo",
            return_value=mock_repo,
        ):
            _handle_payment_failed(
                {"customer_id": "cust_1"},
                {"id": "sub_1"},
            )

        args = mock_repo.update.call_args
        updates = args[0][1]
        assert updates["subscription_status"] == "past_due"

    def test_handle_charged_no_user_no_crash(self):
        mock_repo = MagicMock()
        mock_repo.list_all.return_value = []
        with patch(
            "auth.endpoints.subscription_routes"
            "._helpers._get_repo",
            return_value=mock_repo,
        ):
            _handle_charged({
                "id": "sub_unknown",
                "plan_id": "plan_pro",
                "customer_id": "cust_unknown",
            })
        mock_repo.update.assert_not_called()

    def test_handle_charged_empty_id_skipped(self):
        mock_repo = MagicMock()
        with patch(
            "auth.endpoints.subscription_routes"
            "._helpers._get_repo",
            return_value=mock_repo,
        ):
            _handle_charged({"id": ""})
        mock_repo.list_all.assert_not_called()

    @patch.dict(
        "os.environ",
        {"RAZORPAY_PLAN_PREMIUM": "plan_prem"},
    )
    def test_idempotent_charged(self):
        """Same event processed twice — same result."""
        mock_repo = MagicMock()
        mock_repo.list_all.return_value = [
            {
                "user_id": "u1",
                "razorpay_subscription_id": "sub_1",
                "razorpay_customer_id": "cust_1",
            },
        ]
        with patch(
            "auth.endpoints.subscription_routes"
            "._helpers._get_repo",
            return_value=mock_repo,
        ):
            _handle_charged({
                "id": "sub_1",
                "plan_id": "plan_pro",
                "customer_id": "cust_1",
            })
            _handle_charged({
                "id": "sub_1",
                "plan_id": "plan_pro",
                "customer_id": "cust_1",
            })

        assert mock_repo.update.call_count == 2
        for call_args in mock_repo.update.call_args_list:
            updates = call_args[0][1]
            assert updates["subscription_tier"] == "pro"

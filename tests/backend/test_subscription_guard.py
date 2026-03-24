"""Tests for ASETPLTFRM-77 — Subscription guard middleware.

Covers:
- require_tier() blocks users below the minimum tier
- require_tier() allows users at or above the minimum
- check_usage_quota() blocks when usage_remaining == 0
- check_usage_quota() allows when quota is available
- check_usage_quota() allows premium (unlimited)
- increment_usage increments on success
- increment_usage does NOT increment on missing user
- reset_monthly_usage resets all counts
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from auth.models.response import UserContext


# -----------------------------------------------------------
# require_tier tests
# -----------------------------------------------------------
class TestRequireTier:
    """Verify require_tier() factory dependency."""

    def test_free_blocked_from_pro(self):
        from auth.dependencies import require_tier

        guard = require_tier("pro")
        user = UserContext(
            user_id="u1",
            email="u@t.com",
            role="general",
            subscription_tier="free",
        )
        with pytest.raises(HTTPException) as exc:
            guard(user=user)
        assert exc.value.status_code == 403

    def test_pro_allowed_for_pro(self):
        from auth.dependencies import require_tier

        guard = require_tier("pro")
        user = UserContext(
            user_id="u1",
            email="u@t.com",
            role="general",
            subscription_tier="pro",
        )
        result = guard(user=user)
        assert result.subscription_tier == "pro"

    def test_premium_allowed_for_pro(self):
        from auth.dependencies import require_tier

        guard = require_tier("pro")
        user = UserContext(
            user_id="u1",
            email="u@t.com",
            role="general",
            subscription_tier="premium",
        )
        result = guard(user=user)
        assert result.subscription_tier == "premium"

    def test_free_allowed_for_free(self):
        from auth.dependencies import require_tier

        guard = require_tier("free")
        user = UserContext(
            user_id="u1",
            email="u@t.com",
            role="general",
            subscription_tier="free",
        )
        result = guard(user=user)
        assert result.subscription_tier == "free"

    def test_pro_blocked_from_premium(self):
        from auth.dependencies import require_tier

        guard = require_tier("premium")
        user = UserContext(
            user_id="u1",
            email="u@t.com",
            role="general",
            subscription_tier="pro",
        )
        with pytest.raises(HTTPException) as exc:
            guard(user=user)
        assert exc.value.status_code == 403


# -----------------------------------------------------------
# check_usage_quota tests
# -----------------------------------------------------------
class TestCheckUsageQuota:
    """Verify check_usage_quota() dependency."""

    def test_quota_exceeded_returns_429(self):
        from auth.dependencies import check_usage_quota

        user = UserContext(
            user_id="u1",
            email="u@t.com",
            role="general",
            subscription_tier="free",
            usage_remaining=0,
        )
        with pytest.raises(HTTPException) as exc:
            check_usage_quota(user=user)
        assert exc.value.status_code == 429

    def test_quota_available_allowed(self):
        from auth.dependencies import check_usage_quota

        user = UserContext(
            user_id="u1",
            email="u@t.com",
            role="general",
            subscription_tier="free",
            usage_remaining=2,
        )
        result = check_usage_quota(user=user)
        assert result.usage_remaining == 2

    def test_premium_unlimited_allowed(self):
        from auth.dependencies import check_usage_quota

        user = UserContext(
            user_id="u1",
            email="u@t.com",
            role="general",
            subscription_tier="premium",
            usage_remaining=None,
        )
        result = check_usage_quota(user=user)
        assert result.usage_remaining is None

    def test_negative_remaining_blocked(self):
        from auth.dependencies import check_usage_quota

        user = UserContext(
            user_id="u1",
            email="u@t.com",
            role="general",
            usage_remaining=-1,
        )
        with pytest.raises(HTTPException) as exc:
            check_usage_quota(user=user)
        assert exc.value.status_code == 429


# -----------------------------------------------------------
# Usage tracking tests
# -----------------------------------------------------------
class TestUsageTracker:
    """Verify increment_usage and reset_monthly_usage."""

    @patch("usage_tracker._logger")
    def test_increment_usage_success(self, _):
        from usage_tracker import (
            _current_month,
            increment_usage,
        )

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {
            "user_id": "u1",
            "monthly_usage_count": 2,
            "usage_month": _current_month(),
        }
        with patch(
            "auth.endpoints.helpers._get_repo",
            return_value=mock_repo,
        ):
            increment_usage("u1")

        mock_repo.update.assert_called_once_with(
            "u1",
            {
                "monthly_usage_count": 3,
                "usage_month": _current_month(),
            },
        )

    @patch("usage_tracker._logger")
    def test_increment_no_crash_on_missing_user(
        self, _,
    ):
        from usage_tracker import increment_usage

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = None
        with patch(
            "auth.endpoints.helpers._get_repo",
            return_value=mock_repo,
        ):
            increment_usage("missing")

        mock_repo.update.assert_not_called()

    @patch("usage_tracker._logger")
    def test_increment_none_count_treated_as_zero(
        self, _,
    ):
        from usage_tracker import (
            _current_month,
            increment_usage,
        )

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {
            "user_id": "u1",
            "monthly_usage_count": None,
            "usage_month": _current_month(),
        }
        with patch(
            "auth.endpoints.helpers._get_repo",
            return_value=mock_repo,
        ):
            increment_usage("u1")

        mock_repo.update.assert_called_once_with(
            "u1",
            {
                "monthly_usage_count": 1,
                "usage_month": _current_month(),
            },
        )

    @patch("usage_tracker._logger")
    def test_increment_auto_resets_on_new_month(
        self, _,
    ):
        """When usage_month is old, reset + archive."""
        from usage_tracker import (
            _current_month,
            increment_usage,
        )

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {
            "user_id": "u1",
            "monthly_usage_count": 5,
            "usage_month": "2025-01",
            "subscription_tier": "pro",
        }
        with patch(
            "auth.endpoints.helpers._get_repo",
            return_value=mock_repo,
        ), patch(
            "usage_tracker._archive_usage",
        ) as mock_archive:
            increment_usage("u1")

        # Should archive old month
        mock_archive.assert_called_once_with(
            "u1", "2025-01", 5, "pro",
        )
        # Two updates: reset then increment
        assert mock_repo.update.call_count == 2
        # Last call should be the increment
        last = mock_repo.update.call_args
        assert last[0][1]["monthly_usage_count"] == 1
        assert last[0][1]["usage_month"] == (
            _current_month()
        )

    @patch("usage_tracker._logger")
    def test_reset_monthly_usage(self, _):
        from usage_tracker import reset_monthly_usage

        mock_repo = MagicMock()
        mock_repo.list_all.return_value = [
            {
                "user_id": "u1",
                "monthly_usage_count": 5,
                "subscription_tier": "pro",
                "usage_month": "2026-03",
            },
            {
                "user_id": "u2",
                "monthly_usage_count": 0,
            },
            {
                "user_id": "u3",
                "monthly_usage_count": 12,
                "subscription_tier": "free",
                "usage_month": "2026-03",
            },
        ]
        with patch(
            "auth.endpoints.helpers._get_repo",
            return_value=mock_repo,
        ), patch(
            "usage_tracker._archive_usage",
        ) as mock_archive:
            count = reset_monthly_usage()

        assert count == 2  # u1 and u3 had count > 0
        assert mock_repo.update.call_count == 2
        assert mock_archive.call_count == 2

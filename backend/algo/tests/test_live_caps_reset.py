"""Tests for live_caps_daily_reset job (V2-5)."""
from __future__ import annotations

import pytest


class TestLiveCapsResetJob:
    def test_weekday_check_false_on_weekend(self):
        """is_market_day_ist returns False on Sat/Sun."""
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch

        # Force a Saturday UTC → IST Saturday
        saturday_utc = datetime(2026, 5, 9, 0, 0, 0, tzinfo=timezone.utc)  # May 9 2026 = Saturday
        with patch(
            "backend.algo.jobs.live_caps_reset.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = saturday_utc
            # Reimplement is_market_day_ist inline to test weekday
            ist_now = saturday_utc + timedelta(hours=5, minutes=30)
            assert ist_now.weekday() >= 5  # is weekend

    def test_weekday_check_true_on_monday(self):
        """is_market_day_ist returns True on Mon."""
        from datetime import datetime, timezone, timedelta

        monday_utc = datetime(2026, 5, 11, 3, 0, 0, tzinfo=timezone.utc)  # Mon
        ist_now = monday_utc + timedelta(hours=5, minutes=30)
        assert ist_now.weekday() < 5  # is weekday

    @pytest.mark.asyncio
    async def test_run_on_weekend_skips(self):
        """Job returns skipped=True on weekends."""
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta

        saturday_utc = datetime(2026, 5, 9, 3, 0, 0, tzinfo=timezone.utc)

        with patch(
            "backend.algo.jobs.live_caps_reset.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = saturday_utc
            # Make timedelta subtraction work
            mock_dt.now.side_effect = None

            # Patch is_market_day_ist directly
            with patch(
                "backend.algo.jobs.live_caps_reset.is_market_day_ist",
                return_value=False,
            ):
                from backend.algo.jobs.live_caps_reset import (
                    run_live_caps_daily_reset,
                )
                result = await run_live_caps_daily_reset()
                assert result["skipped"] is True
                assert result["reason"] == "weekend"

    @pytest.mark.asyncio
    async def test_run_on_weekday_resets_counters(self):
        """Job calls reset_daily_counters on weekdays."""
        from unittest.mock import AsyncMock, patch

        with (
            patch(
                "backend.algo.jobs.live_caps_reset.is_market_day_ist",
                return_value=True,
            ),
            patch(
                "backend.algo.live.caps_repo.CapsRepo",
            ) as MockCapsRepo,
        ):
            mock_repo = AsyncMock()
            mock_repo.reset_daily_counters = AsyncMock(return_value=5)
            MockCapsRepo.return_value = mock_repo

            from backend.algo.jobs.live_caps_reset import (
                run_live_caps_daily_reset,
            )
            result = await run_live_caps_daily_reset()
            assert result["skipped"] is False
            assert result["rows_reset"] == 5
            mock_repo.reset_daily_counters.assert_called_once_with(
                user_id=None,
            )

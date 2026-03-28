"""Tests for day-of-month scheduling features."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


# ── _next_run_ist_dates tests ────────────────────────


class TestNextRunIstDates:
    """Tests for forward day-of-month lookup."""

    def test_basic(self):
        """Returns a date on day 1 or 15."""
        from jobs.scheduler_service import (
            _next_run_ist_dates,
        )

        result = _next_run_ist_dates("1,15", "18:00")
        assert result is not None
        assert result.day in {1, 15}
        assert result > datetime.now(IST)

    def test_empty_returns_none(self):
        """Empty cron_dates returns None."""
        from jobs.scheduler_service import (
            _next_run_ist_dates,
        )

        assert _next_run_ist_dates("", "18:00") is None

    def test_result_is_ist_aware(self):
        """Result has IST timezone."""
        from jobs.scheduler_service import (
            _next_run_ist_dates,
        )

        result = _next_run_ist_dates("1,15", "18:00")
        assert result is not None
        assert result.tzinfo is not None


# ── _last_window_dates tests ─────────────────────────


class TestLastWindowDates:
    """Tests for backward day-of-month lookup."""

    def test_basic(self):
        """Returns a past date on day 1 or 15."""
        from jobs.scheduler_service import (
            _last_window_dates,
        )

        result = _last_window_dates("1,15", "00:00")
        assert result is not None
        assert result.day in {1, 15}
        assert result <= datetime.now(IST)

    def test_empty_returns_none(self):
        """Empty cron_dates returns None."""
        from jobs.scheduler_service import (
            _last_window_dates,
        )

        assert _last_window_dates("", "00:00") is None


# ── Monthly registration tests ───────────────────────


class TestMonthlyRegistration:
    """Tests for day-of-month job registration."""

    def test_monthly_uses_daily(self):
        """cron_dates job registers as daily."""
        from jobs.scheduler_service import (
            SchedulerService,
        )

        svc = SchedulerService(MagicMock())
        job = {
            "job_id": "m1",
            "cron_dates": "1,15",
            "cron_days": "",
            "cron_time": "18:00",
        }
        svc._register_schedule(job)
        jobs = svc._scheduler.get_jobs()
        assert len(jobs) == 1

    def test_weekly_ignores_cron_dates(self):
        """Weekly job with no cron_dates uses day."""
        from jobs.scheduler_service import (
            SchedulerService,
        )

        svc = SchedulerService(MagicMock())
        job = {
            "job_id": "w1",
            "cron_dates": "",
            "cron_days": "mon",
            "cron_time": "18:00",
        }
        svc._register_schedule(job)
        jobs = svc._scheduler.get_jobs()
        assert len(jobs) == 1

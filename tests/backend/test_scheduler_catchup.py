"""Tests for scheduler missed-job catch-up on startup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


# ── _last_scheduled_window tests ─────────────────────


class TestLastScheduledWindow:
    """Tests for _last_scheduled_window helper."""

    def test_daily_job_past_time_returns_today(self):
        """Daily job whose time has passed today."""
        from jobs.scheduler_service import (
            _last_scheduled_window,
        )

        days = [
            "mon", "tue", "wed", "thu",
            "fri", "sat", "sun",
        ]
        now = datetime.now(IST)
        # Use a time 2 hours ago
        past_time = (now - timedelta(hours=2)).strftime(
            "%H:%M",
        )
        result = _last_scheduled_window(days, past_time)
        assert result is not None
        assert result.date() == now.date()
        assert result <= now

    def test_daily_job_future_time_returns_yesterday(
        self,
    ):
        """Daily job whose time hasn't arrived yet."""
        from jobs.scheduler_service import (
            _last_scheduled_window,
        )

        days = [
            "mon", "tue", "wed", "thu",
            "fri", "sat", "sun",
        ]
        now = datetime.now(IST)
        # Use a time 2 hours from now
        future_time = (
            now + timedelta(hours=2)
        ).strftime("%H:%M")
        result = _last_scheduled_window(
            days, future_time,
        )
        assert result is not None
        assert result.date() == (
            now - timedelta(days=1)
        ).date()

    def test_weekday_only_skips_weekend(self):
        """Weekday-only job on a Monday looks back."""
        from jobs.scheduler_service import (
            _last_scheduled_window,
        )

        days = ["mon", "tue", "wed", "thu", "fri"]
        # Mock now as Monday 10:00 IST
        monday = datetime(
            2026, 3, 30, 10, 0, tzinfo=IST,
        )  # Monday
        with patch(
            "jobs.scheduler_service.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = monday
            mock_dt.strptime = datetime.strptime
            mock_dt.combine = datetime.combine
            mock_dt.side_effect = lambda *a, **k: (
                datetime(*a, **k)
            )
            result = _last_scheduled_window(
                days, "09:00",
            )
        assert result is not None
        assert result.date() == monday.date()

    def test_empty_cron_days_returns_none(self):
        """No cron days → None."""
        from jobs.scheduler_service import (
            _last_scheduled_window,
        )

        result = _last_scheduled_window([], "18:00")
        assert result is None

    def test_returns_ist_aware_datetime(self):
        """Result should be IST-aware."""
        from jobs.scheduler_service import (
            _last_scheduled_window,
        )

        days = [
            "mon", "tue", "wed", "thu",
            "fri", "sat", "sun",
        ]
        now = datetime.now(IST)
        past = (now - timedelta(hours=1)).strftime(
            "%H:%M",
        )
        result = _last_scheduled_window(days, past)
        assert result is not None
        assert result.tzinfo is not None


# ── trigger_now trigger_type tests ───────────────────


class TestTriggerType:
    """Tests for trigger_type propagation."""

    def test_trigger_now_default_manual(self):
        """Default trigger_type is 'manual'."""
        from jobs.scheduler_service import (
            SchedulerService,
        )

        repo = MagicMock()
        svc = SchedulerService(repo)
        svc._jobs = {
            "j1": {
                "job_id": "j1",
                "job_type": "data_refresh",
                "name": "test",
                "scope": "all",
            },
        }
        with patch.dict(
            "jobs.executor.JOB_EXECUTORS",
            {"data_refresh": MagicMock()},
        ):
            svc.trigger_now("j1")

        call_args = repo.append_scheduler_run.call_args
        run = call_args[0][0]
        assert run["trigger_type"] == "manual"

    def test_trigger_now_catchup_type(self):
        """Catch-up trigger_type is passed through."""
        from jobs.scheduler_service import (
            SchedulerService,
        )

        repo = MagicMock()
        svc = SchedulerService(repo)
        svc._jobs = {
            "j1": {
                "job_id": "j1",
                "job_type": "data_refresh",
                "name": "test",
                "scope": "all",
            },
        }
        with patch.dict(
            "jobs.executor.JOB_EXECUTORS",
            {"data_refresh": MagicMock()},
        ):
            svc.trigger_now(
                "j1", trigger_type="catchup",
            )

        call_args = repo.append_scheduler_run.call_args
        run = call_args[0][0]
        assert run["trigger_type"] == "catchup"

    def test_trigger_now_scheduled_type(self):
        """Scheduled trigger_type is passed through."""
        from jobs.scheduler_service import (
            SchedulerService,
        )

        repo = MagicMock()
        svc = SchedulerService(repo)
        svc._jobs = {
            "j1": {
                "job_id": "j1",
                "job_type": "data_refresh",
                "name": "test",
                "scope": "all",
            },
        }
        with patch.dict(
            "jobs.executor.JOB_EXECUTORS",
            {"data_refresh": MagicMock()},
        ):
            svc.trigger_now(
                "j1", trigger_type="scheduled",
            )

        call_args = repo.append_scheduler_run.call_args
        run = call_args[0][0]
        assert run["trigger_type"] == "scheduled"


# ── _catchup_missed_jobs tests ───────────────────────


class TestCatchupMissedJobs:
    """Tests for the catch-up logic."""

    def _make_svc(self, jobs, last_run=None):
        """Helper to build a SchedulerService."""
        from jobs.scheduler_service import (
            SchedulerService,
        )

        repo = MagicMock()
        repo.get_last_run_for_job.return_value = (
            last_run
        )
        svc = SchedulerService(repo)
        svc._jobs = jobs
        return svc, repo

    def test_catchup_triggers_when_stale(self):
        """Triggers catch-up when last run is old."""
        now_ist = datetime.now(IST)
        old_time = (
            now_ist - timedelta(hours=26)
        ).astimezone(UTC).replace(tzinfo=None)
        past_cron = (
            now_ist - timedelta(hours=2)
        ).strftime("%H:%M")
        jobs = {
            "j1": {
                "job_id": "j1",
                "enabled": True,
                "cron_days": "mon,tue,wed,thu,fri,sat,sun",
                "cron_time": past_cron,
                "name": "test",
                "job_type": "data_refresh",
                "scope": "all",
            },
        }
        last_run = {
            "started_at": old_time.isoformat(),
            "status": "success",
        }
        svc, repo = self._make_svc(jobs, last_run)

        with patch.dict(
            "jobs.executor.JOB_EXECUTORS",
            {"data_refresh": MagicMock()},
        ):
            svc._catchup_missed_jobs()

        repo.append_scheduler_run.assert_called_once()
        run = repo.append_scheduler_run.call_args[0][0]
        assert run["trigger_type"] == "catchup"

    def test_catchup_skips_recent_run(self):
        """Skips catch-up when a recent run exists."""
        now_ist = datetime.now(IST)
        recent = (
            now_ist - timedelta(minutes=30)
        ).astimezone(UTC).replace(tzinfo=None)
        past_cron = (
            now_ist - timedelta(hours=1)
        ).strftime("%H:%M")
        jobs = {
            "j1": {
                "job_id": "j1",
                "enabled": True,
                "cron_days": "mon,tue,wed,thu,fri,sat,sun",
                "cron_time": past_cron,
                "name": "test",
                "job_type": "data_refresh",
                "scope": "all",
            },
        }
        last_run = {
            "started_at": recent.isoformat(),
            "status": "success",
        }
        svc, repo = self._make_svc(jobs, last_run)
        svc._catchup_missed_jobs()
        repo.append_scheduler_run.assert_not_called()

    def test_catchup_skips_running_job(self):
        """Skips catch-up when job is still running."""
        now_ist = datetime.now(IST)
        old_time = (
            now_ist - timedelta(hours=26)
        ).astimezone(UTC).replace(tzinfo=None)
        past_cron = (
            now_ist - timedelta(hours=2)
        ).strftime("%H:%M")
        jobs = {
            "j1": {
                "job_id": "j1",
                "enabled": True,
                "cron_days": "mon,tue,wed,thu,fri,sat,sun",
                "cron_time": past_cron,
                "name": "test",
                "job_type": "data_refresh",
                "scope": "all",
            },
        }
        last_run = {
            "started_at": old_time.isoformat(),
            "status": "running",
        }
        svc, repo = self._make_svc(jobs, last_run)
        svc._catchup_missed_jobs()
        repo.append_scheduler_run.assert_not_called()

    def test_catchup_triggers_never_run_job(self):
        """Triggers catch-up for a job that never ran."""
        now_ist = datetime.now(IST)
        past_cron = (
            now_ist - timedelta(hours=1)
        ).strftime("%H:%M")
        jobs = {
            "j1": {
                "job_id": "j1",
                "enabled": True,
                "cron_days": "mon,tue,wed,thu,fri,sat,sun",
                "cron_time": past_cron,
                "name": "test",
                "job_type": "data_refresh",
                "scope": "all",
            },
        }
        svc, repo = self._make_svc(jobs, None)

        with patch.dict(
            "jobs.executor.JOB_EXECUTORS",
            {"data_refresh": MagicMock()},
        ):
            svc._catchup_missed_jobs()

        repo.append_scheduler_run.assert_called_once()
        run = repo.append_scheduler_run.call_args[0][0]
        assert run["trigger_type"] == "catchup"

    def test_catchup_skips_disabled_job(self):
        """Disabled jobs are never caught up."""
        jobs = {
            "j1": {
                "job_id": "j1",
                "enabled": False,
                "cron_days": "mon,tue,wed,thu,fri,sat,sun",
                "cron_time": "18:00",
                "name": "test",
            },
        }
        svc, repo = self._make_svc(jobs, None)
        svc._catchup_missed_jobs()
        repo.get_last_run_for_job.assert_not_called()


# ── Schedule registration tests ──────────────────────


class TestScheduleRegistration:
    """Verify schedule lib receives IST time directly."""

    def test_register_uses_ist_not_utc(self):
        """Job at 21:00 IST registers as 21:00."""
        from jobs.scheduler_service import (
            SchedulerService,
        )

        repo = MagicMock()
        svc = SchedulerService(repo)
        job = {
            "job_id": "test_job",
            "cron_days": "mon",
            "cron_time": "21:00",
        }

        svc._register_schedule(job)

        jobs = svc._scheduler.get_jobs()
        assert len(jobs) == 1
        assert (
            jobs[0].at_time.strftime("%H:%M")
            == "21:00"
        )

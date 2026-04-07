"""Persistent scheduler service.

Loads job definitions from Iceberg on startup, registers
them with the ``schedule`` library, and runs a daemon
thread that checks for pending jobs every 30 seconds.

All times stored and displayed in IST
(``Asia/Kolkata``).  The ``schedule`` library works in
local process time, so we convert IST to UTC internally.
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import schedule

from config import get_settings
from jobs.executor import JOB_EXECUTORS

_logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

_DAY_MAP = {
    "mon": "monday",
    "tue": "tuesday",
    "wed": "wednesday",
    "thu": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
}


def _next_run_ist(
    cron_days: list[str], cron_time: str,
) -> datetime | None:
    """Calculate the next run datetime in IST."""
    now = datetime.now(IST)
    time_obj = datetime.strptime(
        cron_time, "%H:%M",
    ).time()
    day_names = [
        "mon", "tue", "wed", "thu",
        "fri", "sat", "sun",
    ]

    for offset in range(8):
        candidate = now + timedelta(days=offset)
        day_abbr = day_names[candidate.weekday()]
        if day_abbr in cron_days:
            candidate_dt = datetime.combine(
                candidate.date(), time_obj, tzinfo=IST,
            )
            if candidate_dt > now:
                return candidate_dt
    return None


def _last_scheduled_window(
    cron_days: list[str], cron_time: str,
) -> datetime | None:
    """Find the most recent past scheduled window.

    Looks backward up to 7 days from now (IST) and
    returns the IST-aware datetime of the last window
    that should have fired, or ``None``.
    """
    now = datetime.now(IST)
    time_obj = datetime.strptime(
        cron_time, "%H:%M",
    ).time()
    day_names = [
        "mon", "tue", "wed", "thu",
        "fri", "sat", "sun",
    ]

    for offset in range(8):
        candidate = now - timedelta(days=offset)
        day_abbr = day_names[candidate.weekday()]
        if day_abbr in cron_days:
            candidate_dt = datetime.combine(
                candidate.date(), time_obj, tzinfo=IST,
            )
            if candidate_dt <= now:
                return candidate_dt
    return None


def _next_run_ist_dates(
    cron_dates: str, cron_time: str,
) -> datetime | None:
    """Next run in IST for day-of-month schedule."""
    stripped = cron_dates.strip()
    if not stripped:
        return None
    allowed = {
        int(d.strip())
        for d in stripped.split(",")
        if d.strip().isdigit()
    }
    if not allowed:
        return None
    now = datetime.now(IST)
    time_obj = datetime.strptime(
        cron_time, "%H:%M",
    ).time()
    for offset in range(63):
        candidate = now + timedelta(days=offset)
        if candidate.day in allowed:
            candidate_dt = datetime.combine(
                candidate.date(),
                time_obj,
                tzinfo=IST,
            )
            if candidate_dt > now:
                return candidate_dt
    return None


def _last_window_dates(
    cron_dates: str, cron_time: str,
) -> datetime | None:
    """Most recent past window for day-of-month."""
    stripped = cron_dates.strip()
    if not stripped:
        return None
    allowed = {
        int(d.strip())
        for d in stripped.split(",")
        if d.strip().isdigit()
    }
    if not allowed:
        return None
    now = datetime.now(IST)
    time_obj = datetime.strptime(
        cron_time, "%H:%M",
    ).time()
    for offset in range(63):
        candidate = now - timedelta(days=offset)
        if candidate.day in allowed:
            candidate_dt = datetime.combine(
                candidate.date(),
                time_obj,
                tzinfo=IST,
            )
            if candidate_dt <= now:
                return candidate_dt
    return None


class SchedulerService:
    """Persistent job scheduler backed by Iceberg."""

    def __init__(
        self, repo, max_workers: int = 3,
    ) -> None:
        self._repo = repo
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
        )
        self._futures: dict[str, Future] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._scheduler = schedule.Scheduler()
        self._thread: threading.Thread | None = None

    # ----------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------

    def start(self) -> None:
        """Load jobs from Iceberg and start the daemon."""
        self._cleanup_stale_runs()
        self.reload_jobs()
        if get_settings().scheduler_catchup_enabled:
            self._catchup_missed_jobs()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="scheduler-svc",
        )
        self._thread.start()
        _logger.info(
            "Scheduler service started (%d jobs)",
            len(self._jobs),
        )

    def _cleanup_stale_runs(self) -> None:
        """Mark any 'running' records as 'failed'.

        On restart, no futures survive — any run still
        marked 'running' in Iceberg was interrupted.
        """
        try:
            runs = self._repo.get_scheduler_runs(
                days=7,
            )
            now = datetime.now(UTC)
            for r in runs:
                if r.get("status") == "running":
                    run_id = r.get("run_id", "")
                    _logger.info(
                        "Cleaning up stale run %s",
                        run_id,
                    )
                    self._repo.update_scheduler_run(
                        run_id,
                        {
                            "status": "failed",
                            "completed_at": now,
                            "error_message": (
                                "Server restarted"
                                " while job was running"
                            ),
                        },
                    )
        except Exception:
            _logger.warning(
                "Stale run cleanup failed",
                exc_info=True,
            )

    def _catchup_missed_jobs(self) -> None:
        """Trigger catch-up runs for missed windows.

        For each enabled job, compute the most recent
        past scheduled window and compare it against the
        last run.  If no run covers that window, fire a
        single catch-up.
        """
        for job_id, job in self._jobs.items():
            if not job.get("enabled"):
                continue
            cron_dates = (
                job.get("cron_dates", "") or ""
            ).strip()
            cron_days = (
                job.get("cron_days", "") or ""
            ).split(",")
            cron_days = [
                d.strip().lower() for d in cron_days
                if d.strip()
            ]
            cron_time = job.get("cron_time", "18:00")

            if cron_dates:
                last_window = _last_window_dates(
                    cron_dates, cron_time,
                )
            else:
                last_window = _last_scheduled_window(
                    cron_days, cron_time,
                )
            if last_window is None:
                continue

            # Convert window to UTC tz-naive for
            # comparison with Iceberg timestamps
            window_utc = (
                last_window.astimezone(UTC)
                .replace(tzinfo=None)
            )

            last_run = self._repo.get_last_run_for_job(
                job_id,
            )

            if last_run:
                status = last_run.get("status")
                if status == "running":
                    _logger.debug(
                        "Catchup skip %s: still"
                        " running",
                        job.get("name"),
                    )
                    continue

                started = last_run.get("started_at")
                if isinstance(started, str):
                    started = datetime.fromisoformat(
                        started.replace("Z", "+00:00"),
                    ).replace(tzinfo=None)
                elif hasattr(started, "replace"):
                    started = started.replace(
                        tzinfo=None,
                    )

                if started and started >= window_utc:
                    _logger.debug(
                        "Catchup skip %s: recent run"
                        " at %s",
                        job.get("name"), started,
                    )
                    continue

            _logger.info(
                "Catchup trigger: job=%s name=%s"
                " last_window=%s last_run=%s",
                job_id,
                job.get("name"),
                last_window.strftime("%Y-%m-%d %H:%M"),
                (
                    last_run.get("started_at")
                    if last_run else "never"
                ),
            )
            self.trigger_now(
                job_id, trigger_type="catchup",
            )

    def _loop(self) -> None:
        """Run pending jobs every 30 seconds."""
        import time as _time

        while True:
            try:
                self._scheduler.run_pending()
            except Exception:
                _logger.warning(
                    "Scheduler tick error",
                    exc_info=True,
                )
            _time.sleep(30)

    # ----------------------------------------------------------
    # Job management
    # ----------------------------------------------------------

    def reload_jobs(self) -> None:
        """Reload all jobs from Iceberg into schedule."""
        self._scheduler.clear()
        self._jobs.clear()
        rows = self._repo.get_scheduled_jobs()
        for row in rows:
            jid = row.get("job_id", "")
            self._jobs[jid] = row
            if row.get("enabled"):
                self._register_schedule(row)
        _logger.info(
            "Loaded %d scheduler jobs from Iceberg",
            len(self._jobs),
        )

    def _register_schedule(self, job: dict) -> None:
        """Register a single job with the schedule lib."""
        cron_dates = (
            job.get("cron_dates", "") or ""
        ).strip()
        cron_days = (
            job.get("cron_days", "") or ""
        ).split(",")
        cron_time = job.get("cron_time", "18:00")
        job_id = job["job_id"]

        if cron_dates:
            # Day-of-month: register daily, gate in
            # _trigger_job on matching day
            self._scheduler.every().day.at(
                cron_time,
            ).do(
                self._trigger_job, job_id,
            ).tag(job_id)
        else:
            for day_abbr in cron_days:
                day_abbr = day_abbr.strip().lower()
                day_full = _DAY_MAP.get(day_abbr)
                if not day_full:
                    continue
                sched_day = getattr(
                    self._scheduler.every(), day_full,
                )
                # schedule lib uses local time (IST)
                sched_day.at(cron_time).do(
                    self._trigger_job, job_id,
                ).tag(job_id)

    def add_job(self, job: dict) -> str:
        """Create and persist a new job. Returns job_id."""
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        job["job_id"] = job_id
        job["enabled"] = True
        job["created_at"] = now
        job["updated_at"] = now
        self._repo.upsert_scheduled_job(job)
        self._jobs[job_id] = job
        self._register_schedule(job)
        _logger.info("Added scheduler job %s", job_id)
        return job_id

    def remove_job(self, job_id: str) -> None:
        """Delete a job from Iceberg and unregister."""
        self._scheduler.clear(job_id)
        self._jobs.pop(job_id, None)
        self._repo.delete_scheduled_job(job_id)
        _logger.info("Removed scheduler job %s", job_id)

    def toggle_job(
        self, job_id: str, enabled: bool,
    ) -> None:
        """Enable or disable a job."""
        job = self._jobs.get(job_id)
        if not job:
            return
        job["enabled"] = enabled
        job["updated_at"] = datetime.now(UTC)
        self._repo.upsert_scheduled_job(job)
        self._scheduler.clear(job_id)
        if enabled:
            self._register_schedule(job)
        _logger.info(
            "Toggled job %s → %s",
            job_id,
            "enabled" if enabled else "disabled",
        )

    def update_job(
        self, job_id: str, updates: dict,
    ) -> None:
        """Update job fields and re-register."""
        job = self._jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = datetime.now(UTC)
        self._repo.upsert_scheduled_job(job)
        self._scheduler.clear(job_id)
        if job.get("enabled"):
            self._register_schedule(job)

    # ----------------------------------------------------------
    # Execution
    # ----------------------------------------------------------

    def _trigger_job(self, job_id: str) -> None:
        """Called by schedule lib when a job fires."""
        job = self._jobs.get(job_id)
        if not job or not job.get("enabled"):
            return

        # Day-of-month gate: skip if today doesn't
        # match any configured date
        cron_dates = (
            job.get("cron_dates", "") or ""
        ).strip()
        if cron_dates:
            today_dom = datetime.now(IST).day
            allowed = [
                int(d.strip())
                for d in cron_dates.split(",")
                if d.strip().isdigit()
            ]
            if today_dom not in allowed:
                return

        with self._lock:
            existing = self._futures.get(job_id)
            if existing and not existing.done():
                _logger.info(
                    "Job %s already running — skip",
                    job_id,
                )
                return

        run_id = self.trigger_now(
            job_id, trigger_type="scheduled",
        )
        if run_id:
            _logger.info(
                "Scheduled trigger: job=%s run=%s",
                job_id,
                run_id,
            )

    def trigger_now(
        self,
        job_id: str,
        trigger_type: str = "manual",
    ) -> str | None:
        """Trigger a job. Returns run_id."""
        job = self._jobs.get(job_id)
        if not job:
            return None

        executor_fn = JOB_EXECUTORS.get(
            job.get("job_type", ""),
        )
        if not executor_fn:
            _logger.warning(
                "No executor for job type %s",
                job.get("job_type"),
            )
            return None

        run_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        run = {
            "run_id": run_id,
            "job_id": job_id,
            "job_name": job.get("name", ""),
            "job_type": job.get("job_type", ""),
            "scope": job.get("scope", "all"),
            "status": "running",
            "started_at": now,
            "completed_at": None,
            "duration_secs": None,
            "tickers_total": 0,
            "tickers_done": 0,
            "error_message": None,
            "trigger_type": trigger_type,
        }
        self._repo.append_scheduler_run(run)

        scope = job.get("scope", "all")
        cancel_event = threading.Event()
        self._cancel_events[run_id] = cancel_event

        def _run():
            start = datetime.now(UTC)
            try:
                executor_fn(
                    scope, run_id, self._repo,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                _logger.warning(
                    "Job %s run %s failed: %s",
                    job_id,
                    run_id,
                    exc,
                )
                elapsed = (
                    datetime.now(UTC) - start
                ).total_seconds()
                self._repo.update_scheduler_run(
                    run_id,
                    {
                        "status": "failed",
                        "completed_at": datetime.now(
                            UTC,
                        ),
                        "duration_secs": elapsed,
                        "error_message": str(exc)[:500],
                    },
                )
            else:
                elapsed = (
                    datetime.now(UTC) - start
                ).total_seconds()
                # Only set duration; executor sets
                # status to success/failed itself.
                self._repo.update_scheduler_run(
                    run_id,
                    {"duration_secs": elapsed},
                )

        def _run_and_cleanup():
            try:
                _run()
            finally:
                self._cancel_events.pop(run_id, None)

        with self._lock:
            future = self._pool.submit(_run_and_cleanup)
            self._futures[job_id] = future

        return run_id

    def cancel_run(self, run_id: str) -> bool:
        """Signal a running job to stop.

        Returns True if the cancel signal was sent.
        """
        event = self._cancel_events.get(run_id)
        if event is None:
            return False
        event.set()
        _logger.info(
            "Cancel signal sent for run %s", run_id,
        )
        return True

    # ----------------------------------------------------------
    # Query helpers (for API)
    # ----------------------------------------------------------

    def list_jobs(self) -> list[dict]:
        """Return all jobs with next_run info."""
        result = []
        runs = self._repo.get_scheduler_runs(days=7)
        for job in self._jobs.values():
            cron_dates = (
                job.get("cron_dates", "") or ""
            ).strip()
            cron_days = (
                job.get("cron_days", "") or ""
            ).split(",")
            cron_time = job.get("cron_time", "18:00")
            if cron_dates:
                nxt = _next_run_ist_dates(
                    cron_dates, cron_time,
                )
            else:
                nxt = _next_run_ist(
                    cron_days, cron_time,
                )

            # Find last run for this job
            job_runs = [
                r for r in runs
                if r.get("job_id") == job["job_id"]
            ]
            last_run = (
                job_runs[0] if job_runs else None
            )

            with self._lock:
                fut = self._futures.get(job["job_id"])
                is_running = (
                    fut is not None and not fut.done()
                )

            result.append({
                "job_id": job["job_id"],
                "name": job.get("name", ""),
                "job_type": job.get("job_type", ""),
                "cron_days": cron_days,
                "cron_dates": (
                    [
                        int(d.strip())
                        for d in cron_dates.split(",")
                        if d.strip().isdigit()
                    ]
                    if cron_dates else []
                ),
                "cron_time": cron_time,
                "scope": job.get("scope", "all"),
                "enabled": bool(job.get("enabled")),
                "next_run": (
                    nxt.strftime("%Y-%m-%d %H:%M IST")
                    if nxt and job.get("enabled")
                    else None
                ),
                "next_run_seconds": (
                    int(
                        (nxt - datetime.now(IST))
                        .total_seconds(),
                    )
                    if nxt and job.get("enabled")
                    else None
                ),
                "last_run_status": (
                    "running" if is_running
                    else (
                        last_run.get("status")
                        if last_run else None
                    )
                ),
                "last_run_time": (
                    str(last_run.get("started_at", ""))
                    if last_run else None
                ),
            })
        return result

    def get_stats(self) -> dict:
        """Return dashboard stat card data."""
        jobs = self.list_jobs()
        active = sum(
            1 for j in jobs if j.get("enabled")
        )

        # Next run
        next_runs = [
            j for j in jobs
            if j.get("next_run_seconds") is not None
            and j["next_run_seconds"] > 0
        ]
        next_runs.sort(
            key=lambda j: j["next_run_seconds"],
        )
        nearest = next_runs[0] if next_runs else None

        # Last run
        runs = self._repo.get_scheduler_runs(days=7)
        completed = [
            r for r in runs
            if r.get("status") in ("success", "failed")
        ]
        last = completed[0] if completed else None

        run_stats = (
            self._repo.get_scheduler_run_stats()
        )

        return {
            "active_jobs": active,
            "next_run_label": (
                nearest["next_run"] if nearest
                else None
            ),
            "next_run_seconds": (
                nearest["next_run_seconds"]
                if nearest else None
            ),
            "last_run_status": (
                last.get("status") if last else None
            ),
            "last_run_ago": (
                str(last.get("started_at", ""))
                if last else None
            ),
            "last_run_tickers": (
                last.get("tickers_done")
                if last else None
            ),
            **run_stats,
        }

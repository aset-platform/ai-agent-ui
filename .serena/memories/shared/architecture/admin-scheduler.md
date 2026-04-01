# Admin Scheduler Architecture

## Overview
Persistent job scheduler under Admin → Scheduler tab. Backend daemon thread + Iceberg persistence.

## Components
- `backend/jobs/scheduler_service.py` — SchedulerService class (schedule lib + ThreadPoolExecutor)
- `backend/jobs/executor.py` — @register_job decorator, data_refresh executor
- `backend/routes.py` — 7 REST endpoints (CRUD, trigger, runs, stats), all superuser_only
- `stocks/create_tables.py` — scheduled_jobs + scheduler_runs Iceberg schemas
- `stocks/repository.py` — Iceberg CRUD (get_scheduled_jobs, upsert, append_run, update_run, get_last_run_for_job)
- `frontend/components/admin/SchedulerTab.tsx` — StatCards, JobList, NewScheduleForm, RunTimeline
- `frontend/hooks/useSchedulerData.ts` — SWR hooks for jobs/runs/stats

## Scheduling Modes
- **Weekly**: cron_days (mon-sun) + cron_time → schedule.every().monday.at(time)
- **Monthly**: cron_dates (1-31) + cron_time → schedule.every().day.at(time) + day gate in _trigger_job

## Key Design Decisions
- schedule lib uses system local time (IST) — NO UTC conversion (bug was _ist_to_utc)
- trigger_type field: "scheduled" | "manual" | "catchup" — tracked in scheduler_runs Iceberg table
- Catch-up on startup: _catchup_missed_jobs() compares last run vs last scheduled window
- cron_days and cron_dates are mutually exclusive (frontend enforces via scheduleType toggle)
- Schema evolution: auto-add new columns (trigger_type, cron_dates) on first write

## Config
- scheduler_enabled (bool, default true)
- scheduler_max_workers (int, default 3)
- scheduler_catchup_enabled (bool, default true)

## IST Times
- All cron_time values stored and displayed in IST (Asia/Kolkata)
- Iceberg timestamps stored as UTC tz-naive
- _next_run_ist / _last_scheduled_window compute in IST

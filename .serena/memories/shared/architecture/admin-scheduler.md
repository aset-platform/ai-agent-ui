# Admin Scheduler — Extensible Job Scheduling

## Overview
Superuser-only scheduler system under Admin → Scheduler tab for recurring background jobs.
First job type: "Data Refresh" (refreshes all tickers via `run_full_refresh()`).

## Backend Architecture
- **Job definitions** persisted in Iceberg table `stocks.scheduled_jobs`
- **Run history** persisted in `stocks.scheduler_runs`
- **Executor registry** in `backend/jobs/executor.py` — extensible via `@register_job("type")` decorator
- **SchedulerService** in `backend/jobs/scheduler_service.py`:
  - Loads jobs from Iceberg on startup
  - Registers with `schedule` library (daemon thread, checks every 30s)
  - IST → UTC time conversion via `ZoneInfo("Asia/Kolkata")`
  - `ThreadPoolExecutor(max_workers=3)` for concurrent runs
  - Stale run cleanup on restart (marks orphaned "running" as "failed")
- **7 REST endpoints** at `/v1/admin/scheduler/*` (all `Depends(superuser_only)`)
- **Config**: `scheduler_enabled`, `scheduler_max_workers` in `backend/config.py`

## Frontend
- **SchedulerTab** component in `frontend/components/admin/SchedulerTab.tsx`
- Design B "Dashboard-First": stat cards, job list with toggles, new schedule form, run timeline
- **SWR hooks** in `frontend/hooks/useSchedulerData.ts` (auto-refresh 30s)
- Wired as 6th tab in Admin page

## Adding a New Job Type
1. Backend: Add function in `executor.py` with `@register_job("new_type")`
2. Frontend: Add card in NewScheduleForm job type selector
3. No schema changes needed — `job_type` is a string field

## Critical Gotchas
- Iceberg TimestampType is **tz-naive** — always `v.replace(tzinfo=None)` before writing
- Sanitize NaN/NaT from pandas before JSON serialization (`v != v` check or `pd.isna()`)
- `schedule` lib uses UTC internally — convert IST times with `_ist_to_utc()`
- Backend must restart to pick up code changes (no `--reload` flag in production)

## Jira: ASETPLTFRM-205 (feature), ASETPLTFRM-206 (bug fixes)

# Scheduler & Pipeline Orchestration

The scheduler runs batch jobs on cron schedules and chains them
into multi-step pipelines. All managed via **Admin > Scheduler**.

---

## Architecture

```
SchedulerService (daemon thread, 30s tick)
  ├── Standalone Jobs (single executor)
  │   └── trigger_now() → ThreadPoolExecutor → executor_fn()
  └── Pipelines (chained steps)
      └── trigger_pipeline_now() → PipelineExecutor
          └── _execute_chain() → step 1 → step 2 → ... → step N
```

**Key files:**

| File | Purpose |
|------|---------|
| `backend/jobs/scheduler_service.py` | Job/pipeline scheduling, cron triggers, catchup |
| `backend/jobs/pipeline_executor.py` | Sequential step execution with skip-on-failure |
| `backend/jobs/executor.py` | Job type registry + executor functions |
| `backend/jobs/batch_refresh.py` | Parallel yfinance fetch + bulk Iceberg write |
| `backend/db/pg_stocks.py` | PG CRUD for scheduler_runs, scheduled_jobs |

---

## Job Types

| Type | Executor | What it does | Typical duration |
|------|----------|-------------|-----------------|
| `data_refresh` | `execute_data_refresh` | Batch yfinance OHLCV + company info + dividends + quarterly | 5 min (748 tickers) |
| `compute_analytics` | `execute_compute_analytics` | Technical indicators + analysis summary | 45s |
| `run_sentiment` | `execute_run_sentiment` | LLM headline scoring via Groq | 3.5 min |
| `run_forecasts` | `execute_run_forecasts` | Prophet price forecasts + CV accuracy | 8 min (weekly) / 34 min (monthly force) |
| `run_piotroski` | `execute_run_piotroski` | Piotroski F-Score from quarterly results | 2s |

All executors accept: `(scope, run_id, repo, cancel_event=None, force=False)`

---

## Freshness Gates

Each executor skips tickers that are already fresh. The `force=True`
parameter bypasses all freshness checks.

| Job Type | Freshness Check | Skip if... | Refresh Cadence |
|----------|----------------|-----------|-----------------|
| `data_refresh` | OHLCV latest date | `latest >= yesterday` | Daily |
| `compute_analytics` | analysis_summary date | `computed today` | Daily |
| `run_sentiment` | sentiment scored_at | `scored today` (hot/learning tickers always re-score) | Daily |
| `run_forecasts` | forecast_runs run_date | `run_date < 7 days ago` | Weekly |
| `run_forecasts` (CV) | forecast_runs mae/rmse | `accuracy < 30 days old` | Monthly (auto via 30-day TTL) |
| `run_piotroski` | No freshness gate | Always recomputes | Monthly |

### Forecast CV Reuse Cycle

```
Week 1: CV computes (no cached accuracy or cache >30 days)
Week 2: CV reused (cache age ~7 days) → ~8 min
Week 3: CV reused (cache age ~14 days) → ~8 min
Week 4: CV reused (cache age ~21 days) → ~8 min
Week 5: CV recomputes (cache >30 days) → ~34 min
```

---

## Pipelines

Pipelines chain multiple job types in order. If a step fails,
subsequent steps are skipped (marked "skipped" with reason).

### Default Pipelines

**India Daily Pipeline** (weekdays 08:00 IST):

| Step | Job Type | Duration |
|------|----------|----------|
| 1 | Data Refresh | ~5 min |
| 2 | Compute Analytics | ~45s |
| 3 | Sentiment Scoring | ~3.5 min |
| 4 | Piotroski F-Score | ~2s |

**USA Daily Pipeline** (weekdays 08:00 IST):

| Step | Job Type | Duration |
|------|----------|----------|
| 1 | Data Refresh | ~30s |
| 2 | Compute Analytics | ~30s |
| 3 | Sentiment Scoring | ~40s |
| 4 | Piotroski F-Score | ~20s |

### Pipeline CRUD

Pipelines can be created, edited, and deleted via the Admin UI
(**Admin > Scheduler > New Pipeline** button) or API:

- `POST /v1/admin/scheduler/pipelines` — create
- `PATCH /v1/admin/scheduler/pipelines/{id}` — update
- `DELETE /v1/admin/scheduler/pipelines/{id}` — delete
- `POST /v1/admin/scheduler/pipelines/{id}/trigger` — run now
  (accepts `{ "force": true }` in body)

### Resume from Step

If a pipeline fails at step N, it can be resumed from that step
via the DAG UI ("Run from here" button on the failed step).

---

## Standalone Scheduled Jobs

Jobs run independently on a cron schedule. Managed via
**Admin > Scheduler > Scheduled Jobs** section.

### Current Jobs

| Job | Schedule | Scope | Force |
|-----|----------|-------|-------|
| Weekly Forecast - India | Sat, Sun 08:00 IST | india | false |
| Weekly Forecast - USA | Sat 10:00 IST | us | false |
| Daily Market Close - India | Daily 21:00 IST | india | false (paused) |
| Daily Compute Analytics - India | Daily 21:30 IST | india | false (paused) |
| Daily Sentiment Analytics - India | Daily 22:00 IST | india | false (paused) |

### Force Option

The `force` field on scheduled_jobs controls whether the cron
trigger bypasses freshness gates. Use for:

- Monthly CV refresh: create a forecast job with `force=true`
  and `cron_dates=1` (1st of month)
- Re-ingestion after data cleanup

### Catchup Logic

On startup, the scheduler checks if any enabled job missed its
last scheduled window (within 7 days). If so, it triggers a
catchup run automatically.

---

## Performance Optimizations

### Batch I/O (before parallel loop)

| Operation | Before | After |
|-----------|--------|-------|
| OHLCV load (748 tickers) | 167s (individual reads) | 0.87s (single DuckDB query) |
| Freshness check | 329s (individual Iceberg reads) | 0.44s (single DuckDB query → dict) |
| Regressor load | 1.6s per ticker | 0.05s per ticker (scope-keyed cache) |

### Bulk Writes (after parallel loop)

| Operation | Before | After |
|-----------|--------|-------|
| Forecast writes | 11.5 min (2,244 Iceberg commits) | ~2s (2 commits) |
| Progress updates | 9s per call (Iceberg overwrite) | 14ms per call (PG row update) |

### Worker Configuration

```python
max_workers = max(os.cpu_count() // 2, 2)  # 5 on 10-core
```

Prophet CV uses `parallel=None` (sequential within each thread)
to avoid nested process spawning and CPU contention.

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/admin/scheduler/jobs` | GET | List all scheduled jobs |
| `/admin/scheduler/jobs` | POST | Create job |
| `/admin/scheduler/jobs/{id}` | PATCH | Update job |
| `/admin/scheduler/jobs/{id}` | DELETE | Delete job |
| `/admin/scheduler/jobs/{id}/trigger` | POST | Run now (`{ force: true }`) |
| `/admin/scheduler/runs` | GET | Run history (filters: days, job_type, status) |
| `/admin/scheduler/runs/{id}/cancel` | POST | Cancel running job |
| `/admin/scheduler/stats` | GET | Dashboard stat cards |
| `/admin/scheduler/pipelines` | GET | List pipelines |
| `/admin/scheduler/pipelines` | POST | Create pipeline |
| `/admin/scheduler/pipelines/{id}` | PATCH | Update pipeline |
| `/admin/scheduler/pipelines/{id}` | DELETE | Delete pipeline |
| `/admin/scheduler/pipelines/{id}/trigger` | POST | Trigger pipeline (`{ force: true }`) |

---

## Database

### PostgreSQL (mutable state)

- `scheduled_jobs` — job definitions (name, type, cron, scope, force, enabled)
- `scheduler_runs` — execution records (status, progress, duration, errors)
- `pipelines` — pipeline definitions
- `pipeline_steps` — ordered steps within pipelines

### Run Status Flow

```
running → success | failed | cancelled | skipped
```

- **success**: completed with <5% ticker error rate
- **failed**: >5% error rate or uncaught exception
- **cancelled**: user pressed Stop
- **skipped**: prior pipeline step failed

---

## UI Features

### Scheduler Tab (Admin > Scheduler)

- **Stat cards**: Active jobs, next run countdown, last run status, runs today
- **Pipeline DAG**: Visual step-by-step with status, duration, "Run All" / "Force Run All"
- **Scheduled Jobs**: Toggle enable/disable, "Run Now" / "Force Run" split button
- **New Schedule Form**: Job type, schedule presets, time picker, scope, force toggle
- **New Pipeline Form**: Name, scope, schedule, ordered step editor
- **Run History**: Paginated, filterable (status, job type, days), pipeline grouping
- **Auto-refresh**: 15s interval on all sections via SWR `refreshInterval`
- **URL persistence**: `?tab=scheduler` preserved across page refresh

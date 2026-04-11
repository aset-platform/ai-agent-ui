# Pipeline Chaining & DAG System (Sprint 6)

## Architecture
- **Pipeline** + **PipelineStep** ORM models in PostgreSQL (`backend/db/models/pipeline.py`)
- **PipelineExecutor** (`backend/jobs/pipeline_executor.py`): sequential chain execution in one ThreadPoolExecutor slot
- Steps execute sequentially; on failure, remaining steps marked "skipped"
- Resume-from-step via `POST /admin/scheduler/pipelines/{id}/resume` with `{"from_step": N}`
- `pipeline_run_id` column on Iceberg `scheduler_runs` groups all runs in a chain

## Current Pipelines (both weekdays 08:00 IST)
- **India Daily Pipeline**: data_refresh → compute_analytics → run_sentiment → run_piotroski
- **USA Daily Pipeline**: same 4 steps, scope="us"
- **Independent**: Weekly Forecast India (Sat 08:00), Weekly Forecast USA (Sat 10:00)

## Pipeline IDs
- India: `69581e01-baf7-4103-af55-f34b4098c2ba`
- USA: `a6a0e36e-39c1-4a5e-b4fd-4469d7bd8c64`

## Frontend
- `PipelineDAG.tsx`: interactive step nodes with arrows, "Run from here" context menu (fixed positioning)
- `SchedulerTab.tsx`: pipeline groups in Run History (collapsible cards with step connectors)
- Run History: filters (status, job_type, days), pagination, metrics row

## Key design decisions
- Separate `pipelines` + `pipeline_steps` tables (not `depends_on` on ScheduledJob)
- Chain occupies ONE ThreadPoolExecutor slot (sequential within thread)
- 08:00 IST schedule avoids stale pre-market yfinance data

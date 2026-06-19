# Budget reconciliation (and other algo jobs) stuck as "Running" in scheduler page

## Symptom
Scheduler page shows algo jobs (`algo_reconciliation`, `algo_kite_instruments_refresh`,
etc.) as `Running` indefinitely after the job completes successfully. The job did
run to completion; only the status update was missing.

## Root cause
The 5 algo job wrappers in `executor.py` called `asyncio.run(...)` to bridge the
sync scheduler into async code — but never called `repo.update_scheduler_run` with
`status="success"`. The `else` branch in `scheduler_service.py` only updated
`duration_secs`, not the status field.

## Fix
Added `_algo_job_success(repo, run_id)` helper in `executor.py`:
```python
def _algo_job_success(repo, run_id: str | None) -> None:
    if repo and run_id:
        repo.update_scheduler_run(run_id, {
            "status": "success",
            "completed_at": datetime.now(timezone.utc),
        })
```
Called immediately after `asyncio.run(...)` in all 5 wrappers:
`_job_algo_reconciliation`, `_job_algo_kite_instruments_refresh`,
`_job_algo_risk_state_reset`, `_job_algo_live_caps_daily_reset`,
`_job_algo_ws_tick_count_reset`.

## Related
Also fixed in same session: `disposable_pg_session()` used instead of
`_session_factory()` inside `budget_reconciliation.py` to prevent
"Future attached to a different loop" errors (asyncio.run creates new loop,
incompatible with uvicorn pool connections).

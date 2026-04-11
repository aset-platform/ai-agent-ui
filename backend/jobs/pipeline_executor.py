"""Pipeline chain executor — sequential job chaining."""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone

from jobs.executor import JOB_EXECUTORS

_logger = logging.getLogger(__name__)
UTC = timezone.utc


class PipelineExecutor:
    """Execute pipeline steps sequentially.

    Each step runs in the same thread — the pipeline
    occupies one ThreadPoolExecutor slot for its full
    duration.
    """

    def __init__(self, repo) -> None:
        self._repo = repo

    def trigger_pipeline(
        self,
        pipeline: dict,
        trigger_type: str = "manual",
        cancel_event: threading.Event | None = None,
        force: bool = False,
    ) -> str:
        """Run the full pipeline. Returns pipeline_run_id."""
        return self._execute_chain(
            pipeline,
            pipeline_run_id=str(uuid.uuid4()),
            trigger_type=trigger_type,
            start_step=1,
            cancel_event=cancel_event,
            force=force,
        )

    def resume_pipeline(
        self,
        pipeline: dict,
        from_step: int,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """Resume from a specific step."""
        return self._execute_chain(
            pipeline,
            pipeline_run_id=str(uuid.uuid4()),
            trigger_type="manual",
            start_step=from_step,
            cancel_event=cancel_event,
        )

    def _execute_chain(
        self,
        pipeline: dict,
        pipeline_run_id: str,
        trigger_type: str,
        start_step: int,
        cancel_event: threading.Event | None = None,
        force: bool = False,
    ) -> str:
        """Run steps sequentially from start_step."""
        steps = sorted(
            pipeline.get("steps", []),
            key=lambda s: s["step_order"],
        )
        scope = pipeline.get("scope", "all")
        pipeline_id = pipeline["pipeline_id"]

        _logger.info(
            "Pipeline %s start: steps=%d from=%d "
            "run=%s",
            pipeline.get("name"),
            len(steps),
            start_step,
            pipeline_run_id,
        )

        failed = False
        for step in steps:
            order = step["step_order"]
            if order < start_step:
                continue

            if cancel_event and cancel_event.is_set():
                self._mark_skipped(
                    pipeline_id,
                    pipeline_run_id,
                    step,
                    scope,
                    "Pipeline cancelled",
                )
                failed = True
                continue

            if failed:
                self._mark_skipped(
                    pipeline_id,
                    pipeline_run_id,
                    step,
                    scope,
                    "Skipped: prior step failed",
                )
                continue

            ok = self._run_step(
                pipeline_id,
                pipeline_run_id,
                step,
                scope,
                trigger_type,
                cancel_event,
                force=force,
            )
            if not ok:
                failed = True

        return pipeline_run_id

    def _run_step(
        self,
        pipeline_id: str,
        pipeline_run_id: str,
        step: dict,
        scope: str,
        trigger_type: str,
        cancel_event: threading.Event | None,
        force: bool = False,
    ) -> bool:
        """Execute one step. Returns True on success."""
        job_type = step["job_type"]
        executor_fn = JOB_EXECUTORS.get(job_type)
        if not executor_fn:
            _logger.warning(
                "No executor for %s", job_type,
            )
            return False

        run_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        run = {
            "run_id": run_id,
            "job_id": pipeline_id,
            "job_name": step["job_name"],
            "job_type": job_type,
            "scope": scope,
            "status": "running",
            "started_at": now,
            "completed_at": None,
            "duration_secs": None,
            "tickers_total": 0,
            "tickers_done": 0,
            "error_message": None,
            "trigger_type": trigger_type,
            "pipeline_run_id": pipeline_run_id,
        }
        self._repo.append_scheduler_run(run)

        _logger.info(
            "Pipeline step %d/%s start: run=%s",
            step["step_order"],
            step["job_name"],
            run_id,
        )

        start = datetime.now(UTC)
        try:
            executor_fn(
                scope, run_id, self._repo,
                cancel_event=cancel_event,
                force=force,
            )
        except Exception as exc:
            elapsed = (
                datetime.now(UTC) - start
            ).total_seconds()
            _logger.warning(
                "Pipeline step %s failed: %s",
                step["job_name"],
                exc,
            )
            self._repo.update_scheduler_run(
                run_id,
                {
                    "status": "failed",
                    "completed_at": datetime.now(UTC),
                    "duration_secs": elapsed,
                    "error_message": str(exc)[:500],
                },
            )
            return False

        elapsed = (
            datetime.now(UTC) - start
        ).total_seconds()

        # Check if the executor itself marked it
        # as failed (e.g. >5% ticker error rate)
        last = self._repo.get_scheduler_runs(
            days=1,
            limit=1,
            pipeline_run_id=pipeline_run_id,
        )
        step_run = next(
            (r for r in last if r.get("run_id") == run_id),
            None,
        )
        status = (
            step_run.get("status", "success")
            if step_run else "success"
        )
        if status == "running":
            status = "success"

        self._repo.update_scheduler_run(
            run_id,
            {
                "status": status,
                "completed_at": datetime.now(UTC),
                "duration_secs": elapsed,
            },
        )

        _logger.info(
            "Pipeline step %d/%s done: %s (%.1fs)",
            step["step_order"],
            step["job_name"],
            status,
            elapsed,
        )
        return status == "success"

    def _mark_skipped(
        self,
        pipeline_id: str,
        pipeline_run_id: str,
        step: dict,
        scope: str,
        reason: str,
    ) -> None:
        """Create a 'skipped' run record."""
        now = datetime.now(UTC)
        run = {
            "run_id": str(uuid.uuid4()),
            "job_id": pipeline_id,
            "job_name": step["job_name"],
            "job_type": step["job_type"],
            "scope": scope,
            "status": "skipped",
            "started_at": now,
            "completed_at": now,
            "duration_secs": 0.0,
            "tickers_total": 0,
            "tickers_done": 0,
            "error_message": reason,
            "trigger_type": "pipeline",
            "pipeline_run_id": pipeline_run_id,
        }
        self._repo.append_scheduler_run(run)

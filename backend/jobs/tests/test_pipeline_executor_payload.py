"""Tests for ASETPLTFRM-418 ``PipelineExecutor`` payload
threading.

``_run_step`` introspects the executor function's signature
and passes ``payload=...`` only when the wrapper opts in
(via a named ``payload`` parameter or ``**kwargs``).
This keeps every existing ``@register_job`` wrapper
backwards-compatible while the new
``execute_iceberg_maintenance`` (which now declares
``payload: dict | None = None``) receives the per-step
configuration from the pipeline_steps row.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.jobs.pipeline_executor import PipelineExecutor

# ---------------------------------------------------------
# Fixtures
# ---------------------------------------------------------


@pytest.fixture
def repo():
    r = MagicMock()
    r.append_scheduler_run = MagicMock()
    r.update_scheduler_run = MagicMock()
    r.get_scheduler_runs = MagicMock(return_value=[])
    return r


@pytest.fixture
def step_with_payload():
    return {
        "step_order": 1,
        "job_type": "iceberg_maintenance",
        "job_name": "Compact",
        "payload": {"tables": ["stocks.ohlcv"]},
    }


@pytest.fixture
def step_without_payload():
    return {
        "step_order": 1,
        "job_type": "legacy_job",
        "job_name": "Legacy",
        "payload": {},
    }


# ---------------------------------------------------------
# Signature-based dispatch
# ---------------------------------------------------------


def test_executor_passes_payload_when_wrapper_accepts_it(
    repo,
    step_with_payload,
):
    """Wrapper signature includes ``payload`` → executor
    receives the step's payload dict."""
    calls: list[dict] = []

    def wrapper_with_payload(
        scope,
        run_id,
        _repo,
        *,
        cancel_event=None,
        force=False,
        payload=None,
    ):
        calls.append(
            {
                "scope": scope,
                "payload": payload,
                "force": force,
            }
        )

    with patch.dict(
        "backend.jobs.pipeline_executor.JOB_EXECUTORS",
        {"iceberg_maintenance": wrapper_with_payload},
        clear=False,
    ):
        ex = PipelineExecutor(repo=repo)
        ok = ex._run_step(
            pipeline_id="p",
            pipeline_run_id="pr",
            step=step_with_payload,
            scope="india",
            trigger_type="manual",
            cancel_event=None,
            force=False,
        )

    assert ok is True
    assert calls == [
        {
            "scope": "india",
            "payload": {"tables": ["stocks.ohlcv"]},
            "force": False,
        }
    ]


def test_executor_skips_payload_when_wrapper_doesnt_accept_it(
    repo,
    step_with_payload,
):
    """Legacy wrapper (no ``payload`` kwarg, no ``**kwargs``)
    must still be callable — ``payload`` is silently dropped."""
    calls: list[dict] = []

    def legacy_wrapper(
        scope,
        run_id,
        _repo,
        cancel_event=None,
        force=False,
    ):
        # No ``payload`` kwarg + no ``**kwargs``. Passing
        # ``payload=...`` here would raise TypeError.
        calls.append({"scope": scope, "force": force})

    with patch.dict(
        "backend.jobs.pipeline_executor.JOB_EXECUTORS",
        {"iceberg_maintenance": legacy_wrapper},
        clear=False,
    ):
        ex = PipelineExecutor(repo=repo)
        ok = ex._run_step(
            pipeline_id="p",
            pipeline_run_id="pr",
            step=step_with_payload,
            scope="india",
            trigger_type="manual",
            cancel_event=None,
            force=False,
        )

    assert ok is True
    assert calls == [{"scope": "india", "force": False}]


def test_executor_passes_payload_to_var_keyword_wrappers(
    repo,
    step_with_payload,
):
    """Wrappers that accept ``**kwargs`` should also receive
    the payload — they explicitly opted in to forward
    arbitrary kwargs."""
    captured: dict = {}

    def kw_wrapper(scope, run_id, _repo, **kwargs):
        captured.update(kwargs)

    with patch.dict(
        "backend.jobs.pipeline_executor.JOB_EXECUTORS",
        {"iceberg_maintenance": kw_wrapper},
        clear=False,
    ):
        ex = PipelineExecutor(repo=repo)
        ex._run_step(
            pipeline_id="p",
            pipeline_run_id="pr",
            step=step_with_payload,
            scope="india",
            trigger_type="manual",
            cancel_event=None,
            force=False,
        )

    assert captured.get("payload") == {
        "tables": ["stocks.ohlcv"],
    }


def test_executor_loads_payload_from_pipeline_steps_row(repo):
    """End-to-end: ``trigger_pipeline`` passes the step dict
    (including ``payload``) into ``_run_step`` and the
    wrapper sees it."""
    payload = {"tables": ["stocks.sentiment_scores"]}
    received: list[dict] = []

    def wrapper(
        scope,
        run_id,
        _repo,
        *,
        cancel_event=None,
        force=False,
        payload=None,
    ):
        received.append(payload or {})

    with patch.dict(
        "backend.jobs.pipeline_executor.JOB_EXECUTORS",
        {"iceberg_maintenance": wrapper},
        clear=False,
    ):
        ex = PipelineExecutor(repo=repo)
        ex.trigger_pipeline(
            pipeline={
                "pipeline_id": "pid",
                "name": "Test",
                "scope": "india",
                "steps": [
                    {
                        "step_order": 1,
                        "job_type": "iceberg_maintenance",
                        "job_name": "Compact",
                        "payload": payload,
                    },
                ],
            },
        )

    assert received == [payload]


def test_executor_passes_empty_payload_when_missing(repo):
    """Step row with no ``payload`` key → wrapper receives
    an empty dict, not ``None``-related crash."""
    received: list[dict] = []

    def wrapper(
        scope,
        run_id,
        _repo,
        *,
        cancel_event=None,
        force=False,
        payload=None,
    ):
        received.append(payload)

    with patch.dict(
        "backend.jobs.pipeline_executor.JOB_EXECUTORS",
        {"iceberg_maintenance": wrapper},
        clear=False,
    ):
        ex = PipelineExecutor(repo=repo)
        ex._run_step(
            pipeline_id="p",
            pipeline_run_id="pr",
            step={
                "step_order": 1,
                "job_type": "iceberg_maintenance",
                "job_name": "Compact",
                # no payload key at all
            },
            scope="india",
            trigger_type="manual",
            cancel_event=None,
            force=False,
        )

    assert received == [{}]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

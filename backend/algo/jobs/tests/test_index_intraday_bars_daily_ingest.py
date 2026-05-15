"""Tests for the ``index_intraday_bars_daily_ingest`` scheduler
job (ASETPLTFRM-402 / FE-6).

Covers the orchestration shape rather than the writer (which is
covered in ``test_index_intraday_backfill.py``). All Iceberg /
Kite / DB calls are mocked.

Asserted behaviours:
- Default window is [yesterday, today] IST when payload omits
  ``period_start`` / ``period_end``.
- Keeper resolution uses ``disposable_pg_session`` (NullPool
  bridge for the scheduler's own asyncio loop) — never the
  cached factory.
- No-credentials user → graceful early exit
  (``status='skipped_no_credentials'``); no Kite call.
- Per-interval failures are aggregated into the return payload.
- ``force=True`` payload knob is plumbed through to the result
  shape.
- ``@register_job`` wiring in ``backend.jobs.executor`` is
  pipeline-step compatible.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.jobs.index_intraday_bars_daily_ingest import (
    _default_window,
    run_index_intraday_bars_daily_ingest_job,
)


def _stats(done=10, failed=0, bars=300, failures=None):
    from backend.algo.backtest.intraday_backfill import BackfillStats

    return BackfillStats(
        tickers_done=done,
        tickers_failed=failed,
        bars_written=bars,
        wall_clock_s=0.5,
        failures=failures or [],
    )


@pytest.fixture
def fake_session_factory():
    """Stand-in for ``disposable_pg_session()`` — itself an async
    context manager."""
    sess = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=sess)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=cm)
    return factory, sess


def test_default_window_is_yesterday_to_today():
    """Default window straddles today + yesterday IST so Kite's
    intra-session republish of yesterday's bars is absorbed."""
    today_ist = (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()
    start, end = _default_window()
    assert end == today_ist
    assert start == today_ist - timedelta(days=1)


@pytest.mark.asyncio
async def test_resolves_keeper_user_from_pg(fake_session_factory):
    """Keeper user resolution MUST go through
    ``disposable_pg_session`` (per-loop NullPool engine), not the
    cached factory — re-uses the cached factory crashes with
    "Future attached to a different loop" inside the scheduler's
    own ``asyncio.run`` (CLAUDE.md §5.1)."""
    factory, _ = fake_session_factory
    uid = uuid4()
    with (
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ) as patched_session,
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "_resolve_keeper_user",
            new=AsyncMock(
                return_value={
                    "user_id": uid,
                    "creds": {
                        "api_key": "k",
                        "access_token": "tok",
                        "access_token_expired": False,
                    },
                }
            ),
        ) as resolver,
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "KiteClient",
        ) as MockKite,
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "backfill_index_window",
            new=AsyncMock(return_value=_stats()),
        ) as mock_bf,
    ):
        result = await run_index_intraday_bars_daily_ingest_job(None)

    # disposable_pg_session called once (single async session
    # bracket around the entire run).
    patched_session.assert_called_once()
    resolver.assert_awaited_once()
    MockKite.assert_called_once_with(
        api_key="k",
        access_token="tok",
        dry_run=False,
    )
    assert result["status"] == "ok"
    # Default = 1 cadence (15m) only.
    assert mock_bf.await_count == 1


@pytest.mark.asyncio
async def test_skipped_when_no_user_has_credentials(
    fake_session_factory,
):
    factory, _ = fake_session_factory
    with (
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "_resolve_keeper_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "KiteClient",
        ) as MockKite,
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "backfill_index_window",
            new=AsyncMock(),
        ) as mock_bf,
    ):
        result = await run_index_intraday_bars_daily_ingest_job(None)

    assert result["status"] == "skipped_no_credentials"
    MockKite.assert_not_called()
    mock_bf.assert_not_awaited()


@pytest.mark.asyncio
async def test_aggregates_failures_in_payload(fake_session_factory):
    """Per-interval BackfillStats failures roll up into a capped
    10-element ``sample_failures`` in the return payload."""
    factory, _ = fake_session_factory
    uid = uuid4()
    failures = [(f"NIFTY BAD{i}", "fetch:boom") for i in range(2)]
    with (
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "_resolve_keeper_user",
            new=AsyncMock(
                return_value={
                    "user_id": uid,
                    "creds": {
                        "api_key": "k",
                        "access_token": "tok",
                        "access_token_expired": False,
                    },
                }
            ),
        ),
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "KiteClient",
        ),
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "backfill_index_window",
            new=AsyncMock(
                return_value=_stats(
                    done=8,
                    failed=2,
                    bars=200,
                    failures=failures,
                )
            ),
        ),
    ):
        result = await run_index_intraday_bars_daily_ingest_job(None)

    assert result["status"] == "ok"
    assert result["tickers_failed"] == 2
    assert result["bars_written"] == 200
    assert len(result["sample_failures"]) == 2
    assert result["sample_failures"][0][0] == "NIFTY BAD0"


@pytest.mark.asyncio
async def test_force_payload_bypasses_default_window(
    fake_session_factory,
):
    """Operator-supplied window + force flag survive into the
    return payload; intervals override the default."""
    factory, _ = fake_session_factory
    uid = uuid4()
    with (
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "_resolve_keeper_user",
            new=AsyncMock(
                return_value={
                    "user_id": uid,
                    "creds": {
                        "api_key": "k",
                        "access_token": "tok",
                        "access_token_expired": False,
                    },
                }
            ),
        ),
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "KiteClient",
        ),
        patch(
            "backend.algo.jobs.index_intraday_bars_daily_ingest."
            "backfill_index_window",
            new=AsyncMock(return_value=_stats()),
        ) as mock_bf,
    ):
        result = await run_index_intraday_bars_daily_ingest_job(
            {
                "force": True,
                "intervals": [900, 300],
                "period_start": "2026-05-01",
                "period_end": "2026-05-13",
                "index_symbols": ["NIFTY 50", "NIFTY BANK"],
            }
        )

    assert result["forced"] is True
    assert result["start"] == "2026-05-01"
    assert result["end"] == "2026-05-13"
    assert result["intervals"] == [900, 300]
    # 2 intervals → 2 backfill calls.
    assert mock_bf.await_count == 2
    # Universe override survived to the backfill call.
    for call in mock_bf.await_args_list:
        assert call.kwargs["index_symbols"] == ["NIFTY 50", "NIFTY BANK"]
        assert call.kwargs["period_start"] == date(2026, 5, 1)
        assert call.kwargs["period_end"] == date(2026, 5, 13)


def test_register_job_dispatch_wiring():
    """``index_intraday_bars_daily_ingest`` must be registered in
    JOB_EXECUTORS — without this the pipeline executor cannot
    chain it."""
    from backend.jobs.executor import JOB_EXECUTORS

    assert "index_intraday_bars_daily_ingest" in JOB_EXECUTORS


def test_register_job_wrapper_is_pipeline_compatible():
    """``PipelineExecutor._run_step`` invokes the registered
    handler with ``(scope, run_id, repo, cancel_event=, force=)``.
    Our wrapper must be sync + accept that signature."""
    import inspect

    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["index_intraday_bars_daily_ingest"]
    assert not inspect.iscoroutinefunction(fn)
    params = inspect.signature(fn).parameters
    for required in ("scope", "run_id", "repo", "cancel_event", "force"):
        assert required in params, (
            f"wrapper missing pipeline-step param: {required}"
        )


def test_register_job_wrapper_runs_async_job_via_asyncio():
    """The sync wrapper must drive the async job to completion,
    not just hand back a coroutine."""
    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["index_intraday_bars_daily_ingest"]

    async def _fake_async_job(payload):
        return {"status": "ok", "payload_seen": payload}

    with patch(
        "backend.algo.jobs.index_intraday_bars_daily_ingest."
        "run_index_intraday_bars_daily_ingest_job",
        new=_fake_async_job,
    ):
        result = fn(
            scope="india",
            run_id="abc",
            repo=None,
            cancel_event=None,
            force=False,
            payload={"intervals": [900]},
        )
    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert result["payload_seen"] == {"intervals": [900]}

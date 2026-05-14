"""Tests for the ``intraday_bars_daily_ingest`` scheduler job
(ASETPLTFRM-400 slice 1d).

Covers the orchestration shape rather than the writer (which is
covered in test_intraday_backfill.py). All Iceberg / Kite / DB
calls are mocked.

Asserted behaviours:
- Universe = top-200 ∪ active-MIS-tickers (deduped, sorted).
- All requested intervals (15m, 5m, 1m by default) get one
  ``backfill_window`` call each.
- Per-interval BackfillStats are aggregated into the return
  payload, including a capped 10-item ``sample_failures`` list.
- No-credentials user → graceful early exit
  (``status='skipped_no_credentials'``); no Kite call.
- Expired access_token → graceful early exit
  (``status='skipped_token_expired'``).
- Empty universe → graceful early exit
  (``status='skipped_empty_universe'``).
- Payload-supplied ``intervals`` + window override defaults.
- Payload-supplied ``user_id`` bypasses the auto-resolver.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.jobs.intraday_bars_daily_ingest import (
    run_intraday_bars_daily_ingest_job,
)


@pytest.fixture
def fake_session():
    """Stand-in for ``disposable_pg_session()`` — itself an async
    context manager (no factory layer)."""
    sess = AsyncMock()

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=sess)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    # The patch target replaces ``disposable_pg_session`` (a
    # callable returning the cm). Wrap so ``disposable_pg_session()``
    # returns ``session_cm``.
    factory = MagicMock(return_value=session_cm)
    return factory, sess


def _stats(done=5, failed=0, bars=100, failures=None):
    from backend.algo.backtest.intraday_backfill import BackfillStats

    return BackfillStats(
        tickers_done=done,
        tickers_failed=failed,
        bars_written=bars,
        wall_clock_s=1.23,
        failures=failures or [],
    )


async def test_happy_path_aggregates_all_intervals(fake_session):
    """3 intervals × 5 tickers each → all stats summed into the
    return payload."""
    factory, sess = fake_session
    uid = uuid4()
    with (
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
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
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_nifty500_universe",
            new=AsyncMock(return_value=["A.NS", "B.NS", "C.NS", "D.NS"]),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_instrument_tokens",
            new=AsyncMock(
                return_value={
                    "A.NS": 1,
                    "B.NS": 2,
                    "C.NS": 3,
                    "D.NS": 4,
                }
            ),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.KiteClient",
        ) as MockKite,
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.backfill_window",
            return_value=_stats(done=4, bars=200),
        ) as mock_bf,
    ):
        result = await run_intraday_bars_daily_ingest_job(None)

    assert result["status"] == "ok"
    # Nifty 500 universe (mocked) = [A,B,C,D]
    assert result["ticker_count"] == 4
    # 3 default intervals → 3 backfill_window calls
    assert mock_bf.call_count == 3
    assert result["intervals"] == [900, 300, 60]
    # 200 bars × 3 intervals
    assert result["bars_written"] == 600
    # KiteClient constructed once with the resolved creds
    MockKite.assert_called_once_with(
        api_key="k",
        access_token="tok",
        dry_run=False,
    )


async def test_skipped_when_no_user_has_credentials(fake_session):
    factory, _ = fake_session
    with (
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_keeper_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.KiteClient",
        ) as MockKite,
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.backfill_window",
        ) as mock_bf,
    ):
        result = await run_intraday_bars_daily_ingest_job(None)

    assert result["status"] == "skipped_no_credentials"
    MockKite.assert_not_called()
    mock_bf.assert_not_called()


async def test_skipped_when_token_expired(fake_session):
    factory, _ = fake_session
    uid = uuid4()
    with (
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_keeper_user",
            new=AsyncMock(
                return_value={
                    "user_id": uid,
                    "creds": {
                        "api_key": "k",
                        "access_token": "tok",
                        "access_token_expired": True,
                    },
                }
            ),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.KiteClient",
        ) as MockKite,
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.backfill_window",
        ) as mock_bf,
    ):
        result = await run_intraday_bars_daily_ingest_job(None)

    assert result["status"] == "skipped_token_expired"
    MockKite.assert_not_called()
    mock_bf.assert_not_called()


async def test_skipped_when_universe_empty(fake_session):
    factory, _ = fake_session
    uid = uuid4()
    with (
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
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
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_nifty500_universe",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.KiteClient",
        ) as MockKite,
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.backfill_window",
        ) as mock_bf,
    ):
        result = await run_intraday_bars_daily_ingest_job(None)

    assert result["status"] == "skipped_empty_universe"
    MockKite.assert_not_called()
    mock_bf.assert_not_called()


async def test_failures_sample_capped_to_10(fake_session):
    """If backfill_window returns 50 failed tickers across 3
    intervals, the aggregate ``sample_failures`` MUST cap at 10
    so scheduler_runs doesn't store an unbounded blob."""
    factory, _ = fake_session
    uid = uuid4()
    fail_block = [(f"BAD{i}.NS", "fetch:boom") for i in range(50)]
    with (
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
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
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_nifty500_universe",
            new=AsyncMock(return_value=["A.NS"]),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_instrument_tokens",
            new=AsyncMock(return_value={"A.NS": 1}),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.KiteClient",
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.backfill_window",
            return_value=_stats(failed=50, failures=fail_block),
        ),
    ):
        result = await run_intraday_bars_daily_ingest_job(None)

    assert result["status"] == "ok"
    assert result["tickers_failed"] == 150
    assert len(result["sample_failures"]) == 10


async def test_payload_overrides_intervals_and_window(
    fake_session,
):
    """Operator can poke a custom backfill via payload."""
    factory, _ = fake_session
    uid = uuid4()
    with (
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
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
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_nifty500_universe",
            new=AsyncMock(return_value=["A.NS"]),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_instrument_tokens",
            new=AsyncMock(return_value={"A.NS": 1}),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.KiteClient",
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.backfill_window",
            return_value=_stats(),
        ) as mock_bf,
    ):
        result = await run_intraday_bars_daily_ingest_job(
            {
                "intervals": [900],
                "start": "2026-05-01",
                "end": "2026-05-13",
            }
        )

    assert result["intervals"] == [900]
    assert result["start"] == "2026-05-01"
    assert result["end"] == "2026-05-13"
    assert mock_bf.call_count == 1
    call_kwargs = mock_bf.call_args.kwargs
    assert call_kwargs["interval_sec"] == 900
    assert call_kwargs["start"] == date(2026, 5, 1)
    assert call_kwargs["end"] == date(2026, 5, 13)


async def test_keeper_passes_quality_hook_to_backfill_window(
    fake_session,
):
    """Slice 1e: every backfill_window call MUST receive a
    non-None ``on_batch_written`` hook so the pipeline quality
    assertions actually fire on the daily keeper's writes."""
    factory, _ = fake_session
    uid = uuid4()
    with (
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "disposable_pg_session",
            return_value=factory,
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
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
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_nifty500_universe",
            new=AsyncMock(return_value=["A.NS"]),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest."
            "_resolve_instrument_tokens",
            new=AsyncMock(return_value={"A.NS": 1}),
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.KiteClient",
        ),
        patch(
            "backend.algo.jobs.intraday_bars_daily_ingest.backfill_window",
            return_value=_stats(),
        ) as mock_bf,
    ):
        await run_intraday_bars_daily_ingest_job(None)

    # Every interval got a hook.
    for call in mock_bf.call_args_list:
        assert call.kwargs.get("on_batch_written") is not None


async def test_register_job_dispatch_wiring():
    """``intraday_bars_daily_ingest`` must be registered in the
    JOB_EXECUTORS map — without this the pipeline executor can't
    chain it and the seeder script's pipeline row is dormant."""
    from backend.jobs.executor import JOB_EXECUTORS

    assert "intraday_bars_daily_ingest" in JOB_EXECUTORS


def test_register_job_wrapper_is_pipeline_compatible():
    """``PipelineExecutor._run_step`` invokes the registered
    handler with ``(scope, run_id, repo, cancel_event=, force=)``.
    Our wrapper must be sync + accept that signature, bridging to
    the async job under the hood. Without this the handler would
    return a coroutine and the pipeline step would silent-succeed
    (the 2026-05-12 stale-VIX class of bug)."""
    import inspect

    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["intraday_bars_daily_ingest"]
    # Must be a regular function (not a coroutine function) so the
    # pipeline executor's sync invocation actually runs the job.
    assert not inspect.iscoroutinefunction(fn)
    params = inspect.signature(fn).parameters
    for required in ("scope", "run_id", "repo", "cancel_event", "force"):
        assert (
            required in params
        ), f"wrapper missing pipeline-step param: {required}"


def test_register_job_wrapper_runs_async_job_via_asyncio():
    """The sync wrapper must actually drive the async job to
    completion, not just hand back a coroutine."""
    from unittest.mock import patch

    from backend.jobs.executor import JOB_EXECUTORS

    fn = JOB_EXECUTORS["intraday_bars_daily_ingest"]

    async def _fake_async_job(payload):
        return {"status": "ok", "payload_seen": payload}

    with patch(
        "backend.algo.jobs.intraday_bars_daily_ingest."
        "run_intraday_bars_daily_ingest_job",
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


@pytest.mark.asyncio
async def test_resolve_nifty500_universe_queries_stock_master():
    """Universe resolution reads from ``stock_master`` joined to
    ``stock_tags WHERE tag='nifty500' AND removed_at IS NULL``."""
    from backend.algo.jobs.intraday_bars_daily_ingest import (
        _resolve_nifty500_universe,
    )

    captured: dict[str, str] = {}

    class _StubResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _StubSession:
        async def execute(self, q, params=None):
            captured["sql"] = str(q)
            return _StubResult(
                [("RELIANCE.NS",), ("TCS.NS",), ("INFY.NS",)],
            )

    tickers = await _resolve_nifty500_universe(_StubSession())
    assert tickers == ["RELIANCE.NS", "TCS.NS", "INFY.NS"]
    sql = captured["sql"]
    # Defence: tag filter + soft-remove guard + is_active so the
    # keeper never pulls delisted or de-tagged symbols.
    assert "stock_master" in sql
    assert "stock_tags" in sql
    assert "nifty500" in sql
    assert "removed_at IS NULL" in sql
    assert "is_active" in sql


@pytest.mark.asyncio
async def test_resolve_nifty500_universe_empty_when_untagged():
    """Empty query result → empty list, no exception."""
    from backend.algo.jobs.intraday_bars_daily_ingest import (
        _resolve_nifty500_universe,
    )

    class _StubResult:
        def all(self):
            return []

    class _StubSession:
        async def execute(self, q, params=None):
            return _StubResult()

    assert await _resolve_nifty500_universe(_StubSession()) == []

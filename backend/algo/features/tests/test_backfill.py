"""Smoke tests for the on-demand
:func:`backend.algo.features.backfill.backfill_features_window`.

The function is a thin payload-shaping shim over the daily compute
job; we verify that the payload reaches the job with the expected
keys and that the empty-ticker fast path returns a structured
``skipped_empty_universe`` dict without invoking the job.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from backend.algo.features import FEATURE_SET_VERSION
from backend.algo.features.backfill import backfill_features_window


@pytest.mark.asyncio
async def test_backfill_delegates_to_daily_compute_job_with_payload():
    """The payload the job receives must carry tickers + window +
    interval_sec + feature_set_version + batch_size — those are the
    five knobs FE-4's loader will be tuning on a cache miss."""
    fake = AsyncMock(
        return_value={
            "status": "ok",
            "rows_written": 5,
            "tickers_processed": 1,
        }
    )
    with patch(
        "backend.algo.jobs.intraday_features_daily_compute."
        "run_intraday_features_daily_compute_job",
        new=fake,
    ):
        result = await backfill_features_window(
            tickers=["X.NS"],
            interval_sec=900,
            period_start=date(2026, 5, 12),
            period_end=date(2026, 5, 13),
        )

    assert result["status"] == "ok"
    assert result["rows_written"] == 5
    fake.assert_awaited_once()
    payload = fake.await_args.args[0]
    assert payload["tickers"] == ["X.NS"]
    assert payload["interval_sec"] == 900
    assert payload["period_start"] == "2026-05-12"
    assert payload["period_end"] == "2026-05-13"
    assert payload["feature_set_version"] == FEATURE_SET_VERSION
    assert payload["batch_size"] == 50


@pytest.mark.asyncio
async def test_backfill_empty_tickers_short_circuits_without_call():
    """An empty list returns the canonical skipped payload and
    never touches the job."""
    with patch(
        "backend.algo.jobs.intraday_features_daily_compute."
        "run_intraday_features_daily_compute_job",
        new=AsyncMock(side_effect=AssertionError("must not be called")),
    ):
        result = await backfill_features_window(
            tickers=[],
            interval_sec=900,
            period_start=date(2026, 5, 13),
            period_end=date(2026, 5, 13),
        )
    assert result["status"] == "skipped_empty_universe"
    assert result["rows_written"] == 0
    assert result["universe_size"] == 0
    assert result["window"] == ["2026-05-13", "2026-05-13"]


@pytest.mark.asyncio
async def test_backfill_propagates_custom_version_and_batch_size():
    """Caller can override the feature_set_version + batch_size."""
    fake = AsyncMock(return_value={"status": "ok"})
    with patch(
        "backend.algo.jobs.intraday_features_daily_compute."
        "run_intraday_features_daily_compute_job",
        new=fake,
    ):
        await backfill_features_window(
            tickers=["A.NS", "B.NS"],
            interval_sec=300,
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 13),
            feature_set_version="v1.1-test",
            batch_size=10,
        )
    payload = fake.await_args.args[0]
    assert payload["feature_set_version"] == "v1.1-test"
    assert payload["batch_size"] == 10
    assert payload["interval_sec"] == 300

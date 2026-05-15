"""FE-5.1 — EOD flush job tests (ASETPLTFRM-417).

Validates that the scheduled job drains every Redis LIST for
the trading date in ONE Iceberg commit per user, handles
malformed payloads + per-user failures gracefully, and is
idempotent under partial-flush re-runs.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from backend.algo.jobs import trade_feature_snapshots_eod_flush as eod
from backend.algo.jobs.trade_feature_snapshots_eod_flush import (
    run_trade_feature_snapshots_eod_flush_job,
)


def _payload(
    *,
    fill_id: str,
    ticker: str = "FAKE.NS",
    user_run: str = "run-1",
    strategy: str = "strat-1",
) -> str:
    return json.dumps(
        {
            "fill_id": fill_id,
            "run_id": user_run,
            "strategy_id": strategy,
            "ticker": ticker,
            "side": "BUY",
            "qty": 1,
            "fill_price": "100.00",
            "fill_ts_ns": None,
            "bar_date": "2026-05-15",
            "mode": "live",
            "features": {"rsi_14": "55.0"},
        }
    )


def _fake_cache(
    user_rows: dict[str, list[str]],
    trading_date: date = date(2026, 5, 15),
) -> MagicMock:
    """Build a MagicMock that mimics the cache layer for a
    set of pre-populated user LISTs."""
    keys = [
        f"algo:live:snapshots:{uid}:{trading_date.isoformat()}"
        for uid in user_rows
    ]
    cache = MagicMock()
    cache.scan_keys = MagicMock(return_value=keys)

    def _lrange(key, start=0, stop=-1):
        # Find matching uid -> rows.
        for uid, rows in user_rows.items():
            if key.endswith(f":{uid}:{trading_date.isoformat()}"):
                return list(rows)
        return []

    cache.lrange = MagicMock(side_effect=_lrange)
    cache.delete = MagicMock(return_value=1)
    return cache


@pytest.mark.asyncio
async def test_drains_redis_lists_for_today():
    cache = _fake_cache(
        {
            "uA": [_payload(fill_id=f"a-{i}") for i in range(5)],
            "uB": [_payload(fill_id=f"b-{i}") for i in range(5)],
            "uC": [_payload(fill_id=f"c-{i}") for i in range(5)],
        }
    )
    with (
        patch.object(eod, "get_cache", return_value=cache),
        patch.object(
            eod,
            "write_trade_feature_snapshots_batch",
            side_effect=lambda rows: len(rows),
        ),
        patch.object(
            eod,
            "_scoped_predelete_fill_ids",
        ),
        patch.object(eod, "invalidate_metadata"),
    ):
        stats = await run_trade_feature_snapshots_eod_flush_job(
            {
                "trading_date": "2026-05-15",
            }
        )
    assert stats["users_scanned"] == 3
    assert stats["users_flushed"] == 3
    assert stats["rows_written"] == 15
    # All 3 Redis keys deleted.
    assert cache.delete.call_count == 3


@pytest.mark.asyncio
async def test_one_iceberg_commit_per_user():
    cache = _fake_cache(
        {
            "uA": [_payload(fill_id=f"a-{i}") for i in range(10)],
            "uB": [_payload(fill_id=f"b-{i}") for i in range(20)],
        }
    )
    with (
        patch.object(eod, "get_cache", return_value=cache),
        patch.object(
            eod,
            "write_trade_feature_snapshots_batch",
            side_effect=lambda rows: len(rows),
        ) as wr,
        patch.object(eod, "_scoped_predelete_fill_ids"),
        patch.object(eod, "invalidate_metadata"),
    ):
        await run_trade_feature_snapshots_eod_flush_job(
            {
                "trading_date": "2026-05-15",
            }
        )
    # 2 users -> exactly 2 Iceberg writes (NOT 30 per-row).
    assert wr.call_count == 2
    sizes = sorted(len(c.args[0]) for c in wr.call_args_list)
    assert sizes == [10, 20]


@pytest.mark.asyncio
async def test_dry_run_no_iceberg_write():
    cache = _fake_cache({"uA": [_payload(fill_id="x1")]})
    with (
        patch.object(eod, "get_cache", return_value=cache),
        patch.object(
            eod,
            "write_trade_feature_snapshots_batch",
        ) as wr,
        patch.object(eod, "_scoped_predelete_fill_ids") as pre,
        patch.object(eod, "invalidate_metadata"),
    ):
        stats = await run_trade_feature_snapshots_eod_flush_job(
            {
                "trading_date": "2026-05-15",
                "dry_run": True,
            }
        )
    assert wr.call_count == 0
    assert pre.call_count == 0
    # dry-run never deletes the Redis key.
    assert cache.delete.call_count == 0
    assert stats["dry_run"] is True
    assert stats["rows_written"] == 1


@pytest.mark.asyncio
async def test_per_user_failure_continues_batch():
    cache = _fake_cache(
        {
            "uA": [_payload(fill_id="a1")],
            "uB": [_payload(fill_id="b1")],
            "uC": [_payload(fill_id="c1")],
        }
    )

    def _maybe_fail(rows):
        # User uB's batch fails — assert by inspecting fill_id.
        if rows[0].fill_id == "b1":
            raise RuntimeError("simulated iceberg outage")
        return len(rows)

    with (
        patch.object(eod, "get_cache", return_value=cache),
        patch.object(
            eod,
            "write_trade_feature_snapshots_batch",
            side_effect=_maybe_fail,
        ),
        patch.object(eod, "_scoped_predelete_fill_ids"),
        patch.object(eod, "invalidate_metadata"),
    ):
        stats = await run_trade_feature_snapshots_eod_flush_job(
            {
                "trading_date": "2026-05-15",
            }
        )
    assert stats["users_flushed"] == 2  # A + C, not B
    assert stats["rows_written"] == 2
    # uB's Redis key NOT deleted (retry next run); uA + uC deleted.
    assert cache.delete.call_count == 2
    failure_uids = {f[0] for f in stats["failures"]}
    assert "uB" in failure_uids


@pytest.mark.asyncio
async def test_malformed_payload_dropped():
    cache = _fake_cache(
        {
            "uA": ["{this is not json", _payload(fill_id="a-ok")],
        }
    )
    with (
        patch.object(eod, "get_cache", return_value=cache),
        patch.object(
            eod,
            "write_trade_feature_snapshots_batch",
            side_effect=lambda rows: len(rows),
        ) as wr,
        patch.object(eod, "_scoped_predelete_fill_ids"),
        patch.object(eod, "invalidate_metadata"),
    ):
        stats = await run_trade_feature_snapshots_eod_flush_job(
            {
                "trading_date": "2026-05-15",
            }
        )
    # 1 good row survives, 1 dropped.
    assert stats["rows_written"] == 1
    rows_arg = wr.call_args.args[0]
    assert len(rows_arg) == 1
    assert rows_arg[0].fill_id == "a-ok"


@pytest.mark.asyncio
async def test_explicit_trading_date_payload_override():
    """``trading_date`` in payload overrides today-IST default."""
    cache = _fake_cache(
        {"uA": [_payload(fill_id="a1")]},
        trading_date=date(2026, 1, 2),
    )
    with (
        patch.object(eod, "get_cache", return_value=cache),
        patch.object(
            eod,
            "write_trade_feature_snapshots_batch",
            return_value=1,
        ),
        patch.object(eod, "_scoped_predelete_fill_ids"),
        patch.object(eod, "invalidate_metadata"),
    ):
        stats = await run_trade_feature_snapshots_eod_flush_job(
            {
                "trading_date": "2026-01-02",
            }
        )
    assert stats["trading_date"] == "2026-01-02"
    # scan_keys was called with the overridden date.
    pattern = cache.scan_keys.call_args.args[0]
    assert pattern == "algo:live:snapshots:*:2026-01-02"


@pytest.mark.asyncio
async def test_explicit_user_ids_payload_filter():
    """``user_ids`` payload bypasses SCAN — builds keys directly."""
    cache = MagicMock()
    cache.scan_keys = MagicMock(return_value=[])

    def _lrange(key, start=0, stop=-1):
        return [_payload(fill_id="x1")] if "uA" in key else []

    cache.lrange = MagicMock(side_effect=_lrange)
    cache.delete = MagicMock(return_value=1)
    with (
        patch.object(eod, "get_cache", return_value=cache),
        patch.object(
            eod,
            "write_trade_feature_snapshots_batch",
            return_value=1,
        ),
        patch.object(eod, "_scoped_predelete_fill_ids"),
        patch.object(eod, "invalidate_metadata"),
    ):
        stats = await run_trade_feature_snapshots_eod_flush_job(
            {
                "trading_date": "2026-05-15",
                "user_ids": ["uA"],
            }
        )
    # scan_keys NOT used when explicit user_ids supplied.
    assert cache.scan_keys.call_count == 0
    assert stats["users_scanned"] == 1
    assert stats["users_flushed"] == 1


def test_register_job_wired_in_executor():
    """Smoke-check the @register_job side-effect: the executor
    registry must know about this job at import time."""
    from backend.jobs.executor import JOB_EXECUTORS

    assert "trade_feature_snapshots_eod_flush" in JOB_EXECUTORS


@pytest.mark.asyncio
async def test_idempotent_partial_flush_reruns():
    """Second run for the same trading_date is safe — already-
    flushed users have their Redis key deleted by the first
    run, so LRANGE returns [] on the second."""
    state = {
        "uA": [_payload(fill_id="a1"), _payload(fill_id="a2")],
    }
    cache = MagicMock()
    cache.scan_keys = MagicMock(
        return_value=[
            "algo:live:snapshots:uA:2026-05-15",
        ],
    )

    def _lrange(key, start=0, stop=-1):
        if "uA" in key:
            return list(state["uA"])
        return []

    def _delete(*keys):
        for k in keys:
            if "uA" in k:
                state["uA"] = []
        return len(keys)

    cache.lrange = MagicMock(side_effect=_lrange)
    cache.delete = MagicMock(side_effect=_delete)
    with (
        patch.object(eod, "get_cache", return_value=cache),
        patch.object(
            eod,
            "write_trade_feature_snapshots_batch",
            return_value=2,
        ) as wr,
        patch.object(eod, "_scoped_predelete_fill_ids"),
        patch.object(eod, "invalidate_metadata"),
    ):
        # First run: drains and DELs.
        stats1 = await run_trade_feature_snapshots_eod_flush_job(
            {
                "trading_date": "2026-05-15",
            }
        )
        # Second run: LRANGE returns [], no write.
        stats2 = await run_trade_feature_snapshots_eod_flush_job(
            {
                "trading_date": "2026-05-15",
            }
        )
    assert stats1["rows_written"] == 2
    assert stats2["rows_written"] == 0
    # Iceberg write only happened on the first run.
    assert wr.call_count == 1

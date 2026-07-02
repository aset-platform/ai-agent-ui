"""Tests for the algo_closed_trades_rollup daily job."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.jobs.closed_trades_rollup import (
    run_closed_trades_rollup_job,
)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def _fill_row(strategy_id, event_id, symbol, side, qty, price, ts, mode):
    return {
        "event_id": event_id,
        "strategy_id": strategy_id,
        "user_id": str(uuid4()),
        "mode": mode,
        "type": "order_filled" if mode == "paper" else "order_filled_live",
        "payload_json": json.dumps({
            "symbol": symbol, "side": side, "qty": qty,
            "fill_price": price,
        }),
        "ts_ns": _ts(ts),
    }


@pytest.fixture
def fake_session():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


def _disposable_session_cm(session):
    class _CM:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *a):
            return None

    return lambda: _CM()


def test_upserts_paired_trades(fake_session):
    sid = str(uuid4())
    uid = str(uuid4())
    events = [
        {**_fill_row(
            sid, "e1", "ITC", "BUY", 10, 300.0,
            datetime(2026, 6, 1, tzinfo=timezone.utc), "paper",
        ), "user_id": uid},
        {**_fill_row(
            sid, "e2", "ITC", "SELL", 10, 310.0,
            datetime(2026, 6, 5, tzinfo=timezone.utc), "paper",
        ), "user_id": uid},
    ]
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=events,
    ), patch(
        "backend.db.engine.disposable_pg_session",
        _disposable_session_cm(fake_session),
    ), patch("cache.get_cache") as mock_cache:
        mock_cache.return_value = MagicMock()
        result = run_closed_trades_rollup_job(
            {"today": "2026-06-06"},
        )
    assert result["status"] == "ok"
    assert result["trades_upserted"] == 1
    # One INSERT ... ON CONFLICT executed against algo.closed_trades.
    assert fake_session.execute.await_count == 1
    call_sql = str(fake_session.execute.await_args_list[0].args[0])
    assert "algo.closed_trades" in call_sql
    assert "ON CONFLICT" in call_sql


def test_no_events_returns_zero_upserted(fake_session):
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[],
    ), patch(
        "backend.db.engine.disposable_pg_session",
        _disposable_session_cm(fake_session),
    ), patch("cache.get_cache") as mock_cache:
        mock_cache.return_value = MagicMock()
        result = run_closed_trades_rollup_job(
            {"today": "2026-06-06"},
        )
    assert result["status"] == "ok"
    assert result["trades_upserted"] == 0
    assert fake_session.execute.await_count == 0


def test_wrapper_marks_scheduler_run_success(fake_session):
    """Found 2026-07-02: the scheduled 16:30 IST run completed all
    its real work (events fetched, trades paired, PG upsert done --
    confirmed via computed_at matching the run's started_at) but the
    scheduler_runs row stayed status='running' forever, because the
    executor.py wrapper never called the shared _algo_job_success
    helper that every other standalone (non-pipeline) algo job
    wrapper calls (e.g. _job_algo_reconciliation,
    _job_algo_kite_instruments_refresh). scheduler_service.py's own
    dispatcher only ever sets duration_secs on success -- status is
    explicitly documented as the executor's responsibility
    ("executor sets status to success/failed itself")."""
    from backend.jobs.executor import _job_algo_closed_trades_rollup

    run_id = "test-run-id"
    repo = MagicMock()
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[],
    ), patch(
        "backend.db.engine.disposable_pg_session",
        _disposable_session_cm(fake_session),
    ), patch("cache.get_cache") as mock_cache:
        mock_cache.return_value = MagicMock()
        result = _job_algo_closed_trades_rollup(
            scope="all", run_id=run_id, repo=repo,
            payload={"today": "2026-06-06"},
        )
    assert result["status"] == "ok"
    repo.update_scheduler_run.assert_called_once()
    call_args = repo.update_scheduler_run.call_args
    assert call_args.args[0] == run_id
    updates = call_args.args[1]
    assert updates["status"] == "success"
    assert "completed_at" in updates


def test_dry_run_does_not_write(fake_session):
    events = [
        _fill_row(
            str(uuid4()), "e1", "ITC", "BUY", 10, 300.0,
            datetime(2026, 6, 1, tzinfo=timezone.utc), "paper",
        ),
        _fill_row(
            str(uuid4()), "e2", "ITC", "SELL", 10, 310.0,
            datetime(2026, 6, 5, tzinfo=timezone.utc), "paper",
        ),
    ]
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=events,
    ), patch(
        "backend.db.engine.disposable_pg_session",
        _disposable_session_cm(fake_session),
    ):
        result = run_closed_trades_rollup_job(
            {"today": "2026-06-06", "dry_run": True},
        )
    assert result["status"] == "dry_run"
    assert fake_session.execute.await_count == 0

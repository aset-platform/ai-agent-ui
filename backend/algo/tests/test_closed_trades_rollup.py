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

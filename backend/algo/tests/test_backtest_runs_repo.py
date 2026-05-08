"""Round-trip the lifecycle of an algo.runs row using a stub
session. Mirrors the pattern from test_instruments_repo to avoid
event-loop-isolation issues with real PG in pytest-asyncio.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from backend.algo.backtest.runs_repo import BacktestRunsRepo
from backend.algo.backtest.types import (
    BacktestSummary, EquityPoint,
)


class _StubSession:
    """In-memory stand-in for AsyncSession.

    Tracks rows in self.rows keyed by id; routes the SQL strings
    used by BacktestRunsRepo to dict updates.
    """

    def __init__(self) -> None:
        self.rows: dict[UUID, dict] = {}

    async def execute(self, q, params=None):  # noqa: ANN001
        sql = str(q)
        params = dict(params or {})

        class _Res:
            def __init__(self, items):
                self._items = items

            def mappings(self):
                return self

            def all(self):
                return self._items

            def first(self):
                return self._items[0] if self._items else None

        if "INSERT INTO algo.runs" in sql:
            self.rows[params["id"]] = {
                "id": params["id"],
                "strategy_id": params["sid"],
                "user_id": params["uid"],
                "mode": "backtest",
                "status": "pending",
                "period_start": params["ps"],
                "period_end": params["pe"],
                "started_at": params["sa"],
                "completed_at": None,
                "summary_json": None,
                "error_text": None,
            }
            return _Res([self.rows[params["id"]]])

        if "UPDATE algo.runs SET status = 'running'" in sql:
            row = self.rows.get(params["id"])
            if row:
                row["status"] = "running"
            return _Res([])

        if "status = 'completed'" in sql:
            row = self.rows.get(params["id"])
            if row:
                row["status"] = "completed"
                row["completed_at"] = params["ca"]
                # Parse JSON to mimic JSONB round-trip.
                import json
                row["summary_json"] = json.loads(params["sj"])
            return _Res([])

        if "status = 'failed'" in sql:
            row = self.rows.get(params["id"])
            if row:
                row["status"] = "failed"
                row["completed_at"] = params["ca"]
                row["error_text"] = params["et"]
            return _Res([])

        if "WHERE id = :id AND user_id" in sql:
            row = self.rows.get(params["id"])
            if row and row["user_id"] == params["uid"]:
                return _Res([row])
            return _Res([])

        if "ORDER BY started_at DESC" in sql:
            uid = params["uid"]
            matches = [
                r for r in self.rows.values()
                if r["user_id"] == uid
            ]
            matches.sort(key=lambda r: r["started_at"], reverse=True)
            sliced = matches[
                params["off"]: params["off"] + params["lim"]
            ]
            return _Res(sliced)

        return _Res([])

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_create_then_mark_completed_round_trip():
    repo = BacktestRunsRepo()
    session = _StubSession()
    user_id = uuid4()
    strategy_id = uuid4()

    row = await repo.create_pending(
        session,
        user_id=user_id, strategy_id=strategy_id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    assert row.status == "pending"

    await repo.mark_running(session, run_id=row.run_id)

    summary = BacktestSummary(
        run_id=row.run_id, strategy_id=strategy_id,
        status="completed",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        initial_capital_inr=Decimal("100000"),
        final_equity_inr=Decimal("105000"),
        total_pnl_inr=Decimal("5000"),
        total_pnl_pct=Decimal("5"),
        total_fees_inr=Decimal("100"),
        total_trades=1, winning_trades=1, losing_trades=0,
        win_rate_pct=Decimal("100"),
        max_drawdown_pct=Decimal("0"),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        fee_rates_version="2026-04-01",
        equity_curve=[
            EquityPoint(bar_date=date(2026, 4, 1),
                        equity_inr=Decimal("100000")),
        ],
        trade_list=[],
    )

    await repo.mark_completed(
        session, run_id=row.run_id, summary=summary,
    )

    fetched = await repo.get_by_id(
        session, user_id=user_id, run_id=row.run_id,
    )
    assert fetched is not None
    assert fetched.status == "completed"
    assert len(fetched.equity_curve) == 1


@pytest.mark.asyncio
async def test_mark_failed_records_error_text():
    repo = BacktestRunsRepo()
    session = _StubSession()
    user_id = uuid4()

    row = await repo.create_pending(
        session, user_id=user_id, strategy_id=uuid4(),
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    await repo.mark_failed(
        session, run_id=row.run_id,
        error_text="period_start after period_end",
    )
    fetched = await repo.get_by_id(
        session, user_id=user_id, run_id=row.run_id,
    )
    assert fetched.status == "failed"
    assert fetched.error_text == "period_start after period_end"


@pytest.mark.asyncio
async def test_list_by_user_paginates_newest_first():
    repo = BacktestRunsRepo()
    session = _StubSession()
    user_id = uuid4()

    for _ in range(3):
        await repo.create_pending(
            session, user_id=user_id, strategy_id=uuid4(),
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
        )

    rows = await repo.list_by_user(
        session, user_id=user_id, limit=10, offset=0,
    )
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_get_by_id_returns_none_for_unknown():
    repo = BacktestRunsRepo()
    session = _StubSession()
    fetched = await repo.get_by_id(
        session, user_id=uuid4(), run_id=uuid4(),
    )
    assert fetched is None

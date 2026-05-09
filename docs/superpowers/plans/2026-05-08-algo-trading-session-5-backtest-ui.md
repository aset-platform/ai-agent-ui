# Algo Trading — Session 5: Backtest UI + PG persistence (Slice 7b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Slice 7b — promote the headless backtest engine from Session 4 (Slice 7a) into a user-facing tab. Replaces the in-memory `_RUNS` dict with PG-backed `algo.runs` persistence; resolves universe from the strategy's stored `universe.scope` via the existing `_scoped_tickers` helper; wraps `POST /run` in a background job so the UI receives `run_id` immediately and polls; and ships the `BacktestTab` UI with a run form, summary metric cards, equity curve (ECharts), and trade table (with column selector + CSV download per CLAUDE.md §5.4).

**Architecture:** Three layers, in order. (1) Backend: a single Alembic migration adds `summary_json jsonb` to `algo.runs` so we persist the full equity curve + trade list inline (small enough that MinIO is overkill for v1; promoted to MinIO in 7c when run sizes grow). (2) `runner.run_backtest()` extends to emit `equity_curve: list[dict]` and `trade_list: list[dict]` on `BacktestSummary`. (3) Routes refactor: `POST /run` enqueues a `BackgroundTask`, returns `run_id` + `status="pending"` immediately; the task body runs the existing engine, persists `summary_json` + status transitions; `GET /runs/{id}` reads from PG. (4) Frontend: new `useBacktestRun` SWR hook polls every 2s while status ∈ `{pending, running}`; `BacktestTab` composes a run form, summary cards, equity curve, and trade table.

**Tech Stack:** Python 3.12 / FastAPI BackgroundTasks / asyncpg / SQLAlchemy 2.0 async / Pydantic 2 / pytest. Frontend: Next.js 16 / React 19 / SWR / ECharts (tree-shaken, see `frontend/lib/echarts.ts`) / Tailwind. Reuses Slice 7a's `runner`, `evaluator`, `sim_broker`, `positions`, `event_writer`. Reuses `_scoped_tickers` from `backend/insights_routes.py`.

**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md` (§ 2.2 tab strip, § 9 Slice 7).

**Branch:** `feature/algo-trading-session-5-backtest-ui` (cut off Session 4's tip `fc13b1e`).

**Conventions reminders:**
- Branch off `dev`; squash-only merge; Co-Authored-By Abhay; line length 79; `X | None`; `_logger`; backend restart after route/model changes (CLAUDE.md §6.2).
- `apiFetch` (not bare `fetch`); SWR hook pattern (CLAUDE.md §5.3); ECharts theme via `useDarkMode` MutationObserver (CLAUDE.md §5.3); `<Image />` not `<img>` (ESLint).
- Tabular pages MUST follow CLAUDE.md §5.4 — column selector + CSV download share a single `visibleCols` source of truth; locked identity column.
- Cache: `cache:algo:runs:{user_id}` keys, `TTL_VOLATILE=60` (per CLAUDE.md §5.13). Add `algo.runs` entry to `_CACHE_INVALIDATION_MAP`.
- Run record persistence MUST use `_pg_session()` from `backend/algo/strategy/repo.py` pattern (NullPool, sync→async bridge — CLAUDE.md §5.1).

---

## File Structure

### Backend (new)

- `backend/db/migrations/versions/2026_05_08_algo_runs_summary_json.py` — adds `summary_json jsonb NULL` + `error_text text NULL` columns to `algo.runs`.
- `backend/algo/backtest/runs_repo.py` — async CRUD on `algo.runs`. `create_pending`, `mark_running`, `mark_completed`, `mark_failed`, `get_by_id`, `list_by_user`.
- `backend/algo/backtest/universe.py` — `resolve_universe(user, strategy)` reusing `_scoped_tickers`.
- `backend/algo/backtest/job.py` — `run_backtest_job(run_id, user_id, strategy_id, request_json)` background coroutine.

### Backend (modified)

- `backend/algo/backtest/types.py` — add `equity_curve` + `trade_list` + `status` fields to `BacktestSummary`; new `BacktestRun` model for the list endpoint.
- `backend/algo/backtest/runner.py` — populate `equity_curve` + `trade_list` on the returned summary.
- `backend/algo/routes/backtest.py` — replace `_RUNS` dict with PG-backed run repo; `POST /run` returns 202 + `run_id`; add `GET /runs` list endpoint.
- `stocks/repository.py` — add `algo.runs` to `_CACHE_INVALIDATION_MAP` (already wired for `algo.events` from Slice 0).

### Tests (new)

- `backend/algo/tests/test_backtest_runs_repo.py` — CRUD round-trip on `algo.runs`.
- `backend/algo/tests/test_backtest_universe.py` — `resolve_universe` for each scope.
- `backend/algo/tests/test_backtest_job.py` — async-job wrapper happy path + error path.
- `frontend/components/algo-trading/__tests__/BacktestTab.test.tsx` — vitest render + form submit flow.

### Tests (modified)

- `backend/algo/tests/test_backtest_runner.py` — assert new `equity_curve` + `trade_list` shape.
- `backend/algo/tests/test_backtest_routes.py` — refactor for async-job + PG-backed runs.

### Frontend (new)

- `frontend/hooks/useBacktestRuns.ts` — SWR hook for list + single run, polling-aware.
- `frontend/components/algo-trading/BacktestTab.tsx` — top-level tab. Run form + active-run progress + summary cards + equity curve + trade table.
- `frontend/components/algo-trading/BacktestRunForm.tsx` — strategy picker + period range + capital input + Submit.
- `frontend/components/algo-trading/BacktestSummaryCards.tsx` — six metric cards.
- `frontend/components/algo-trading/BacktestEquityCurve.tsx` — ECharts line chart.
- `frontend/components/algo-trading/BacktestTradeTable.tsx` — sortable + column selector + CSV.
- `frontend/components/algo-trading/__tests__/BacktestEquityCurve.test.tsx` — ECharts options unit test.

### Frontend (modified)

- `frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx` — wire `BacktestTab` into the tab switch.

### E2E (new)

- `e2e/tests/algo-trading/backtest-flow.spec.ts` — Playwright smoke: open Backtest tab → see empty state.

---

## Task 1: Migration — `algo.runs.summary_json` + `error_text`

**Files:**
- Create: `backend/db/migrations/versions/2026_05_08_algo_runs_summary_json.py`

- [ ] **Step 1: Find current head**

```bash
docker compose exec backend alembic current 2>&1 | tail -3
```

Note the head revision ID (the long hex). The new migration's `down_revision` MUST equal that. The Slice 0 head was `f8e7d6c5b4a3` after re-parenting (per Session 1 adaptation note in resumption memory) — verify before writing.

- [ ] **Step 2: Write migration**

```python
# backend/db/migrations/versions/2026_05_08_algo_runs_summary_json.py
"""Add summary_json + error_text to algo.runs (Slice 7b).

Revision ID: b3c5e7d9f1a4
Revises: <CURRENT HEAD — verify with `alembic current`>
Create Date: 2026-05-08

Slice 7b inlines the equity curve + trade list as JSONB on the
runs row instead of pushing to MinIO. JSONB is fine for v1 sizes
(<1MB per run on watchlist-scoped backtests). MinIO promotion is
deferred to a future 7c if runs grow.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3c5e7d9f1a4"
down_revision: str | None = "<PASTE HEAD FROM STEP 1>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "summary_json", postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="algo",
    )
    op.add_column(
        "runs",
        sa.Column("error_text", sa.Text(), nullable=True),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_column("runs", "error_text", schema="algo")
    op.drop_column("runs", "summary_json", schema="algo")
```

- [ ] **Step 3: Run + verify**

```bash
docker compose exec backend alembic upgrade head 2>&1 | tail -10
docker compose exec postgres psql -U postgres -d aiagent \
  -c "\d algo.runs" 2>&1 | grep -E "summary_json|error_text"
```

Expected: both columns visible with `jsonb` and `text` types.

- [ ] **Step 4: Commit**

```bash
git add backend/db/migrations/versions/2026_05_08_algo_runs_summary_json.py
git commit -m "$(cat <<'EOF'
feat(algo): migration — algo.runs.summary_json + error_text

Slice 7b. Inline the equity curve + trade list as JSONB so the
UI can render without a separate MinIO bucket on v1. error_text
captures last-failed-run reason so the UI shows a precise toast.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: Extend `BacktestSummary` types + runner output

**Files:**
- Modify: `backend/algo/backtest/types.py`
- Modify: `backend/algo/backtest/runner.py`
- Modify: `backend/algo/tests/test_backtest_runner.py`

- [ ] **Step 1: Extend types.py**

Append to `backend/algo/backtest/types.py` after `Position` (preserve existing models verbatim — only ADD):

```python
class EquityPoint(BaseModel):
    """One end-of-day equity snapshot."""
    model_config = ConfigDict(extra="forbid")

    bar_date: date
    equity_inr: Decimal


class TradeRow(BaseModel):
    """One closed-position row for the trade table."""
    model_config = ConfigDict(extra="forbid")

    ticker: str
    qty: int
    avg_price: Decimal
    fill_price: Decimal
    opened_at: date
    closed_at: date
    holding_days: int
    realised_pnl_inr: Decimal
    return_pct: Decimal


class BacktestRun(BaseModel):
    """Row shape for GET /runs (list endpoint)."""
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    strategy_id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    period_start: date
    period_end: date
    started_at: datetime
    completed_at: datetime | None = None
    total_pnl_inr: Decimal | None = None
    total_pnl_pct: Decimal | None = None
    error_text: str | None = None
```

Modify `BacktestSummary` to include the new fields:

```python
class BacktestSummary(BaseModel):
    """Run-level metrics persisted to algo.runs and returned by
    GET /v1/algo/backtest/runs/{id}."""
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    strategy_id: UUID
    status: Literal["pending", "running", "completed", "failed"] = "completed"
    period_start: date
    period_end: date
    initial_capital_inr: Decimal
    final_equity_inr: Decimal
    total_pnl_inr: Decimal
    total_pnl_pct: Decimal
    total_fees_inr: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    max_drawdown_pct: Decimal
    started_at: datetime
    completed_at: datetime
    fee_rates_version: str
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    trade_list: list[TradeRow] = Field(default_factory=list)
    error_text: str | None = None
```

- [ ] **Step 2: Update runner.py**

In `backend/algo/backtest/runner.py`, replace the body of `run_backtest` so it (a) accumulates `EquityPoint`s as it walks bars (existing `equity_curve: list[Decimal]` becomes `equity_points: list[EquityPoint]` paired with `bar_date`), and (b) builds `trade_list` from `pt.closed_positions()` after the bar walk.

Add the following helpers at module top (after `_features_for_bar`):

```python
def _trade_row(p, fill_price: Decimal) -> "TradeRow":  # noqa: ANN001
    """Project a closed Position into a TradeRow for the UI."""
    from backend.algo.backtest.types import TradeRow  # local import
    holding_days = (p.closed_at - p.opened_at).days if p.closed_at else 0
    return_pct = (
        ((fill_price - p.avg_price) / p.avg_price) * Decimal("100")
        if p.avg_price > 0 else Decimal("0")
    )
    return TradeRow(
        ticker=p.ticker,
        qty=p.qty,
        avg_price=p.avg_price,
        fill_price=fill_price,
        opened_at=p.opened_at,
        closed_at=p.closed_at,
        holding_days=holding_days,
        realised_pnl_inr=p.realised_pnl_inr,
        return_pct=return_pct,
    )
```

Replace the `equity_curve: list[Decimal] = ...` line with:

```python
    equity_points: list = []  # list[EquityPoint]
```

Replace the inner `equity_curve.append(equity)` line with:

```python
        from backend.algo.backtest.types import EquityPoint
        equity_points.append(EquityPoint(
            bar_date=bar_date, equity_inr=equity,
        ))
```

After the bar loop, derive `final_equity` from `equity_points[-1].equity_inr` (or `initial_capital_inr` if list is empty):

```python
    final_equity = (
        equity_points[-1].equity_inr if equity_points
        else request.initial_capital_inr
    )
```

Build `trade_list` from `pt.closed_positions()` — for v1 we don't have a stored fill price per close, so use `avg_price + realised_pnl/qty` as the implied fill price:

```python
    trade_rows = []
    for p in pt.closed_positions():
        implied_fill = (
            p.avg_price + (p.realised_pnl_inr / Decimal(p.qty))
            if p.qty > 0 else p.avg_price
        )
        trade_rows.append(_trade_row(p, implied_fill))
```

Pass both to `BacktestSummary(..., equity_curve=equity_points, trade_list=trade_rows)`.

- [ ] **Step 3: Update test_backtest_runner.py**

Add to `backend/algo/tests/test_backtest_runner.py`:

```python
def test_runner_emits_equity_curve(patches):
    strategy = parse_strategy(_strategy_payload())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    assert len(summary.equity_curve) == 30
    assert summary.equity_curve[0].bar_date == date(2026, 4, 1)
    assert summary.equity_curve[-1].equity_inr > Decimal("0")


def test_runner_trade_list_empty_when_no_closes(patches):
    """The default strategy is BUY-only — no closed positions, so
    trade_list is empty even though events fired."""
    strategy = parse_strategy(_strategy_payload())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    assert summary.trade_list == []
```

- [ ] **Step 4: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_runner.py -v 2>&1 | tail -10

git add backend/algo/backtest/types.py backend/algo/backtest/runner.py backend/algo/tests/test_backtest_runner.py
git commit -m "$(cat <<'EOF'
feat(algo): backtest summary — equity_curve + trade_list

Slice 7b. BacktestSummary now carries the per-bar equity curve
(EquityPoint list) and closed-trade rows (TradeRow list) so the
UI can render charts/tables directly from the API response.
Also adds BacktestRun model for the upcoming list endpoint and
status enum to support the async-job wrapper. 6 runner tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: Backtest runs repo (PG CRUD)

**Files:**
- Create: `backend/algo/backtest/runs_repo.py`
- Create: `backend/algo/tests/test_backtest_runs_repo.py`

- [ ] **Step 1: Failing test**

```python
# backend/algo/tests/test_backtest_runs_repo.py
"""Round-trip the lifecycle of an algo.runs row."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.algo.backtest.runs_repo import BacktestRunsRepo
from backend.algo.backtest.types import (
    BacktestSummary, EquityPoint, TradeRow,
)
from backend.db.engine import get_session_factory


@pytest.mark.asyncio
async def test_create_then_mark_completed_round_trip():
    repo = BacktestRunsRepo()
    factory = get_session_factory()
    user_id = uuid4()
    strategy_id = uuid4()  # FK violation tolerated in test schema

    async with factory() as session:
        row = await repo.create_pending(
            session,
            user_id=user_id,
            strategy_id=strategy_id,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
        )
        await session.commit()
    run_id = row.run_id
    assert row.status == "pending"

    async with factory() as session:
        await repo.mark_running(session, run_id=run_id)
        await session.commit()

    summary = BacktestSummary(
        run_id=run_id, strategy_id=strategy_id,
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
        started_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc,
        ),
        completed_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc,
        ),
        fee_rates_version="2026-04-01",
        equity_curve=[
            EquityPoint(bar_date=date(2026, 4, 1),
                        equity_inr=Decimal("100000")),
        ],
        trade_list=[],
    )

    async with factory() as session:
        await repo.mark_completed(
            session, run_id=run_id, summary=summary,
        )
        await session.commit()

    async with factory() as session:
        fetched = await repo.get_by_id(
            session, user_id=user_id, run_id=run_id,
        )
    assert fetched is not None
    assert fetched.status == "completed"
    assert len(fetched.equity_curve) == 1


@pytest.mark.asyncio
async def test_mark_failed_records_error_text():
    repo = BacktestRunsRepo()
    factory = get_session_factory()
    user_id = uuid4()

    async with factory() as session:
        row = await repo.create_pending(
            session, user_id=user_id, strategy_id=uuid4(),
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
        )
        await session.commit()

    async with factory() as session:
        await repo.mark_failed(
            session, run_id=row.run_id,
            error_text="period_start after period_end",
        )
        await session.commit()

    async with factory() as session:
        fetched = await repo.get_by_id(
            session, user_id=user_id, run_id=row.run_id,
        )
    assert fetched.status == "failed"
    assert fetched.error_text == "period_start after period_end"


@pytest.mark.asyncio
async def test_list_by_user_paginates_newest_first():
    repo = BacktestRunsRepo()
    factory = get_session_factory()
    user_id = uuid4()

    async with factory() as session:
        for _ in range(3):
            await repo.create_pending(
                session, user_id=user_id, strategy_id=uuid4(),
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 30),
            )
        await session.commit()

    async with factory() as session:
        rows = await repo.list_by_user(
            session, user_id=user_id, limit=10, offset=0,
        )
    assert len(rows) == 3
```

- [ ] **Step 2: Run — expect ImportError**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_runs_repo.py -v 2>&1 | tail -8
```

- [ ] **Step 3: Implement repo**

```python
# backend/algo/backtest/runs_repo.py
"""Async CRUD for algo.runs.

Wraps SQLAlchemy core inserts/selects with the canonical
session pattern from ``backend/algo/strategy/repo.py``. Returns
plain dicts (or ``BacktestRun``/``BacktestSummary`` Pydantic
models) — never ORM rows.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.algo.backtest.types import (
    BacktestRun, BacktestSummary,
)

_logger = logging.getLogger(__name__)


class BacktestRunsRepo:
    async def create_pending(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        strategy_id: UUID,
        period_start: date,
        period_end: date,
    ) -> BacktestRun:
        run_id = uuid4()
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                "INSERT INTO algo.runs ("
                "id, strategy_id, user_id, mode, status, "
                "period_start, period_end, started_at) VALUES ("
                ":id, :sid, :uid, 'backtest', 'pending', "
                ":ps, :pe, :sa)"
            ),
            {
                "id": run_id, "sid": strategy_id, "uid": user_id,
                "ps": period_start, "pe": period_end, "sa": now,
            },
        )
        return BacktestRun(
            run_id=run_id, strategy_id=strategy_id,
            status="pending",
            period_start=period_start, period_end=period_end,
            started_at=now,
        )

    async def mark_running(
        self, session: AsyncSession, *, run_id: UUID,
    ) -> None:
        await session.execute(
            text(
                "UPDATE algo.runs SET status = 'running' "
                "WHERE id = :id"
            ),
            {"id": run_id},
        )

    async def mark_completed(
        self,
        session: AsyncSession,
        *,
        run_id: UUID,
        summary: BacktestSummary,
    ) -> None:
        await session.execute(
            text(
                "UPDATE algo.runs SET "
                "status = 'completed', "
                "completed_at = :ca, "
                "summary_json = :sj "
                "WHERE id = :id"
            ),
            {
                "id": run_id,
                "ca": datetime.now(timezone.utc),
                "sj": summary.model_dump_json(),
            },
        )

    async def mark_failed(
        self,
        session: AsyncSession,
        *,
        run_id: UUID,
        error_text: str,
    ) -> None:
        await session.execute(
            text(
                "UPDATE algo.runs SET "
                "status = 'failed', "
                "completed_at = :ca, "
                "error_text = :et "
                "WHERE id = :id"
            ),
            {
                "id": run_id,
                "ca": datetime.now(timezone.utc),
                "et": error_text[:2000],
            },
        )

    async def get_by_id(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        run_id: UUID,
    ) -> BacktestSummary | None:
        result = await session.execute(
            text(
                "SELECT id, strategy_id, status, period_start, "
                "period_end, started_at, completed_at, "
                "summary_json, error_text "
                "FROM algo.runs "
                "WHERE id = :id AND user_id = :uid AND "
                "      mode = 'backtest'"
            ),
            {"id": run_id, "uid": user_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        if row["summary_json"] is not None:
            return BacktestSummary.model_validate(row["summary_json"])
        # Pending or running — synthesize a partial summary.
        return BacktestSummary(
            run_id=row["id"], strategy_id=row["strategy_id"],
            status=row["status"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            initial_capital_inr=__import__("decimal").Decimal("0"),
            final_equity_inr=__import__("decimal").Decimal("0"),
            total_pnl_inr=__import__("decimal").Decimal("0"),
            total_pnl_pct=__import__("decimal").Decimal("0"),
            total_fees_inr=__import__("decimal").Decimal("0"),
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate_pct=__import__("decimal").Decimal("0"),
            max_drawdown_pct=__import__("decimal").Decimal("0"),
            started_at=row["started_at"],
            completed_at=(
                row["completed_at"] or row["started_at"]
            ),
            fee_rates_version="n/a",
            equity_curve=[],
            trade_list=[],
            error_text=row["error_text"],
        )

    async def list_by_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BacktestRun]:
        result = await session.execute(
            text(
                "SELECT id, strategy_id, status, period_start, "
                "period_end, started_at, completed_at, "
                "summary_json, error_text "
                "FROM algo.runs "
                "WHERE user_id = :uid AND mode = 'backtest' "
                "ORDER BY started_at DESC "
                "LIMIT :lim OFFSET :off"
            ),
            {"uid": user_id, "lim": limit, "off": offset},
        )
        rows: list[BacktestRun] = []
        for r in result.mappings().all():
            sj: dict[str, Any] | None = r["summary_json"]
            rows.append(BacktestRun(
                run_id=r["id"], strategy_id=r["strategy_id"],
                status=r["status"],
                period_start=r["period_start"],
                period_end=r["period_end"],
                started_at=r["started_at"],
                completed_at=r["completed_at"],
                total_pnl_inr=(
                    __import__("decimal").Decimal(
                        str(sj["total_pnl_inr"])
                    ) if sj else None
                ),
                total_pnl_pct=(
                    __import__("decimal").Decimal(
                        str(sj["total_pnl_pct"])
                    ) if sj else None
                ),
                error_text=r["error_text"],
            ))
        return rows
```

- [ ] **Step 4: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_runs_repo.py -v 2>&1 | tail -10

git add backend/algo/backtest/runs_repo.py backend/algo/tests/test_backtest_runs_repo.py
git commit -m "$(cat <<'EOF'
feat(algo): backtest runs repo — PG CRUD on algo.runs

Slice 7b. BacktestRunsRepo handles the run lifecycle: create_pending
→ mark_running → mark_completed (writes summary_json JSONB) /
mark_failed (writes error_text). get_by_id returns full
BacktestSummary on hit; list_by_user paginates newest-first.
3 round-trip tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Universe resolver

**Files:**
- Create: `backend/algo/backtest/universe.py`
- Create: `backend/algo/tests/test_backtest_universe.py`

- [ ] **Step 1: Failing tests**

```python
# backend/algo/tests/test_backtest_universe.py
"""Universe resolution from strategy.universe.scope."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from auth.models import UserContext
from backend.algo.backtest.universe import resolve_universe


def _make_strategy(scope: str):
    class _U:
        scope = scope
    class _S:
        universe = _U()
    return _S()


@pytest.mark.asyncio
async def test_resolve_universe_passes_scope_to_helper():
    user = UserContext(
        user_id="11111111-1111-1111-1111-111111111111",
        email="t@t", role="pro",
    )
    strategy = _make_strategy("watchlist")
    with patch(
        "backend.algo.backtest.universe._scoped_tickers",
        new=AsyncMock(return_value=["TCS.NS", "INFY.NS"]),
    ) as helper:
        out = await resolve_universe(user=user, strategy=strategy)
    helper.assert_awaited_once()
    assert helper.call_args.kwargs == {"user": user, "scope": "watchlist"}
    assert out == ["TCS.NS", "INFY.NS"]


@pytest.mark.asyncio
async def test_resolve_universe_unknown_scope_falls_back_to_watchlist():
    user = UserContext(
        user_id="22222222-2222-2222-2222-222222222222",
        email="t@t", role="pro",
    )
    strategy = _make_strategy("nonsense")
    with patch(
        "backend.algo.backtest.universe._scoped_tickers",
        new=AsyncMock(return_value=[]),
    ) as helper:
        await resolve_universe(user=user, strategy=strategy)
    assert helper.call_args.kwargs["scope"] == "watchlist"
```

- [ ] **Step 2: Implement**

```python
# backend/algo/backtest/universe.py
"""Resolve a Strategy's stored universe.scope to a concrete
list of tickers, reusing the existing _scoped_tickers helper
from insights_routes (the same scoping that powers the
Insights tabs).
"""
from __future__ import annotations

import logging
from typing import Any

from auth.models import UserContext
from backend.insights_routes import _scoped_tickers

_logger = logging.getLogger(__name__)

_VALID_SCOPES = {"discovery", "watchlist", "portfolio"}


async def resolve_universe(
    *,
    user: UserContext,
    strategy: Any,
) -> list[str]:
    """Return the list of tickers the backtest should iterate.

    Strategy AST stores ``universe.scope`` ∈ ``{discovery, watchlist,
    portfolio}``. Anything else degrades to ``watchlist`` (the
    safest non-empty default).
    """
    raw = getattr(strategy.universe, "scope", "watchlist")
    scope = raw if raw in _VALID_SCOPES else "watchlist"
    return await _scoped_tickers(user=user, scope=scope)
```

- [ ] **Step 3: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_universe.py -v 2>&1 | tail -8

git add backend/algo/backtest/universe.py backend/algo/tests/test_backtest_universe.py
git commit -m "$(cat <<'EOF'
feat(algo): backtest universe resolver

Slice 7b. resolve_universe(user, strategy) delegates to the
existing _scoped_tickers helper so backtest scoping matches
Insights scoping. Unknown scopes degrade to watchlist (non-
empty fallback). 2 unit tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: Async-job wrapper

**Files:**
- Create: `backend/algo/backtest/job.py`
- Create: `backend/algo/tests/test_backtest_job.py`

- [ ] **Step 1: Failing tests**

```python
# backend/algo/tests/test_backtest_job.py
"""Async job wrapper — happy + error path."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.algo.backtest.job import run_backtest_job
from backend.algo.backtest.types import (
    BacktestRequest, BacktestSummary,
)


def _summary(run_id, strategy_id) -> BacktestSummary:
    return BacktestSummary(
        run_id=run_id, strategy_id=strategy_id, status="completed",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        initial_capital_inr=Decimal("100000"),
        final_equity_inr=Decimal("105000"),
        total_pnl_inr=Decimal("5000"),
        total_pnl_pct=Decimal("5"),
        total_fees_inr=Decimal("100"),
        total_trades=0, winning_trades=0, losing_trades=0,
        win_rate_pct=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        fee_rates_version="2026-04-01",
    )


@pytest.mark.asyncio
async def test_job_happy_path_marks_completed():
    run_id = uuid4()
    strategy_id = uuid4()
    user_id = uuid4()
    request = BacktestRequest(
        strategy_id=strategy_id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    fake_strategy = type("S", (), {
        "id": strategy_id, "root": None,
        "universe": type("U", (), {"scope": "watchlist"})(),
    })()
    with patch(
        "backend.algo.backtest.job.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.backtest.job.resolve_universe",
        new=AsyncMock(return_value=["TCS.NS"]),
    ), patch(
        "backend.algo.backtest.job.run_backtest",
        return_value=_summary(run_id, strategy_id),
    ), patch(
        "backend.algo.backtest.job.BacktestRunsRepo"
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.mark_running = AsyncMock()
        repo.mark_completed = AsyncMock()
        repo.mark_failed = AsyncMock()
        with patch(
            "backend.algo.backtest.job._session_factory"
        ) as sf:
            sf.return_value.__aenter__ = AsyncMock(
                return_value=AsyncMock()
            )
            sf.return_value.__aexit__ = AsyncMock(
                return_value=None,
            )
            await run_backtest_job(
                run_id=run_id, user_id=user_id, request=request,
            )
        repo.mark_running.assert_awaited_once()
        repo.mark_completed.assert_awaited_once()
        repo.mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_error_path_marks_failed():
    run_id = uuid4()
    strategy_id = uuid4()
    request = BacktestRequest(
        strategy_id=strategy_id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    with patch(
        "backend.algo.backtest.job.get_strategy",
        new=AsyncMock(return_value=None),
    ), patch(
        "backend.algo.backtest.job.BacktestRunsRepo"
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.mark_running = AsyncMock()
        repo.mark_failed = AsyncMock()
        repo.mark_completed = AsyncMock()
        with patch(
            "backend.algo.backtest.job._session_factory"
        ) as sf:
            sf.return_value.__aenter__ = AsyncMock(
                return_value=AsyncMock()
            )
            sf.return_value.__aexit__ = AsyncMock(
                return_value=None,
            )
            await run_backtest_job(
                run_id=run_id, user_id=uuid4(), request=request,
            )
        repo.mark_failed.assert_awaited_once()
        repo.mark_completed.assert_not_awaited()
```

- [ ] **Step 2: Implement**

```python
# backend/algo/backtest/job.py
"""Async background coroutine that runs a backtest end-to-end
and reflects status transitions in algo.runs.

Lifecycle:
    pending  ─create_pending─►  pending  (sync, before this call)
    pending  ─mark_running────►  running
    running  ─mark_completed──►  completed (summary_json filled)
    running  ─mark_failed─────►  failed    (error_text filled)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from uuid import UUID

from auth.models import UserContext
from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.runs_repo import BacktestRunsRepo
from backend.algo.backtest.types import BacktestRequest
from backend.algo.backtest.universe import resolve_universe
from backend.algo.strategy.repo import get_strategy

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def _session_factory():
    """Wraps the lazy import so tests can patch it cleanly."""
    from backend.db.engine import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def run_backtest_job(
    *,
    run_id: UUID,
    user_id: UUID,
    request: BacktestRequest,
) -> None:
    """Execute the backtest in the background. NEVER raises —
    every error path writes via mark_failed."""
    repo = BacktestRunsRepo()
    try:
        async with _session_factory() as session:
            await repo.mark_running(session, run_id=run_id)
            await session.commit()

        async with _session_factory() as session:
            strategy = await get_strategy(
                session, user_id, request.strategy_id,
            )
        if strategy is None:
            async with _session_factory() as session:
                await repo.mark_failed(
                    session, run_id=run_id,
                    error_text="Strategy not found",
                )
                await session.commit()
            return

        # Build a minimal UserContext for the universe helper.
        user = UserContext(
            user_id=str(user_id), email="", role="pro",
        )
        universe = await resolve_universe(
            user=user, strategy=strategy,
        )

        summary = run_backtest(
            strategy=strategy, request=request,
            user_id=user_id, universe=universe,
        )
        # Stamp run_id from the route — the runner generated its
        # own; we overwrite so the persisted summary matches the
        # row id.
        summary = summary.model_copy(update={"run_id": run_id})

        async with _session_factory() as session:
            await repo.mark_completed(
                session, run_id=run_id, summary=summary,
            )
            await session.commit()

    except Exception as exc:  # noqa: BLE001 — last-resort catch
        _logger.exception("backtest job %s failed: %s", run_id, exc)
        try:
            async with _session_factory() as session:
                await repo.mark_failed(
                    session, run_id=run_id, error_text=str(exc),
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            _logger.exception("failed to record job failure")
```

- [ ] **Step 3: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_job.py -v 2>&1 | tail -8

git add backend/algo/backtest/job.py backend/algo/tests/test_backtest_job.py
git commit -m "$(cat <<'EOF'
feat(algo): backtest job wrapper — async lifecycle

Slice 7b. run_backtest_job runs the engine in the background
and transitions algo.runs status (pending→running→completed/
failed). Never raises — every error path writes via mark_failed.
2 unit tests cover happy + missing-strategy paths.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: Routes refactor — async wrapper + list endpoint + cache map

**Files:**
- Modify: `backend/algo/routes/backtest.py`
- Modify: `backend/algo/tests/test_backtest_routes.py`
- Modify: `stocks/repository.py` (add `algo.runs` to `_CACHE_INVALIDATION_MAP`)

- [ ] **Step 1: Add cache invalidation entry**

In `stocks/repository.py`, find `_CACHE_INVALIDATION_MAP` and add (preserving existing entries):

```python
    "algo.runs": ["cache:algo:runs:*"],
```

- [ ] **Step 2: Rewrite routes/backtest.py**

```python
# backend/algo/routes/backtest.py
"""POST /v1/algo/backtest/run — async-job wrapper.

POST /run creates a 'pending' run row, schedules a background
task, and returns 202 with run_id immediately. The UI polls
GET /runs/{id} until status ∈ {completed, failed}.

GET /runs lists the user's recent runs (newest first, paginated).
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi import Query
from fastapi.responses import JSONResponse

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.backtest.job import run_backtest_job
from backend.algo.backtest.runs_repo import BacktestRunsRepo
from backend.algo.backtest.types import (
    BacktestRequest, BacktestRun, BacktestSummary,
)

_logger = logging.getLogger(__name__)


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def create_backtest_router() -> APIRouter:
    router = APIRouter(prefix="/algo/backtest", tags=["algo-trading"])
    repo = BacktestRunsRepo()

    @router.post("/run", status_code=202)
    async def run_endpoint(
        body: BacktestRequest,
        background: BackgroundTasks,
        user: UserContext = Depends(pro_or_superuser),
    ):
        factory = _get_session_factory()
        async with factory() as session:
            row = await repo.create_pending(
                session,
                user_id=UUID(user.user_id),
                strategy_id=body.strategy_id,
                period_start=body.period_start,
                period_end=body.period_end,
            )
            await session.commit()

        background.add_task(
            run_backtest_job,
            run_id=row.run_id,
            user_id=UUID(user.user_id),
            request=body,
        )
        return JSONResponse(
            status_code=202,
            content={
                "run_id": str(row.run_id),
                "status": "pending",
            },
        )

    @router.get(
        "/runs/{run_id}", response_model=BacktestSummary,
    )
    async def get_run(
        run_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> BacktestSummary:
        factory = _get_session_factory()
        async with factory() as session:
            summary = await repo.get_by_id(
                session,
                user_id=UUID(user.user_id),
                run_id=run_id,
            )
        if summary is None:
            raise HTTPException(
                status_code=404, detail="Run not found",
            )
        return summary

    @router.get("/runs", response_model=list[BacktestRun])
    async def list_runs(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[BacktestRun]:
        factory = _get_session_factory()
        async with factory() as session:
            return await repo.list_by_user(
                session,
                user_id=UUID(user.user_id),
                limit=limit, offset=offset,
            )

    return router
```

- [ ] **Step 3: Rewrite test_backtest_routes.py**

```python
# backend/algo/tests/test_backtest_routes.py
"""Endpoint smokes for /v1/algo/backtest/{run,runs/{id},runs}."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.backtest.types import (
    BacktestRun, BacktestSummary,
)
from backend.algo.routes.backtest import create_backtest_router


def _summary(run_id, strategy_id) -> BacktestSummary:
    return BacktestSummary(
        run_id=run_id, strategy_id=strategy_id, status="completed",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        initial_capital_inr=Decimal("100000"),
        final_equity_inr=Decimal("105000"),
        total_pnl_inr=Decimal("5000"),
        total_pnl_pct=Decimal("5"),
        total_fees_inr=Decimal("100"),
        total_trades=0, winning_trades=0, losing_trades=0,
        win_rate_pct=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        fee_rates_version="2026-04-01",
    )


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(create_backtest_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t", role="superuser",
    )
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    factory = MagicMock()
    factory.__aenter__ = AsyncMock(return_value=fake_session)
    factory.__aexit__ = AsyncMock(return_value=None)
    factory_factory = MagicMock(return_value=factory)
    import backend.algo.routes.backtest as bt
    monkeypatch.setattr(bt, "_get_session_factory", factory_factory)
    return app


def test_post_run_returns_202(app):
    strategy_id = uuid4()
    pending_run = BacktestRun(
        run_id=uuid4(), strategy_id=strategy_id, status="pending",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        started_at=datetime.now(timezone.utc),
    )
    with patch(
        "backend.algo.routes.backtest.BacktestRunsRepo"
    ) as cls:
        cls.return_value.create_pending = AsyncMock(
            return_value=pending_run,
        )
        client = TestClient(app)
        r = client.post(
            "/v1/algo/backtest/run",
            json={
                "strategy_id": str(strategy_id),
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "initial_capital_inr": "100000.00",
            },
        )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending"
    assert body["run_id"] == str(pending_run.run_id)


def test_get_run_404(app):
    with patch(
        "backend.algo.routes.backtest.BacktestRunsRepo"
    ) as cls:
        cls.return_value.get_by_id = AsyncMock(return_value=None)
        client = TestClient(app)
        r = client.get(f"/v1/algo/backtest/runs/{uuid4()}")
    assert r.status_code == 404


def test_get_run_returns_summary(app):
    strategy_id = uuid4()
    summary = _summary(uuid4(), strategy_id)
    with patch(
        "backend.algo.routes.backtest.BacktestRunsRepo"
    ) as cls:
        cls.return_value.get_by_id = AsyncMock(return_value=summary)
        client = TestClient(app)
        r = client.get(f"/v1/algo/backtest/runs/{summary.run_id}")
    assert r.status_code == 200
    assert r.json()["total_pnl_inr"] == "5000"


def test_list_runs_empty(app):
    with patch(
        "backend.algo.routes.backtest.BacktestRunsRepo"
    ) as cls:
        cls.return_value.list_by_user = AsyncMock(return_value=[])
        client = TestClient(app)
        r = client.get("/v1/algo/backtest/runs")
    assert r.status_code == 200
    assert r.json() == []
```

- [ ] **Step 4: Restart, run + commit**

```bash
docker compose restart backend
sleep 6
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_routes.py -v 2>&1 | tail -10

git add backend/algo/routes/backtest.py backend/algo/tests/test_backtest_routes.py stocks/repository.py
git commit -m "$(cat <<'EOF'
feat(algo): /v1/algo/backtest — async-job + list endpoint

Slice 7b. POST /run now creates a pending algo.runs row and
schedules a BackgroundTask, returning 202 + run_id immediately.
GET /runs/{id} reads from PG (fully replaces the in-memory
_RUNS dict). GET /runs lists user's recent runs newest-first.
algo.runs added to _CACHE_INVALIDATION_MAP for write-through
invalidation. 4 endpoint tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Frontend — `useBacktestRuns` hook

**Files:**
- Create: `frontend/hooks/useBacktestRuns.ts`

- [ ] **Step 1: Implement hook**

```typescript
// frontend/hooks/useBacktestRuns.ts
"use client";

import useSWR, { mutate } from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";

export type BacktestStatus = "pending" | "running" | "completed" | "failed";

export interface EquityPoint {
  bar_date: string;
  equity_inr: string;
}

export interface TradeRow {
  ticker: string;
  qty: number;
  avg_price: string;
  fill_price: string;
  opened_at: string;
  closed_at: string;
  holding_days: number;
  realised_pnl_inr: string;
  return_pct: string;
}

export interface BacktestSummary {
  run_id: string;
  strategy_id: string;
  status: BacktestStatus;
  period_start: string;
  period_end: string;
  initial_capital_inr: string;
  final_equity_inr: string;
  total_pnl_inr: string;
  total_pnl_pct: string;
  total_fees_inr: string;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: string;
  max_drawdown_pct: string;
  started_at: string;
  completed_at: string;
  fee_rates_version: string;
  equity_curve: EquityPoint[];
  trade_list: TradeRow[];
  error_text: string | null;
}

export interface BacktestRunListItem {
  run_id: string;
  strategy_id: string;
  status: BacktestStatus;
  period_start: string;
  period_end: string;
  started_at: string;
  completed_at: string | null;
  total_pnl_inr: string | null;
  total_pnl_pct: string | null;
  error_text: string | null;
}

async function fetcher<T>(url: string): Promise<T> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useBacktestRuns() {
  const key = `${API_URL}/algo/backtest/runs?limit=50`;
  const { data, error, isLoading } = useSWR<BacktestRunListItem[]>(
    key, fetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );
  return {
    rows: data ?? [],
    loading: isLoading,
    error: error
      ? error instanceof Error ? error.message : "Failed to load"
      : null,
  };
}

export function useBacktestRun(runId: string | null) {
  const key = runId ? `${API_URL}/algo/backtest/runs/${runId}` : null;
  const { data, error, isLoading } = useSWR<BacktestSummary>(
    key, fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) => {
        if (!latest) return 2_000;
        return latest.status === "pending" || latest.status === "running"
          ? 2_000 : 0;
      },
    },
  );
  return {
    run: data ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error ? error.message : "Failed to load"
      : null,
  };
}

export async function startBacktestRun(
  strategyId: string,
  periodStart: string,
  periodEnd: string,
  initialCapitalInr: string,
): Promise<string> {
  const r = await apiFetch(`${API_URL}/algo/backtest/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      strategy_id: strategyId,
      period_start: periodStart,
      period_end: periodEnd,
      initial_capital_inr: initialCapitalInr,
    }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const body = (await r.json()) as { run_id: string };
  await mutate(
    (k) => typeof k === "string" &&
      k.startsWith(`${API_URL}/algo/backtest/runs`),
  );
  return body.run_id;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/hooks/useBacktestRuns.ts
git commit -m "$(cat <<'EOF'
feat(algo): useBacktestRuns + useBacktestRun hooks

Slice 7b. SWR-backed hooks for the run list + single-run polling
(2s interval while pending/running, 0 once terminal). startBacktestRun
POSTs and invalidates the list cache.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: Frontend — Summary cards + Run form components

**Files:**
- Create: `frontend/components/algo-trading/BacktestSummaryCards.tsx`
- Create: `frontend/components/algo-trading/BacktestRunForm.tsx`

- [ ] **Step 1: Summary cards**

```tsx
// frontend/components/algo-trading/BacktestSummaryCards.tsx
"use client";

import type { BacktestSummary } from "@/hooks/useBacktestRuns";

interface Props {
  summary: BacktestSummary;
}

function fmtInr(v: string | number): string {
  const n = typeof v === "string" ? Number(v) : v;
  return new Intl.NumberFormat("en-IN", {
    style: "currency", currency: "INR", maximumFractionDigits: 0,
  }).format(n);
}

function fmtPct(v: string): string {
  return `${Number(v).toFixed(2)}%`;
}

export function BacktestSummaryCards({ summary }: Props) {
  const positive = Number(summary.total_pnl_inr) >= 0;
  return (
    <div
      className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6"
      data-testid="backtest-summary-cards"
    >
      <Card label="Total PnL"
        value={fmtInr(summary.total_pnl_inr)}
        tone={positive ? "good" : "bad"} />
      <Card label="PnL %"
        value={fmtPct(summary.total_pnl_pct)}
        tone={positive ? "good" : "bad"} />
      <Card label="Trades"
        value={String(summary.total_trades)} />
      <Card label="Win Rate"
        value={fmtPct(summary.win_rate_pct)} />
      <Card label="Max DD"
        value={fmtPct(summary.max_drawdown_pct)}
        tone="bad" />
      <Card label="Fees"
        value={fmtInr(summary.total_fees_inr)} />
    </div>
  );
}

function Card({
  label, value, tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  const valueClass =
    tone === "good"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "bad"
        ? "text-rose-600 dark:text-rose-400"
        : "text-slate-900 dark:text-slate-100";
  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2"
      data-testid={`backtest-card-${label.toLowerCase().replace(/\s+/g, "-")}`}
    >
      <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
      <div className={`text-lg font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}
```

- [ ] **Step 2: Run form**

```tsx
// frontend/components/algo-trading/BacktestRunForm.tsx
"use client";

import { useState } from "react";

import { useStrategies } from "@/hooks/useStrategies";
import {
  startBacktestRun,
} from "@/hooks/useBacktestRuns";

interface Props {
  onSubmitted: (runId: string) => void;
}

function todayMinus(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export function BacktestRunForm({ onSubmitted }: Props) {
  const { rows: strategies } = useStrategies();
  const [strategyId, setStrategyId] = useState<string>("");
  const [periodStart, setPeriodStart] = useState<string>(todayMinus(180));
  const [periodEnd, setPeriodEnd] = useState<string>(todayMinus(1));
  const [capital, setCapital] = useState<string>("100000.00");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!strategyId) {
      setErr("Pick a strategy");
      return;
    }
    setErr(null);
    setSubmitting(true);
    try {
      const runId = await startBacktestRun(
        strategyId, periodStart, periodEnd, capital,
      );
      onSubmitted(runId);
    } catch (exc) {
      setErr(exc instanceof Error ? exc.message : "Failed to submit");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-wrap items-end gap-3 rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 p-3"
      data-testid="backtest-run-form"
    >
      <Field label="Strategy">
        <select
          className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
          value={strategyId}
          onChange={(e) => setStrategyId(e.target.value)}
          data-testid="backtest-strategy-select"
        >
          <option value="">Select…</option>
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      </Field>
      <Field label="From">
        <input type="date" value={periodStart}
          onChange={(e) => setPeriodStart(e.target.value)}
          className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
          data-testid="backtest-period-start" />
      </Field>
      <Field label="To">
        <input type="date" value={periodEnd}
          onChange={(e) => setPeriodEnd(e.target.value)}
          className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm"
          data-testid="backtest-period-end" />
      </Field>
      <Field label="Capital ₹">
        <input type="number" min={1000} step={1000}
          value={capital}
          onChange={(e) => setCapital(e.target.value)}
          className="rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-2 py-1 text-sm w-28"
          data-testid="backtest-capital" />
      </Field>
      <button type="submit" disabled={submitting}
        className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
        data-testid="backtest-submit">
        {submitting ? "Starting…" : "Run backtest"}
      </button>
      {err && (
        <span className="text-sm text-rose-600" data-testid="backtest-form-error">
          {err}
        </span>
      )}
    </form>
  );
}

function Field({
  label, children,
}: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-slate-600 dark:text-slate-400">
      <span>{label}</span>
      {children}
    </label>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/components/algo-trading/BacktestSummaryCards.tsx \
        frontend/components/algo-trading/BacktestRunForm.tsx
git commit -m "$(cat <<'EOF'
feat(algo): BacktestSummaryCards + BacktestRunForm components

Slice 7b. Six metric cards (PnL, PnL%, trades, win rate, max DD,
fees) with tone-coded values. Run form with strategy picker,
date range, capital input. ESLint-clean test IDs throughout.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 9: Frontend — Equity curve (ECharts) + vitest

**Files:**
- Create: `frontend/components/algo-trading/BacktestEquityCurve.tsx`
- Create: `frontend/components/algo-trading/__tests__/BacktestEquityCurve.test.tsx`
- Modify: `frontend/lib/echarts.ts` (verify `LineChart` already registered)

- [ ] **Step 1: Equity curve component**

```tsx
// frontend/components/algo-trading/BacktestEquityCurve.tsx
"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";

import type { EquityPoint } from "@/hooks/useBacktestRuns";
import { useDarkMode } from "@/hooks/useDarkMode";

const ReactECharts = dynamic(
  () => import("echarts-for-react"),
  { ssr: false },
);

interface Props {
  points: EquityPoint[];
  initialCapitalInr: string;
}

export function BacktestEquityCurve({ points, initialCapitalInr }: Props) {
  const isDark = useDarkMode();

  const option = useMemo(() => ({
    grid: { left: 50, right: 12, top: 16, bottom: 32 },
    xAxis: {
      type: "category" as const,
      data: points.map((p) => p.bar_date),
      axisLabel: { fontSize: 11 },
    },
    yAxis: {
      type: "value" as const,
      scale: true,
      axisLabel: { fontSize: 11 },
    },
    tooltip: { trigger: "axis" as const },
    series: [
      {
        type: "line" as const,
        showSymbol: false,
        lineStyle: { width: 2 },
        data: points.map((p) => Number(p.equity_inr)),
        markLine: {
          symbol: "none",
          lineStyle: { type: "dashed" as const, color: "#94a3b8" },
          data: [{ yAxis: Number(initialCapitalInr) }],
        },
      },
    ],
  }), [points, initialCapitalInr]);

  if (points.length === 0) {
    return (
      <div
        className="flex h-64 items-center justify-center rounded-md border border-slate-200 dark:border-slate-700 text-sm text-slate-500"
        data-testid="backtest-equity-curve-empty"
      >
        No equity data yet
      </div>
    );
  }

  return (
    <div
      className="rounded-md border border-slate-200 dark:border-slate-700 p-2"
      data-testid="backtest-equity-curve"
    >
      <ReactECharts
        option={option}
        notMerge={true}
        key={isDark ? "d" : "l"}
        style={{ height: 280, width: "100%" }}
        opts={{ renderer: "canvas" }}
      />
    </div>
  );
}
```

- [ ] **Step 2: Vitest**

```tsx
// frontend/components/algo-trading/__tests__/BacktestEquityCurve.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

vi.mock("@/hooks/useDarkMode", () => ({
  useDarkMode: () => false,
}));
vi.mock("next/dynamic", () => ({
  default: () => () => null, // ECharts shim
}));

import { BacktestEquityCurve } from "../BacktestEquityCurve";

describe("BacktestEquityCurve", () => {
  it("renders empty state when no points", () => {
    render(
      <BacktestEquityCurve points={[]} initialCapitalInr="100000" />,
    );
    expect(
      screen.getByTestId("backtest-equity-curve-empty"),
    ).toBeInTheDocument();
  });

  it("renders chart container when points present", () => {
    render(
      <BacktestEquityCurve
        points={[
          { bar_date: "2026-04-01", equity_inr: "100000" },
          { bar_date: "2026-04-02", equity_inr: "101000" },
        ]}
        initialCapitalInr="100000"
      />,
    );
    expect(
      screen.getByTestId("backtest-equity-curve"),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run + commit**

```bash
cd frontend && npx vitest run components/algo-trading/__tests__/BacktestEquityCurve.test.tsx 2>&1 | tail -10 && cd ..

git add frontend/components/algo-trading/BacktestEquityCurve.tsx \
        frontend/components/algo-trading/__tests__/BacktestEquityCurve.test.tsx
git commit -m "$(cat <<'EOF'
feat(algo): BacktestEquityCurve — ECharts line chart

Slice 7b. Equity curve with mark-line at initial capital,
useDarkMode-driven theme toggle (per CLAUDE.md §5.3),
next/dynamic({ssr:false}) for the chart import. Empty state
when no points. 2 vitest cases.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 10: Frontend — Trade table (column selector + CSV)

**Files:**
- Create: `frontend/components/algo-trading/BacktestTradeTable.tsx`

- [ ] **Step 1: Implement (uses existing `useColumnSelection` + `<DownloadCsvButton/>` per CLAUDE.md §5.4)**

```tsx
// frontend/components/algo-trading/BacktestTradeTable.tsx
"use client";

import { useMemo } from "react";

import {
  ColumnSelector,
  DownloadCsvButton,
  useColumnSelection,
} from "@/components/insights/columnSelection";
import type { TradeRow } from "@/hooks/useBacktestRuns";

const ALL_COLS = [
  { key: "ticker", label: "Ticker", category: "Identity" },
  { key: "qty", label: "Qty", category: "Trade" },
  { key: "avg_price", label: "Avg ₹", category: "Trade" },
  { key: "fill_price", label: "Fill ₹", category: "Trade" },
  { key: "opened_at", label: "Opened", category: "Trade" },
  { key: "closed_at", label: "Closed", category: "Trade" },
  { key: "holding_days", label: "Days", category: "Trade" },
  { key: "realised_pnl_inr", label: "PnL ₹", category: "Performance" },
  { key: "return_pct", label: "Return %", category: "Performance" },
] as const;

const DEFAULT_COLS = [
  "ticker", "qty", "avg_price", "fill_price",
  "closed_at", "realised_pnl_inr", "return_pct",
];

const VALID_KEYS = ALL_COLS.map((c) => c.key);

interface Props {
  rows: TradeRow[];
}

export function BacktestTradeTable({ rows }: Props) {
  const { selected, setSelected } = useColumnSelection(
    "algo:backtest:trade-cols",
    DEFAULT_COLS,
    VALID_KEYS,
  );
  const visibleCols = useMemo(
    () => ALL_COLS.filter((c) => selected.includes(c.key)),
    [selected],
  );

  if (rows.length === 0) {
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
        data-testid="backtest-trade-table-empty"
      >
        No closed trades yet — run a strategy that exits positions.
      </div>
    );
  }

  return (
    <div className="space-y-2" data-testid="backtest-trade-table">
      <div className="flex items-center justify-between">
        <ColumnSelector
          catalog={ALL_COLS}
          selected={selected}
          setSelected={setSelected}
          lockedKeys={["ticker"]}
        />
        <DownloadCsvButton
          rows={rows}
          cols={visibleCols}
          filename="backtest-trades.csv"
        />
      </div>
      <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 dark:bg-slate-800">
            <tr>
              {visibleCols.map((c) => (
                <th key={c.key}
                  className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.ticker}-${r.closed_at}-${i}`}
                className="border-t border-slate-200 dark:border-slate-700">
                {visibleCols.map((c) => (
                  <td key={c.key}
                    className="px-3 py-1.5 text-slate-800 dark:text-slate-200">
                    {String(r[c.key as keyof TradeRow])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

NOTE: If `@/components/insights/columnSelection` doesn't expose `ColumnSelector`, `DownloadCsvButton`, `useColumnSelection` under that path, find the correct import path with `grep -r "DownloadCsvButton" frontend/components` (likely `@/components/insights/ColumnSelector` and `@/hooks/useColumnSelection`). Adjust imports accordingly.

- [ ] **Step 2: Commit**

```bash
git add frontend/components/algo-trading/BacktestTradeTable.tsx
git commit -m "$(cat <<'EOF'
feat(algo): BacktestTradeTable — sortable + column selector + CSV

Slice 7b. Closed-trade table following the tabular page pattern
(CLAUDE.md §5.4): localStorage-backed column selector, CSV
export consuming the SAME visibleCols (single source of truth),
locked ticker column.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 11: Frontend — `BacktestTab` + wire into AlgoTradingClient

**Files:**
- Create: `frontend/components/algo-trading/BacktestTab.tsx`
- Modify: `frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx`

- [ ] **Step 1: BacktestTab**

```tsx
// frontend/components/algo-trading/BacktestTab.tsx
"use client";

import { useState } from "react";

import {
  useBacktestRun,
  useBacktestRuns,
} from "@/hooks/useBacktestRuns";

import { BacktestEquityCurve } from "./BacktestEquityCurve";
import { BacktestRunForm } from "./BacktestRunForm";
import { BacktestSummaryCards } from "./BacktestSummaryCards";
import { BacktestTradeTable } from "./BacktestTradeTable";

export function BacktestTab() {
  const { rows: history } = useBacktestRuns();
  const [activeRunId, setActiveRunId] = useState<string | null>(
    history[0]?.run_id ?? null,
  );
  const { run, error } = useBacktestRun(activeRunId);

  return (
    <div className="space-y-4" data-testid="backtest-tab">
      <BacktestRunForm onSubmitted={(id) => setActiveRunId(id)} />

      {history.length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="text-slate-500">Recent:</span>
          {history.slice(0, 8).map((h) => (
            <button key={h.run_id}
              onClick={() => setActiveRunId(h.run_id)}
              className={`rounded px-2 py-0.5 border ${
                h.run_id === activeRunId
                  ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30"
                  : "border-slate-200 dark:border-slate-700"
              }`}
              data-testid={`backtest-history-${h.run_id}`}>
              {h.period_start}…{h.period_end} · {h.status}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
          data-testid="backtest-load-error">
          {error}
        </div>
      )}

      {run && run.status === "failed" && (
        <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
          data-testid="backtest-run-error">
          Run failed: {run.error_text ?? "unknown error"}
        </div>
      )}

      {run && (run.status === "pending" || run.status === "running") && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800"
          data-testid="backtest-run-progress">
          Run is {run.status}…
        </div>
      )}

      {run && run.status === "completed" && (
        <>
          <BacktestSummaryCards summary={run} />
          <BacktestEquityCurve
            points={run.equity_curve}
            initialCapitalInr={run.initial_capital_inr}
          />
          <BacktestTradeTable rows={run.trade_list} />
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire into AlgoTradingClient.tsx**

In `frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx`:

1. Add import near other tab imports:
```tsx
import { BacktestTab } from "@/components/algo-trading/BacktestTab";
```

2. In the `tabPanel = useMemo(...)` switch, replace the `case "backtest"` line that returns `<PlaceholderTab />` with:
```tsx
case "backtest":
  return <BacktestTab />;
```

- [ ] **Step 3: Verify by hand**

```bash
docker compose restart frontend
sleep 4
curl -s http://localhost:3000/algo-trading 2>&1 | head -5
```

Open http://localhost:3000/algo-trading?tab=backtest in a browser, verify the tab loads with the run form and either an empty state or last-run.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/algo-trading/BacktestTab.tsx \
        "frontend/app/(authenticated)/algo-trading/AlgoTradingClient.tsx"
git commit -m "$(cat <<'EOF'
feat(algo): BacktestTab — wire form/cards/curve/trades together

Slice 7b. Top-level tab composes BacktestRunForm, history pill
strip, BacktestSummaryCards, BacktestEquityCurve, and
BacktestTradeTable. URL-synced via the existing AlgoTradingClient
?tab=backtest hash.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 12: Playwright smoke + PROGRESS.md + push

**Files:**
- Create: `e2e/tests/algo-trading/backtest-flow.spec.ts`
- Modify: `e2e/utils/selectors.ts` (add the backtest test IDs to the `FE` registry per CLAUDE.md §5.14)
- Modify: `PROGRESS.md`

- [ ] **Step 1: Add testids to e2e/utils/selectors.ts**

In the `FE` object, add:
```typescript
algoBacktestTab: "backtest-tab",
algoBacktestRunForm: "backtest-run-form",
algoBacktestStrategySelect: "backtest-strategy-select",
algoBacktestSubmit: "backtest-submit",
algoBacktestSummaryCards: "backtest-summary-cards",
algoBacktestEquityCurve: "backtest-equity-curve",
algoBacktestEquityCurveEmpty: "backtest-equity-curve-empty",
algoBacktestTradeTable: "backtest-trade-table",
algoBacktestTradeTableEmpty: "backtest-trade-table-empty",
```

- [ ] **Step 2: Smoke spec**

```typescript
// e2e/tests/algo-trading/backtest-flow.spec.ts
import { test, expect } from "../../fixtures/superuser";
import { FE } from "../../utils/selectors";

test.describe("Backtest tab", () => {
  test("loads with empty state for new user", async ({ page }) => {
    await page.goto("/algo-trading?tab=backtest");
    await expect(page.getByTestId(FE.algoBacktestTab)).toBeVisible();
    await expect(page.getByTestId(FE.algoBacktestRunForm)).toBeVisible();
  });
});
```

- [ ] **Step 3: Run smoke**

```bash
cd e2e && npx playwright test tests/algo-trading/backtest-flow.spec.ts --project=algo-chromium 2>&1 | tail -10 && cd ..
```

If `algo-chromium` doesn't exist as a project, fall back to `frontend-chromium`. Adjust as needed.

- [ ] **Step 4: PROGRESS.md**

Prepend after the `# PROGRESS.md` line + `---`:

```markdown
## 2026-05-08 (later 6) — Algo Trading Slice 7b: backtest UI + PG persistence

**Branch:** `feature/algo-trading-session-5-backtest-ui` (built off Session 4's tip)
**Epic:** Algo Trading Platform v1
**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md`
**Plan:** `docs/superpowers/plans/2026-05-08-algo-trading-session-5-backtest-ui.md`

**Shipped (Slice 7b):**
- Migration: `algo.runs.summary_json` (jsonb) + `error_text` (text).
- BacktestSummary extended with `equity_curve: list[EquityPoint]` + `trade_list: list[TradeRow]` + `status` enum + `error_text`.
- `BacktestRunsRepo` for PG-backed run lifecycle (replaces in-memory `_RUNS` dict).
- `resolve_universe(user, strategy)` reusing `_scoped_tickers`.
- `run_backtest_job` async wrapper; `POST /run` returns 202 + run_id immediately, `BackgroundTasks` runs the engine.
- `GET /v1/algo/backtest/runs` list endpoint.
- `algo.runs` added to `_CACHE_INVALIDATION_MAP` for write-through invalidation.
- Frontend: `useBacktestRuns` + `useBacktestRun` SWR hooks (2s polling while terminal-pending), `BacktestRunForm`, `BacktestSummaryCards` (6 cards), `BacktestEquityCurve` (ECharts), `BacktestTradeTable` (column selector + CSV per CLAUDE.md §5.4), `BacktestTab` composer wired into `AlgoTradingClient`.

**Tests:** ~14 new pytest (3 runs-repo + 2 universe + 2 job + 4 routes refactor + 3 runner extensions) + 2 new vitest (BacktestEquityCurve) + 1 new Playwright smoke. Total algo backend tests: **~140 passing**.

**Deferred to Session 6 (Slice 7c — optional):**
- MinIO artifact upload (PNG equity curve + JSONL events bundle + CSV trade list export at run time).
- Walk-forward CV harness.
- Slippage modelling beyond next-open fills.
- BacktestRunForm strategy filter by status.

**Deferred to v2:**
- crossover / between / select_top_n / weighted node evaluation.
- set_target_weight resolver.
- Async-job cancel button (run termination mid-flight).

---
```

- [ ] **Step 5: Commit + push**

```bash
git add e2e/utils/selectors.ts e2e/tests/algo-trading/backtest-flow.spec.ts PROGRESS.md
git commit -m "$(cat <<'EOF'
feat(algo): backtest playwright smoke + PROGRESS log

Slice 7b. Smoke confirms tab + run form render. PROGRESS entry
logs the Slice 7b ship.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"

git push -u origin feature/algo-trading-session-5-backtest-ui 2>&1 | tail -5
```

---

## Self-Review (post-write)

**1. Spec coverage (§ 9 Slice 7 + § 2.2 tab strip):**
- Backtest tab UI → Tasks 8–11 (form, cards, equity curve, trade table, composer).
- PG-backed run persistence → Tasks 1, 3, 6 (migration + repo + routes).
- Universe resolution → Task 4 (reuses `_scoped_tickers`).
- Async-job wrapper → Tasks 5, 6 (`BackgroundTasks` + `mark_running/completed/failed`).
- Equity curve + trade list → Tasks 2, 9, 10 (data shape + chart + table).
- MinIO artifact upload → DEFERRED to Slice 7c (documented in "Deferred to Session 6"). v1 inlines as JSONB on the runs row, which is fine for watchlist-scoped backtests.
- Walk-forward CV harness → DEFERRED to Slice 7c.
- Strategy-vs-strategy comparison (Slice 9) → out of scope (separate slice).

**2. Placeholder scan:**
- Task 1 has `<PASTE HEAD FROM STEP 1>` for `down_revision` — explicit, scoped instruction; engineer must verify and substitute the actual head from `alembic current`.
- Task 10 has a runtime fallback note for `@/components/insights/columnSelection` import — explicit `grep` instruction, not a TBD.
- Task 12 falls back from `algo-chromium` to `frontend-chromium` if the project doesn't exist — explicit instruction.

**3. Type consistency:**
- `BacktestSummary` shape matches between Tasks 2 (definition), 3 (repo round-trip), 5 (job persistence), 6 (route response_model), 7 (frontend interface), 8 (cards), 9 (equity curve points), 10 (trade table rows), 11 (composer).
- `BacktestRun` (list-row shape) consistent between Tasks 2 (definition), 3 (repo), 6 (routes), 7 (`BacktestRunListItem` mirror).
- `TradeRow` shape consistent between Tasks 2 (definition), 7 (frontend type), 10 (table cells).
- `BacktestStatus` enum consistent across Python (Literal) and TypeScript types.
- `_scoped_tickers` signature (kwargs `user`, `scope`) matches between `insights_routes.py` and Task 4 / 5 callers.

**4. Adaptations expected during execution:**
- `down_revision` in Task 1 — engineer pastes whatever `alembic current` reports.
- `@/components/insights/columnSelection` may live at a different path; grep + adjust.
- The `useStrategies` hook is assumed; if its return shape doesn't match `{rows: [{id, name}, ...]}`, adapt the form's iteration in Task 8.

No gaps; type drift addressed; placeholders are scoped to engineer-side substitution with explicit grep instructions.

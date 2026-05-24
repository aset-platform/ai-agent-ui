"""Async CRUD for algo.runs.

Wraps SQLAlchemy core inserts/selects with the canonical
session pattern from ``backend/algo/strategy/repo.py``. Returns
plain dicts (or ``BacktestRun``/``BacktestSummary`` Pydantic
models) — never ORM rows.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.algo.backtest.types import (
    BacktestRun,
    BacktestSummary,
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
        mode: str = "backtest",
        parent_walkforward_id: UUID | None = None,
        parent_sweep_id: UUID | None = None,
        window_start: date | None = None,
        window_end: date | None = None,
    ) -> BacktestRun:
        run_id = uuid4()
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                "INSERT INTO algo.runs ("
                "id, strategy_id, user_id, mode, status, "
                "period_start, period_end, started_at, "
                "parent_walkforward_id, parent_sweep_id, "
                "window_start, window_end"
                ") VALUES ("
                ":id, :sid, :uid, :mode, 'pending', "
                ":ps, :pe, :sa, "
                ":pwf_id, :psw_id, :ws, :we)"
            ),
            {
                "id": run_id, "sid": strategy_id, "uid": user_id,
                "mode": mode,
                "ps": period_start, "pe": period_end, "sa": now,
                "pwf_id": parent_walkforward_id,
                "psw_id": parent_sweep_id,
                "ws": window_start,
                "we": window_end,
            },
        )
        return BacktestRun(
            run_id=run_id, strategy_id=strategy_id,
            status="pending",
            period_start=period_start, period_end=period_end,
            started_at=now,
        )

    async def create_pending_sweep(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        base_strategy_id: UUID,
        period_start: date,
        period_end: date,
    ) -> BacktestRun:
        """Create the sweep parent row (mode='sweep').

        Strategy FK points at the BASE strategy. Variants
        are tracked by parent_sweep_id on their child
        walkforward rows.
        """
        run_id = uuid4()
        now = datetime.now(timezone.utc)
        await session.execute(
            text(
                "INSERT INTO algo.runs ("
                "id, strategy_id, user_id, mode, status, "
                "period_start, period_end, started_at"
                ") VALUES ("
                ":id, :sid, :uid, 'sweep', 'pending', "
                ":ps, :pe, :sa)"
            ),
            {
                "id": run_id, "sid": base_strategy_id,
                "uid": user_id,
                "ps": period_start, "pe": period_end,
                "sa": now,
            },
        )
        return BacktestRun(
            run_id=run_id,
            strategy_id=base_strategy_id,
            status="pending",
            period_start=period_start,
            period_end=period_end,
            started_at=now,
        )

    async def list_children_of_sweep(
        self,
        session: AsyncSession,
        *,
        sweep_run_id: UUID,
    ) -> list[dict[str, Any]]:
        """Variant walkforward rows owned by a sweep."""
        result = await session.execute(
            text(
                "SELECT id, status, summary_json, "
                "error_text "
                "FROM algo.runs "
                "WHERE parent_sweep_id = :sid "
                "  AND mode = 'walkforward' "
                "ORDER BY started_at"
            ),
            {"sid": sweep_run_id},
        )
        return [dict(r) for r in result.mappings().all()]

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

    async def mark_stale_running_as_crashed(
        self,
        session: AsyncSession,
        *,
        threshold_seconds: int = 3600,
    ) -> list[UUID]:
        """Mark any ``status='running'`` rows older than the
        threshold with no ``completed_at`` as ``crashed``.

        Zombie ledger entries appear when the backing asyncio task
        dies (uvicorn reload, container restart, OS kill) without a
        clean shutdown path to call ``mark_completed`` /
        ``mark_failed``. The DB row keeps saying "running" forever,
        which (a) misleads operators, and (b) bogs down postback
        reconciliation — every postback walks live_orders_in_flight
        across all live "running" rows for the user. With several
        zombies the scan becomes seconds-slow.

        ASETPLTFRM-379 — called once from ``_run_startup_hooks``
        on backend boot.

        Args:
            threshold_seconds: a run must have started this many
                seconds ago to be marked crashed. Tolerates a
                normal uvicorn reload window. Default 1 hour.

        Returns:
            list of run ids that were transitioned. Empty list
            means no zombies were found.
        """
        result = await session.execute(
            text(
                "UPDATE algo.runs SET "
                "status = 'crashed', "
                "completed_at = NOW() "
                "WHERE status = 'running' "
                "  AND completed_at IS NULL "
                "  AND started_at "
                "    < NOW() - make_interval(secs => :thr) "
                "RETURNING id"
            ),
            {"thr": threshold_seconds},
        )
        ids: list[UUID] = [row[0] for row in result]
        if ids:
            await session.commit()
        return ids

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
                "summary_json = CAST(:sj AS jsonb) "
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
            initial_capital_inr=Decimal("0"),
            final_equity_inr=Decimal("0"),
            total_pnl_inr=Decimal("0"),
            total_pnl_pct=Decimal("0"),
            total_fees_inr=Decimal("0"),
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate_pct=Decimal("0"),
            max_drawdown_pct=Decimal("0"),
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
                    Decimal(str(sj["total_pnl_inr"]))
                    if sj else None
                ),
                total_pnl_pct=(
                    Decimal(str(sj["total_pnl_pct"]))
                    if sj else None
                ),
                error_text=r["error_text"],
            ))
        return rows

    async def list_children_by_walkforward(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        walkforward_run_id: UUID,
    ) -> list[BacktestRun]:
        """Return child window runs for a walk-forward parent."""
        result = await session.execute(
            text(
                "SELECT id, strategy_id, status, period_start, "
                "period_end, started_at, completed_at, "
                "summary_json, error_text, "
                "window_start, window_end "
                "FROM algo.runs "
                "WHERE user_id = :uid "
                "  AND parent_walkforward_id = :pwf_id "
                "  AND mode = 'backtest' "
                "ORDER BY window_start ASC NULLS LAST"
            ),
            {"uid": user_id, "pwf_id": walkforward_run_id},
        )
        rows: list[BacktestRun] = []
        for r in result.mappings().all():
            sj: dict[str, Any] | None = r["summary_json"]
            rows.append(BacktestRun(
                run_id=r["id"],
                strategy_id=r["strategy_id"],
                status=r["status"],
                period_start=r["period_start"],
                period_end=r["period_end"],
                started_at=r["started_at"],
                completed_at=r["completed_at"],
                total_pnl_inr=(
                    Decimal(str(sj["total_pnl_inr"]))
                    if sj else None
                ),
                total_pnl_pct=(
                    Decimal(str(sj["total_pnl_pct"]))
                    if sj else None
                ),
                error_text=r["error_text"],
            ))
        return rows

    async def get_walkforward_by_id(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        run_id: UUID,
    ) -> BacktestSummary | None:
        """Fetch the parent walk-forward aggregate row."""
        result = await session.execute(
            text(
                "SELECT id, strategy_id, status, period_start, "
                "period_end, started_at, completed_at, "
                "summary_json, error_text "
                "FROM algo.runs "
                "WHERE id = :id AND user_id = :uid "
                "  AND mode = 'walkforward'"
            ),
            {"id": run_id, "uid": user_id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        if row["summary_json"] is not None:
            return BacktestSummary.model_validate(
                row["summary_json"],
            )
        return BacktestSummary(
            run_id=row["id"],
            strategy_id=row["strategy_id"],
            status=row["status"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            initial_capital_inr=Decimal("0"),
            final_equity_inr=Decimal("0"),
            total_pnl_inr=Decimal("0"),
            total_pnl_pct=Decimal("0"),
            total_fees_inr=Decimal("0"),
            total_trades=0, winning_trades=0, losing_trades=0,
            win_rate_pct=Decimal("0"),
            max_drawdown_pct=Decimal("0"),
            started_at=row["started_at"],
            completed_at=(
                row["completed_at"] or row["started_at"]
            ),
            fee_rates_version="n/a",
            equity_curve=[],
            trade_list=[],
            error_text=row["error_text"],
        )

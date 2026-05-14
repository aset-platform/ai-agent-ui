"""Per-strategy runtime + open-position checks used by the
promotion workflow.

These helpers are pure read-side: they never mutate state. The
Strategies tab uses them to drive the "edit will affect runtime"
banner and the "open positions" warning on the promote / bypass
dialogs.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class RuntimeState:
    """Aggregate runtime + position summary for a strategy."""

    active_modes: list[str]   # subset of ('paper', 'live')
    open_position_count: int
    open_position_modes: list[str]  # which sources hold open pos

    @property
    def has_active_runtime(self) -> bool:
        return len(self.active_modes) > 0


async def get_runtime_state(
    session: AsyncSession,
    *,
    strategy_id: UUID,
    user_id: UUID,
) -> RuntimeState:
    """Snapshot of paper / live runtimes + open positions.

    Runtime detection: ``algo.runs.status='running'`` filtered to
    ``mode IN ('paper','live')``. Open positions: rows in
    ``algo.positions`` joined to those runs where ``closed_at IS
    NULL``. Both are scoped to ``user_id`` so cross-user noise
    can't leak into the warning banner.
    """
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT mode "
                "FROM algo.runs "
                "WHERE strategy_id = :sid AND user_id = :uid "
                "  AND status = 'running' "
                "  AND mode IN ('paper','live')"
            ),
            {"sid": str(strategy_id), "uid": str(user_id)},
        )
    ).all()
    active_modes = sorted({r[0] for r in rows})

    pos_rows = (
        await session.execute(
            text(
                "SELECT p.source::text AS source, COUNT(*) AS n "
                "FROM algo.positions p "
                "JOIN algo.runs r ON r.id = p.run_id "
                "WHERE r.strategy_id = :sid AND r.user_id = :uid "
                "  AND p.closed_at IS NULL "
                "GROUP BY p.source"
            ),
            {"sid": str(strategy_id), "uid": str(user_id)},
        )
    ).mappings().all()
    open_count = sum(int(r["n"]) for r in pos_rows)
    open_modes = sorted({r["source"] for r in pos_rows})

    return RuntimeState(
        active_modes=active_modes,
        open_position_count=open_count,
        open_position_modes=open_modes,
    )

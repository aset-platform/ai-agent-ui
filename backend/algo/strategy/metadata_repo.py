"""Async PG repo for ``algo.strategy_metadata`` (REGIME-3).

Holds per-strategy regime binding (``applicable_regimes``), the
optional ``expected_edge``, and a free-form ``description``.  This
data lives outside the AST so the JSON payload stays minimal and so
metadata edits don't bump the strategy AST schema or trigger
revalidation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class StrategyMetadata:
    """Mirror of ``algo.strategy_metadata``.

    ``applicable_regimes`` defaults to all-3 (regime-agnostic).  The
    selector + RegimeChangeBanner treat that default as "no filter".
    """
    applicable_regimes: list[str] = field(
        default_factory=lambda: ["bull", "sideways", "bear"],
    )
    expected_edge: Decimal | float | None = None
    description: str = ""


async def upsert_metadata(
    session: AsyncSession,
    strategy_id: UUID,
    md: StrategyMetadata,
) -> None:
    """Insert or replace the metadata row for a strategy."""
    await session.execute(
        text(
            "INSERT INTO algo.strategy_metadata "
            "(strategy_id, applicable_regimes, expected_edge, "
            " description, updated_at) "
            "VALUES (:sid, :regimes, :edge, :descr, NOW()) "
            "ON CONFLICT (strategy_id) DO UPDATE SET "
            "applicable_regimes = EXCLUDED.applicable_regimes, "
            "expected_edge = EXCLUDED.expected_edge, "
            "description = EXCLUDED.description, "
            "updated_at = NOW()"
        ),
        {
            "sid": str(strategy_id),
            "regimes": list(md.applicable_regimes),
            "edge": (
                float(md.expected_edge)
                if md.expected_edge is not None else None
            ),
            "descr": md.description,
        },
    )


async def get_metadata(
    session: AsyncSession, strategy_id: UUID,
) -> StrategyMetadata | None:
    """Fetch one metadata row.  Returns ``None`` on miss."""
    row = (await session.execute(
        text(
            "SELECT applicable_regimes, expected_edge, description "
            "FROM algo.strategy_metadata WHERE strategy_id = :sid"
        ),
        {"sid": str(strategy_id)},
    )).mappings().first()
    if row is None:
        return None
    return StrategyMetadata(
        applicable_regimes=list(row["applicable_regimes"]),
        expected_edge=row["expected_edge"],
        description=(row["description"] or ""),
    )


async def delete_metadata(
    session: AsyncSession, strategy_id: UUID,
) -> None:
    """Idempotent delete.  Returns nothing — caller doesn't need
    rowcount."""
    await session.execute(
        text(
            "DELETE FROM algo.strategy_metadata "
            "WHERE strategy_id = :sid"
        ),
        {"sid": str(strategy_id)},
    )

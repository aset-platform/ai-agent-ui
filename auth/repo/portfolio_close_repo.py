"""Async PG repo for portfolio_closed_positions (realized P&L)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models.portfolio_close import PortfolioClosedPosition


def _to_dict(m: PortfolioClosedPosition) -> dict[str, Any]:
    # Numeric columns come back as Decimal; JSON-encode them as floats so
    # the API matches the frontend's `number` contract (Decimal serializes
    # to a JSON string via pydantic and breaks `.toFixed()` in the UI).
    out: dict[str, Any] = {}
    for c in m.__table__.columns:
        val = getattr(m, c.name)
        out[c.name] = float(val) if isinstance(val, Decimal) else val
    return out


def _coerce_date(v: Any) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


async def add_closed_position(
    session: AsyncSession, data: dict[str, Any]
) -> dict[str, Any]:
    """Insert a closed-position row and return it as a dict."""
    row = PortfolioClosedPosition(
        id=data.get("id") or str(uuid.uuid4()),
        user_id=data["user_id"],
        ticker=data["ticker"],
        quantity=data["quantity"],
        buy_price=data["buy_price"],
        sell_price=data["sell_price"],
        buy_date=_coerce_date(data.get("buy_date")),
        sell_date=_coerce_date(data["sell_date"]),
        fees=data.get("fees", 0),
        realized_pnl=data["realized_pnl"],
        realized_pnl_pct=data.get("realized_pnl_pct"),
        currency=data["currency"],
        market=data["market"],
        sell_transaction_id=data.get("sell_transaction_id"),
        notes=data.get("notes"),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_dict(row)


async def list_closed_positions(
    session: AsyncSession, user_id: str
) -> list[dict[str, Any]]:
    """Return closed positions for a user, newest sell first."""
    result = await session.execute(
        select(PortfolioClosedPosition)
        .where(PortfolioClosedPosition.user_id == user_id)
        .order_by(
            PortfolioClosedPosition.sell_date.desc(),
            PortfolioClosedPosition.created_at.desc(),
        )
    )
    return [_to_dict(m) for m in result.scalars().all()]

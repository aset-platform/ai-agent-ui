"""Realized-P&L record for a closed (sold) portfolio position."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class PortfolioClosedPosition(Base):
    __tablename__ = "portfolio_closed_positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[float] = mapped_column(
        Numeric(18, 4), nullable=False
    )
    buy_price: Mapped[float] = mapped_column(
        Numeric(18, 4), nullable=False
    )
    sell_price: Mapped[float] = mapped_column(
        Numeric(18, 4), nullable=False
    )
    buy_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    sell_date: Mapped[date] = mapped_column(Date, nullable=False)
    fees: Mapped[float] = mapped_column(
        Numeric(18, 4), nullable=False, server_default="0"
    )
    realized_pnl: Mapped[float] = mapped_column(
        Numeric(18, 4), nullable=False
    )
    realized_pnl_pct: Mapped[float | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    sell_transaction_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        nullable=False,
    )

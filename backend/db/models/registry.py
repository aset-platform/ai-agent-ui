"""StockRegistry ORM model — ticker fetch metadata."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class StockRegistry(Base):
    __tablename__ = "stock_registry"

    ticker: Mapped[str] = mapped_column(
        String(20), primary_key=True,
    )
    last_fetch_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True,
    )
    total_rows: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    date_range_start: Mapped[date | None] = mapped_column(
        Date, nullable=True,
    )
    date_range_end: Mapped[date | None] = mapped_column(
        Date, nullable=True,
    )
    market: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

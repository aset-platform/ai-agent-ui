"""Single-row market index cache for Nifty 50 + Sensex."""
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class MarketIndex(Base):
    __tablename__ = "market_indices"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_market_indices_single"),
        {"schema": "stocks", "extend_existing": True},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, default=1,
    )
    nifty_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
    )
    sensex_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
    )
    market_state: Mapped[str] = mapped_column(
        String(10), nullable=False,
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

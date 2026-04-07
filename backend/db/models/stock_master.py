"""StockMaster ORM model — canonical stock/security master."""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class StockMaster(Base):
    __tablename__ = "stock_master"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    symbol: Mapped[str] = mapped_column(
        String(30), unique=True, nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    isin: Mapped[str | None] = mapped_column(
        String(12), unique=True, nullable=True, index=True,
    )
    exchange: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True,
    )
    yf_ticker: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True,
    )
    nse_symbol: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
    )
    sector: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    industry: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )
    market_cap: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
    )
    currency: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="INR",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true",
        index=True,
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

    tags = relationship(
        "StockTag", back_populates="stock",
        cascade="all, delete-orphan",
    )

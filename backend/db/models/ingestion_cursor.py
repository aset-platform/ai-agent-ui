"""IngestionCursor ORM model — resumable batch cursor."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class IngestionCursor(Base):
    __tablename__ = "ingestion_cursor"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    cursor_name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False,
    )
    total_tickers: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    last_processed_id: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    batch_size: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="50",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending",
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

    skipped = relationship(
        "IngestionSkipped",
        back_populates="cursor",
        cascade="all, delete-orphan",
    )

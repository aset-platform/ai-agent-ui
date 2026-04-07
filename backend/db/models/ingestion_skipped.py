"""IngestionSkipped ORM model — failed ingestion records."""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class IngestionSkipped(Base):
    __tablename__ = "ingestion_skipped"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    cursor_name: Mapped[str] = mapped_column(
        String(100),
        ForeignKey(
            "ingestion_cursor.cursor_name",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(
        String(30), nullable=False,
    )
    job_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )
    error_category: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1",
    )
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    last_attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    cursor = relationship(
        "IngestionCursor", back_populates="skipped",
    )

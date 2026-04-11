"""ScheduledJob ORM model — cron job definitions."""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    job_id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
    )
    name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False,
    )
    job_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
    )
    cron_days: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    cron_time: Mapped[str | None] = mapped_column(
        String(10), nullable=True,
    )
    cron_dates: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    scope: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true",
    )
    force: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
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

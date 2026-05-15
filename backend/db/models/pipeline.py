"""Pipeline + PipelineStep ORM models — job chains."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from backend.db.base import Base


class Pipeline(Base):
    __tablename__ = "pipelines"

    pipeline_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    cron_days: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    cron_time: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )
    cron_dates: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
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

    steps: Mapped[list["PipelineStep"]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineStep.step_order",
    )


class PipelineStep(Base):
    __tablename__ = "pipeline_steps"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_id",
            "step_order",
            name="uq_pipeline_step_order",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    pipeline_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "pipelines.pipeline_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    step_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    job_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    # Free-form per-step args (e.g. iceberg_maintenance reads
    # ``payload["tables"]`` to scope the run). Empty dict by
    # default — wrappers that ignore ``payload`` keep their
    # existing behaviour. ASETPLTFRM-418.
    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )

    pipeline: Mapped["Pipeline"] = relationship(
        back_populates="steps",
    )

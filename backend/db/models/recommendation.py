"""Recommendation ORM models — portfolio health + stock recs."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    __table_args__ = (
        Index(
            "ix_rec_runs_user_date",
            "user_id", "run_date",
        ),
        {"schema": "stocks", "extend_existing": True},
    )

    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False,
    )
    run_date: Mapped[date] = mapped_column(
        Date, nullable=False,
    )
    run_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    portfolio_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False,
    )
    health_score: Mapped[float] = mapped_column(
        Float, nullable=False,
    )
    health_label: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    health_assessment: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    candidates_scanned: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    candidates_passed: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    llm_model: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    llm_tokens_used: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    duration_secs: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recs_run_id", "run_id"),
        Index("ix_recs_ticker_status", "ticker", "status"),
        Index("ix_recs_status_created", "status", "created_at"),
        {"schema": "stocks", "extend_existing": True},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey(
            "stocks.recommendation_runs.run_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    tier: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    category: Mapped[str] = mapped_column(
        String(25), nullable=False,
    )
    ticker: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
    )
    action: Mapped[str] = mapped_column(
        String(15), nullable=False,
    )
    severity: Mapped[str] = mapped_column(
        String(10), nullable=False,
    )
    rationale: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    expected_impact: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    data_signals: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False,
    )
    price_at_rec: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    target_price: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    expected_return_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    index_tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(15), nullable=False,
        server_default=text("'active'"),
    )
    acted_on_date: Mapped[date | None] = mapped_column(
        Date, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    run: Mapped["RecommendationRun"] = relationship(
        back_populates="recommendations",
    )
    outcomes: Mapped[list["RecommendationOutcome"]] = relationship(
        back_populates="recommendation",
        cascade="all, delete-orphan",
    )


class RecommendationOutcome(Base):
    __tablename__ = "recommendation_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "recommendation_id", "days_elapsed",
            name="uq_rec_outcome_rec_days",
        ),
        {"schema": "stocks", "extend_existing": True},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    recommendation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey(
            "stocks.recommendations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    check_date: Mapped[date] = mapped_column(
        Date, nullable=False,
    )
    days_elapsed: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    actual_price: Mapped[float] = mapped_column(
        Float, nullable=False,
    )
    return_pct: Mapped[float] = mapped_column(
        Float, nullable=False,
    )
    benchmark_return_pct: Mapped[float] = mapped_column(
        Float, nullable=False,
    )
    excess_return_pct: Mapped[float] = mapped_column(
        Float, nullable=False,
    )
    outcome_label: Mapped[str] = mapped_column(
        String(15), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    recommendation: Mapped["Recommendation"] = relationship(
        back_populates="outcomes",
    )

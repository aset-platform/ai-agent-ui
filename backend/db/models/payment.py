"""PaymentTransaction ORM model — webhook events + reconciliation."""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from backend.db.base import Base


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    transaction_id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.user_id"),
        nullable=False,
    )
    gateway: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False,
    )
    gateway_event_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    customer_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    amount: Mapped[float | None] = mapped_column(
        Float, nullable=True,
    )
    currency: Mapped[str | None] = mapped_column(
        String(10), nullable=True,
    )
    tier_before: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    tier_after: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False,
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user = relationship(
        "User", back_populates="payment_transactions",
    )

    __table_args__ = (
        Index(
            "ix_payment_gateway_event",
            "gateway", "gateway_event_id",
        ),
    )

"""User ORM model — maps to auth.users Iceberg table."""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False,
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    full_name: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="user",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true",
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
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    password_reset_token: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    password_reset_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    oauth_provider: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    oauth_sub: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    profile_picture_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    page_permissions: Mapped[str | None] = mapped_column(
        String(1000), nullable=True,
    )
    subscription_tier: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    subscription_status: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )
    razorpay_customer_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    razorpay_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    monthly_usage_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    usage_month: Mapped[str | None] = mapped_column(
        String(7), nullable=True,
    )
    subscription_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    subscription_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    chat_request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    byo_monthly_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="100",
    )

    tickers = relationship(
        "UserTicker", back_populates="user",
        cascade="all, delete-orphan",
    )
    payment_transactions = relationship(
        "PaymentTransaction", back_populates="user",
    )
    llm_keys = relationship(
        "UserLLMKey", back_populates="user",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_user_oauth", "oauth_provider", "oauth_sub"),
        Index("ix_user_tier", "subscription_tier"),
    )

"""UserLLMKey ORM — BYO provider keys for chat agent."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class UserLLMKey(Base):
    __tablename__ = "user_llm_keys"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )
    encrypted_key: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False,
    )
    label: Mapped[str | None] = mapped_column(
        String(120), nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    request_count_30d: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
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

    user = relationship("User", back_populates="llm_keys")

    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", name="uq_user_llm_keys_user_provider",
        ),
        Index("ix_user_llm_keys_user_id", "user_id"),
    )

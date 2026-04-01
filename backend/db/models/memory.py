"""UserMemory ORM model — pgvector-backed per-user memory."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class UserMemory(Base):
    """Per-user memory entry with vector embedding.

    Stores session summaries, structured facts, and
    user preferences alongside a 768-dim embedding
    for cosine-similarity retrieval via pgvector.
    """

    __tablename__ = "user_memories"

    memory_id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "users.user_id", ondelete="CASCADE",
        ),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(
        String(100), nullable=False,
    )
    memory_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    structured: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
    )
    embedding = mapped_column(
        Vector(768), nullable=False,
    )
    turn_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        Index("ix_memory_user", "user_id"),
        Index(
            "ix_memory_session",
            "user_id",
            "session_id",
        ),
        {"extend_existing": True},
    )

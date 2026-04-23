"""SentimentDormant ORM model — per-ticker headline-fetch dormancy.

Tracks tickers whose news-source fetches returned zero headlines so
the batch sentiment job can stop hitting Yahoo/Google for them and
fall straight through to market_fallback. Mirrors the
``ingestion_skipped`` upsert + retry pattern.

Cooldown schedule (capped exponential):
    consecutive_empty=1 → next_retry +2 days
    consecutive_empty=2 → +4 days
    consecutive_empty=3 → +8 days
    consecutive_empty=4 → +16 days
    consecutive_empty>=5 → +30 days (cap)

A successful fetch resets ``consecutive_empty`` to 0 and
``next_retry_at`` to NULL.
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class SentimentDormant(Base):
    __tablename__ = "sentiment_dormant"

    ticker: Mapped[str] = mapped_column(
        String(30), primary_key=True,
    )
    consecutive_empty: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_headline_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    last_seen_headlines_at: Mapped[datetime | None] = (
        mapped_column(
            DateTime(timezone=True), nullable=True,
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_sentiment_dormant_next_retry_at",
            "next_retry_at",
        ),
    )

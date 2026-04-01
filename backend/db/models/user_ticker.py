"""UserTicker ORM model — user watchlist link/unlink."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class UserTicker(Base):
    __tablename__ = "user_tickers"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ticker: Mapped[str] = mapped_column(
        String(20), primary_key=True,
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="manual",
    )

    user = relationship("User", back_populates="tickers")

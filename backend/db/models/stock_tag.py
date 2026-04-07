"""StockTag ORM model — flexible tagging for stocks."""
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class StockTag(Base):
    __tablename__ = "stock_tags"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "tag", "added_at",
            name="uq_stock_tag_added",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    stock_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stock_master.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag: Mapped[str] = mapped_column(
        String(50), nullable=False,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    stock = relationship(
        "StockMaster", back_populates="tags",
    )

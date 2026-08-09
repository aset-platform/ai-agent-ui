"""Single-row market index cache for Nifty 50 + Sensex."""
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base

# Real Postgres (prod) gets JSONB unchanged; any other dialect (the
# in-memory SQLite engine tests/backend/conftest.py + test_pg_models.py
# use for Base.metadata.create_all) falls back to generic JSON instead
# of failing compilation — JSONB is Postgres-only and previously broke
# EVERY test sharing this metadata (37 tests across test_pg_models.py/
# test_pg_repos.py/test_recommendation_engine.py), not just MarketIndex's
# own tests.
_JSONB_OR_JSON = JSONB().with_variant(JSON(), "sqlite")


class MarketIndex(Base):
    __tablename__ = "market_indices"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_market_indices_single"),
        {"schema": "stocks", "extend_existing": True},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, default=1,
    )
    nifty_data: Mapped[dict] = mapped_column(
        _JSONB_OR_JSON, nullable=False,
    )
    sensex_data: Mapped[dict] = mapped_column(
        _JSONB_OR_JSON, nullable=False,
    )
    market_state: Mapped[str] = mapped_column(
        String(10), nullable=False,
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

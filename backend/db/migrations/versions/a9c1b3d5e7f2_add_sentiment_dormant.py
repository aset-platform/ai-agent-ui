"""add sentiment_dormant table

Revision ID: a9c1b3d5e7f2
Revises: f8e7d6c5b4a3
Create Date: 2026-04-21 14:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9c1b3d5e7f2"
down_revision: Union[str, None] = "f8e7d6c5b4a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sentiment_dormant",
        sa.Column(
            "ticker",
            sa.String(length=30),
            nullable=False,
        ),
        sa.Column(
            "consecutive_empty",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_headline_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "last_seen_headlines_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("ticker"),
    )
    op.create_index(
        "ix_sentiment_dormant_next_retry_at",
        "sentiment_dormant",
        ["next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sentiment_dormant_next_retry_at",
        table_name="sentiment_dormant",
    )
    op.drop_table("sentiment_dormant")

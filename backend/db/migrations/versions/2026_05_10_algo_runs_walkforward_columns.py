"""Add walk-forward columns to algo.runs (Slice V2-2).

Revision ID: c4d6e8f0a2b5
Revises: b3c5e7d9f1a4
Create Date: 2026-05-10

V2-2 adds three nullable columns so existing single-run rows
are unaffected (NULL indicates a standalone backtest run):

  parent_walkforward_id UUID  — FK to the parent walk-forward
                                row (self-referencing on algo.runs
                                where mode='walkforward')
  window_start          DATE  — start of this window's test period
  window_end            DATE  — end of this window's test period
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c4d6e8f0a2b5"
down_revision: str | None = "b3c5e7d9f1a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "parent_walkforward_id",
            sa.UUID(as_uuid=True),
            nullable=True,
        ),
        schema="algo",
    )
    op.add_column(
        "runs",
        sa.Column("window_start", sa.Date(), nullable=True),
        schema="algo",
    )
    op.add_column(
        "runs",
        sa.Column("window_end", sa.Date(), nullable=True),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_column("runs", "window_end", schema="algo")
    op.drop_column("runs", "window_start", schema="algo")
    op.drop_column(
        "runs", "parent_walkforward_id", schema="algo",
    )

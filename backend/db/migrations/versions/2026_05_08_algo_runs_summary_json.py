"""Add summary_json + error_text to algo.runs (Slice 7b).

Revision ID: b3c5e7d9f1a4
Revises: 72a8a2cc1c1a
Create Date: 2026-05-08

Slice 7b inlines the equity curve + trade list as JSONB on the
runs row instead of pushing to MinIO. JSONB is fine for v1 sizes
(<1MB per run on watchlist-scoped backtests). MinIO promotion is
deferred to a future 7c if runs grow.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3c5e7d9f1a4"
down_revision: str | None = "72a8a2cc1c1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "summary_json", postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="algo",
    )
    op.add_column(
        "runs",
        sa.Column("error_text", sa.Text(), nullable=True),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_column("runs", "error_text", schema="algo")
    op.drop_column("runs", "summary_json", schema="algo")

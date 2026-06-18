"""Add locked_tickers TEXT[] to algo.runs for per-ticker position cap.

Revision ID: 2026_06_18_tickers
Revises: 2026_05_24_budget
Create Date: 2026-06-18

Persists the set of tickers currently locked by the LiveRuntime
(in-flight BUY or open position) so locks survive a backend restart
and can be observed / debugged via SQL.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_06_18_tickers"
down_revision = "2026_05_24_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "locked_tickers",
            sa.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_column("runs", "locked_tickers", schema="algo")

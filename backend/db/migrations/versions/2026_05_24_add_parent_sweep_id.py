"""Add parent_sweep_id to algo.runs.

Revision ID: 2026_05_24_sweep
Revises: c9d0e1f2a3b4
Create Date: 2026-05-24

Adds the parent_sweep_id column on algo.runs to support
the walk-forward parameter sweep epic. The column is
nullable and references algo.runs(id) — sweep parent
rows have NULL; per-variant walkforward rows have the
sweep's id; per-window backtest rows have NULL (they
chain via parent_walkforward_id, which already exists).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_05_24_sweep"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "parent_sweep_id",
            sa.UUID(),
            sa.ForeignKey(
                "algo.runs.id", ondelete="SET NULL",
            ),
            nullable=True,
        ),
        schema="algo",
    )
    op.create_index(
        "idx_runs_parent_sweep_id",
        "runs",
        ["parent_sweep_id"],
        schema="algo",
        postgresql_where=sa.text(
            "parent_sweep_id IS NOT NULL",
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_runs_parent_sweep_id",
        table_name="runs",
        schema="algo",
    )
    op.drop_column(
        "runs", "parent_sweep_id", schema="algo",
    )

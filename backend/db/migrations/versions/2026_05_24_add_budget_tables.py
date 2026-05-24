"""Add algo.user_budget + algo.budget_reservations.

Revision ID: 2026_05_24_budget
Revises: 2026_05_24_sweep
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_05_24_budget"
down_revision = "2026_05_24_sweep"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_budget",
        sa.Column("user_id", sa.UUID(), primary_key=True),
        sa.Column(
            "allocated_inr",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        schema="algo",
    )

    op.create_table(
        "budget_reservations",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "reservation_id", sa.UUID(), nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "strategy_id", sa.UUID(), nullable=False,
        ),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column(
            "reserved_inr",
            sa.Numeric(14, 2),
            nullable=False,
        ),
        sa.Column(
            "filled_qty",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "filled_inr",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "kite_order_id", sa.Text(), nullable=True,
        ),
        sa.Column(
            "transitioned_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "metadata",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_text", sa.Text(), nullable=True),
        schema="algo",
    )

    op.create_index(
        "idx_budget_res_active",
        "budget_reservations",
        ["user_id", "reservation_id"],
        schema="algo",
        postgresql_where=sa.text(
            "state IN ('PENDING', 'SUBMITTED', 'PARTIAL')"
        ),
    )
    op.create_index(
        "idx_budget_res_user_time",
        "budget_reservations",
        ["user_id", sa.text("transitioned_at DESC")],
        schema="algo",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_budget_res_user_time",
        table_name="budget_reservations",
        schema="algo",
    )
    op.drop_index(
        "idx_budget_res_active",
        table_name="budget_reservations",
        schema="algo",
    )
    op.drop_table("budget_reservations", schema="algo")
    op.drop_table("user_budget", schema="algo")

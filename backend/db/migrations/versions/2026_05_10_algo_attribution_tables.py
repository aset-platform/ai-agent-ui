"""algo: add attribution_daily + factor_regression tables (REGIME-6).

Revision ID: a4b5c6d7e8f9
Revises: f3a1b2c4d5e7
Create Date: 2026-05-10

Adds:
  - algo.attribution_daily — daily Brinson decomposition vs the
    NIFTY 50 baseline. Composite PK on
    (user_id, strategy_id, bar_date). JSONB columns hold the
    per-sector breakdown for each Brinson effect plus the
    aggregate active return.
  - algo.factor_regression — monthly OLS factor regression
    output. Composite PK on
    (user_id, strategy_id, period_start, period_end). Stores
    alpha + per-factor betas (JSONB) + R^2 + sample size.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "f3a1b2c4d5e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attribution_daily",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "strategy_id", postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("bar_date", sa.Date(), primary_key=True),
        sa.Column(
            "brinson_alloc", postgresql.JSONB(), nullable=False,
        ),
        sa.Column(
            "brinson_select", postgresql.JSONB(), nullable=False,
        ),
        sa.Column(
            "brinson_interaction", postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "total_active_return", sa.Numeric(), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        schema="algo",
    )
    op.create_table(
        "factor_regression",
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "strategy_id", postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("period_start", sa.Date(), primary_key=True),
        sa.Column("period_end", sa.Date(), primary_key=True),
        sa.Column("alpha", sa.Numeric(), nullable=False),
        sa.Column("betas", postgresql.JSONB(), nullable=False),
        sa.Column("r_squared", sa.Numeric(), nullable=False),
        sa.Column(
            "n_observations", sa.Integer(), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_table("factor_regression", schema="algo")
    op.drop_table("attribution_daily", schema="algo")

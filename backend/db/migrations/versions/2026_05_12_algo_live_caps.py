"""algo: live_caps table + positions.source column (V2-5).

Revision ID: e1f2a3b4c5d6
Revises: d7e9f1a3b5c6
Create Date: 2026-05-12

Adds:
  - algo.live_caps  — per-(user, strategy) live-trading config.
    Composite PK (user_id, strategy_id).  Default OFF.
  - algo.positions.source  — enum('paper','live') DEFAULT 'paper'.
    Backfills existing rows to 'paper'.
  - algo.runs.live_orders_in_flight  — JSONB tracking in-flight
    orders for kill-switch cancellation.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d7e9f1a3b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --------------------------------------------------------
    # 1. algo.live_caps — per-(user, strategy) live config
    # --------------------------------------------------------
    op.create_table(
        "live_caps",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "strategy_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        # Safety caps
        sa.Column(
            "max_inr",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "max_orders_per_day",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "allowed_tickers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # The main enable gate — DEFAULT FALSE (never live
        # unless explicitly set)
        sa.Column(
            "live_orders_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        # Approval metadata
        sa.Column(
            "approved_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_walkforward_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        # Daily rolling counters — reset at market open 09:00 IST
        sa.Column(
            "cumulative_inr_today",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "orders_count_today",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="algo",
    )
    op.create_index(
        "ix_algo_live_caps_user_id",
        "live_caps",
        ["user_id"],
        schema="algo",
    )
    op.create_index(
        "ix_algo_live_caps_strategy_id",
        "live_caps",
        ["strategy_id"],
        schema="algo",
    )

    # --------------------------------------------------------
    # 2. algo.positions.source — paper or live
    # --------------------------------------------------------
    op.execute(
        "DO $$ BEGIN "
        "  IF NOT EXISTS ("
        "    SELECT 1 FROM pg_type "
        "    JOIN pg_namespace ON pg_namespace.oid = pg_type.typnamespace "
        "    WHERE pg_type.typname = 'position_source' "
        "      AND pg_namespace.nspname = 'algo'"
        "  ) THEN "
        "    CREATE TYPE algo.position_source "
        "    AS ENUM ('paper', 'live'); "
        "  END IF; "
        "END $$"
    )
    op.add_column(
        "positions",
        sa.Column(
            "source",
            postgresql.ENUM(
                "paper", "live",
                name="position_source",
                schema="algo",
                create_type=False,
            ),
            nullable=False,
            server_default="paper",
        ),
        schema="algo",
    )
    # Backfill any pre-existing rows to 'paper'
    op.execute(
        "UPDATE algo.positions SET source = 'paper' "
        "WHERE source IS NULL OR source = 'paper'"
    )

    # --------------------------------------------------------
    # 3. algo.runs.live_orders_in_flight — JSONB list
    # --------------------------------------------------------
    op.add_column(
        "runs",
        sa.Column(
            "live_orders_in_flight",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_column("runs", "live_orders_in_flight", schema="algo")
    op.drop_column("positions", "source", schema="algo")
    op.execute(
        "DROP TYPE IF EXISTS algo.position_source"
    )
    op.drop_index(
        "ix_algo_live_caps_strategy_id",
        table_name="live_caps",
        schema="algo",
    )
    op.drop_index(
        "ix_algo_live_caps_user_id",
        table_name="live_caps",
        schema="algo",
    )
    op.drop_table("live_caps", schema="algo")

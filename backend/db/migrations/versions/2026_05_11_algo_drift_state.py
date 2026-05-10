"""algo: add live_drift_state table (V2-3 reconciliation).

Revision ID: d7e9f1a3b5c6
Revises: c4d6e8f0a2b5
Create Date: 2026-05-11

Adds:
  - algo.live_drift_state — per-user/symbol drift counter table.
  - drift_threshold column on algo.kill_switch — how many shares of
    discrepancy triggers a drift event (default 0 = any diff).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7e9f1a3b5c6"
down_revision: str | None = "c4d6e8f0a2b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_drift_state",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "symbol",
            sa.String(32),
            primary_key=True,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "consecutive_runs",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "last_diff",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
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
        "ix_algo_live_drift_state_user_id",
        "live_drift_state",
        ["user_id"],
        schema="algo",
    )

    # Drift threshold: add to kill_switch so it is per-user.
    # 0 = any non-zero qty diff counts as drift.
    op.add_column(
        "kill_switch",
        sa.Column(
            "drift_threshold_shares",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_column(
        "kill_switch", "drift_threshold_shares", schema="algo",
    )
    op.drop_index(
        "ix_algo_live_drift_state_user_id",
        table_name="live_drift_state",
        schema="algo",
    )
    op.drop_table("live_drift_state", schema="algo")

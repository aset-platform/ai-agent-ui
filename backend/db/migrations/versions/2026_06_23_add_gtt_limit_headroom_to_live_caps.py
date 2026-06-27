"""Add gtt_limit_headroom_pct to algo.live_caps.

Revision ID: 2026_06_23_gtt_headroom
Revises: 2026_06_18_tickers
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_06_23_gtt_headroom"
down_revision = "2026_06_18_tickers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "live_caps",
        sa.Column(
            "gtt_limit_headroom_pct",
            sa.Numeric(5, 4),
            nullable=False,
            server_default="0.0100",
        ),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_column("live_caps", "gtt_limit_headroom_pct", schema="algo")

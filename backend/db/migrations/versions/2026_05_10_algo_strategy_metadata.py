"""algo: add strategy_metadata table (REGIME-3 binding).

Revision ID: f3a1b2c4d5e7
Revises: e1f2a3b4c5d6
Create Date: 2026-05-10

Adds:
  - algo.strategy_metadata — per-strategy regime applicability +
    optional expected edge + free-form description. PK = strategy_id
    with FK→algo.strategies(id) ON DELETE CASCADE so the metadata
    row is wiped when its strategy is hard-deleted.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a1b2c4d5e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_metadata",
        sa.Column(
            "strategy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "algo.strategies.id", ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column(
            "applicable_regimes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text(
                "ARRAY['bull','sideways','bear']::text[]",
            ),
        ),
        sa.Column(
            "expected_edge",
            sa.Numeric(),
            nullable=True,
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_table("strategy_metadata", schema="algo")

"""Add force column to scheduled_jobs

Allows jobs to propagate force=True through the
scheduler → executor pipeline, skipping freshness
gates when triggered manually with force.

Revision ID: d5e6f7a8b9c0
Revises: c4d9e2f1a8b3
Create Date: 2026-04-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d9e2f1a8b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scheduled_jobs",
        sa.Column(
            "force",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("scheduled_jobs", "force")

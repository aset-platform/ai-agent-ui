"""pipeline_steps: payload jsonb column for scoped step args.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-05-15

Adds a free-form ``payload`` jsonb column to ``pipeline_steps``
so per-pipeline step invocations can declare scoped arguments
without bloating the column list (e.g. ``iceberg_maintenance``
gets ``{"tables": [...]}`` to scope backup + compact + sweep
to just the tables that pipeline actually writes).

Default is ``'{}'::jsonb`` so all existing rows stay
backwards-compatible — wrappers that ignore ``payload`` (every
current ``@register_job``) keep their existing behaviour.

ASETPLTFRM-418.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_steps",
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("pipeline_steps", "payload")

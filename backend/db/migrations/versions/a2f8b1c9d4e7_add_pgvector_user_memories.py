"""add pgvector extension and user_memories table

Revision ID: a2f8b1c9d4e7
Revises: 1670e38531ce
Create Date: 2026-04-01 13:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "a2f8b1c9d4e7"
down_revision: Union[str, None] = "1670e38531ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension.
    op.execute(
        "CREATE EXTENSION IF NOT EXISTS vector"
    )

    op.create_table(
        "user_memories",
        sa.Column(
            "memory_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "memory_type",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "structured",
            postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "embedding",
            Vector(768),
            nullable=False,
        ),
        sa.Column(
            "turn_number",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("memory_id"),
    )

    op.create_index(
        "ix_memory_user",
        "user_memories",
        ["user_id"],
    )
    op.create_index(
        "ix_memory_session",
        "user_memories",
        ["user_id", "session_id"],
    )
    # IVFFlat index for cosine similarity.
    # lists=20 is adequate for <10K vectors.
    op.execute(
        "CREATE INDEX ix_memory_embedding "
        "ON user_memories "
        "USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 20)"
    )


def downgrade() -> None:
    op.drop_index("ix_memory_embedding")
    op.drop_index("ix_memory_session")
    op.drop_index("ix_memory_user")
    op.drop_table("user_memories")
    op.execute("DROP EXTENSION IF EXISTS vector")

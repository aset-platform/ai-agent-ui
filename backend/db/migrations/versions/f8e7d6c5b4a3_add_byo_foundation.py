"""add BYO foundation — user_llm_keys + chat counter + byo limit

Revision ID: f8e7d6c5b4a3
Revises: e7f8a9b0c1d2
Create Date: 2026-04-18 21:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8e7d6c5b4a3"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "chat_request_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "byo_monthly_limit",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
    )

    op.create_table(
        "user_llm_keys",
        sa.Column(
            "id", sa.String(length=36), primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider", sa.String(length=32), nullable=False,
        ),
        sa.Column(
            "encrypted_key", sa.LargeBinary(), nullable=False,
        ),
        sa.Column(
            "label", sa.String(length=120), nullable=True,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "request_count_30d",
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
        sa.UniqueConstraint(
            "user_id", "provider",
            name="uq_user_llm_keys_user_provider",
        ),
    )
    op.create_index(
        "ix_user_llm_keys_user_id",
        "user_llm_keys",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_llm_keys_user_id",
        table_name="user_llm_keys",
    )
    op.drop_table("user_llm_keys")
    op.drop_column("users", "byo_monthly_limit")
    op.drop_column("users", "chat_request_count")

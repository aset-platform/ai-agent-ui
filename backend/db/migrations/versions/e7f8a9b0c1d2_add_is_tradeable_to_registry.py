"""add is_tradeable to stock_registry

Revision ID: e7f8a9b0c1d2
Revises: b2c3d4e5f6a7
Create Date: 2026-04-18 14:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stock_registry",
        sa.Column(
            "is_tradeable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_index(
        "ix_stock_registry_is_tradeable",
        "stock_registry",
        ["is_tradeable"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_stock_registry_is_tradeable",
        table_name="stock_registry",
    )
    op.drop_column("stock_registry", "is_tradeable")

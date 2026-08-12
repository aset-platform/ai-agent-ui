"""Add portfolio_closed_positions — realized-P&L record for a closed
(sold) portfolio position.

Mutable ledger row keyed by an app-generated UUID; not append-only
market data, so Postgres (not Iceberg) per the storage convention.

Revision ID: 2026_08_12_portfolio_closed
Revises: 2026_08_10_labeled_outcomes
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_08_12_portfolio_closed"
down_revision = "2026_08_10_labeled_outcomes"
branch_labels = None
depends_on = None

_TABLE = "portfolio_closed_positions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("buy_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("sell_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("buy_date", sa.Date, nullable=True),
        sa.Column("sell_date", sa.Date, nullable=False),
        sa.Column(
            "fees", sa.Numeric(18, 4), nullable=False,
            server_default="0",
        ),
        sa.Column("realized_pnl", sa.Numeric(18, 4), nullable=False),
        sa.Column(
            "realized_pnl_pct", sa.Numeric(12, 4), nullable=True
        ),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column(
            "sell_transaction_id", sa.String(36), nullable=True
        ),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
    )
    op.create_index(
        "ix_pcp_user_sell_date",
        _TABLE,
        ["user_id", "sell_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_pcp_user_sell_date", table_name=_TABLE)
    op.drop_table(_TABLE)

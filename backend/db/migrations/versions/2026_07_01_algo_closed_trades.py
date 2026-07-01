"""Add algo.closed_trades — materialized closed-trade rollup for
the Strategy Performance page (Paper/Live modes).

Revision ID: 2026_07_01_closed_trades
Revises: 2026_06_23_gtt_headroom
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_07_01_closed_trades"
down_revision = "2026_06_23_gtt_headroom"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "closed_trades",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "strategy_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "algo.strategies.id", ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("avg_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("fill_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("opened_at", sa.Date, nullable=False),
        sa.Column("closed_at", sa.Date, nullable=False),
        sa.Column("opened_at_ts_ns", sa.BigInteger, nullable=True),
        sa.Column("closed_at_ts_ns", sa.BigInteger, nullable=True),
        sa.Column(
            "realised_pnl_inr", sa.Numeric(14, 2), nullable=False,
        ),
        sa.Column("return_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column(
            "exit_reason", sa.String(32), nullable=False,
            server_default="signal",
        ),
        sa.Column(
            "dry_run", sa.Boolean, nullable=False,
            server_default="false",
        ),
        sa.Column("buy_event_id", sa.String(64), nullable=False),
        sa.Column("sell_event_id", sa.String(64), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "buy_event_id", "sell_event_id",
            name="uq_closed_trades_fill_pair",
        ),
        schema="algo",
    )
    op.create_index(
        "ix_closed_trades_lookup",
        "closed_trades",
        ["user_id", "strategy_id", "mode", "closed_at"],
        schema="algo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_closed_trades_lookup",
        table_name="closed_trades",
        schema="algo",
    )
    op.drop_table("closed_trades", schema="algo")

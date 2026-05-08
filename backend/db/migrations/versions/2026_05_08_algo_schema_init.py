"""algo schema + 7 base tables (Slice 0 of the Algo Trading epic).

Revision ID: 72a8a2cc1c1a
Revises: f8e7d6c5b4a3
Create Date: 2026-05-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "72a8a2cc1c1a"
down_revision: Union[str, None] = "f8e7d6c5b4a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS algo")

    op.create_table(
        "broker_credentials",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("api_key_fernet", sa.LargeBinary(), nullable=False),
        sa.Column("access_token_fernet", sa.LargeBinary(), nullable=True),
        sa.Column(
            "access_token_expires_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column("kite_user_id", sa.String(32), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        schema="algo",
    )

    op.create_table(
        "instruments",
        sa.Column("instrument_token", sa.BigInteger(), primary_key=True),
        sa.Column("tradingsymbol", sa.String(64), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("segment", sa.String(32), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("tick_size", sa.Numeric(12, 4), nullable=False),
        sa.Column("our_ticker", sa.String(32), nullable=True),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        schema="algo",
    )
    op.create_index(
        "ix_algo_instruments_tradingsymbol",
        "instruments", ["tradingsymbol"], schema="algo",
    )
    op.create_index(
        "ix_algo_instruments_our_ticker",
        "instruments", ["our_ticker"], schema="algo",
    )

    op.create_table(
        "strategies",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("ast_json", postgresql.JSONB(), nullable=False),
        sa.Column("ast_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        schema="algo",
    )
    op.create_index(
        "ix_algo_strategies_user_id", "strategies", ["user_id"], schema="algo",
    )

    op.create_table(
        "runs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "strategy_id", postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("params_hash", sa.String(64), nullable=True),
        sa.Column("artifact_uri", sa.String(512), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["strategy_id"], ["algo.strategies.id"],
            name="fk_runs_strategy_id",
        ),
        schema="algo",
    )
    op.create_index(
        "ix_algo_runs_strategy_id", "runs", ["strategy_id"], schema="algo",
    )
    op.create_index(
        "ix_algo_runs_user_id_started_at",
        "runs", ["user_id", "started_at"], schema="algo",
    )

    op.create_table(
        "positions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("avg_price", sa.Numeric(18, 4), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "realised_pnl_inr", sa.Numeric(18, 4), nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["algo.runs.id"], name="fk_positions_run_id",
        ),
        schema="algo",
    )
    op.create_index(
        "ix_algo_positions_run_id", "positions", ["run_id"], schema="algo",
    )

    op.create_table(
        "risk_state",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("day_date", sa.Date(), primary_key=True),
        sa.Column(
            "daily_realised_pnl_inr", sa.Numeric(18, 4),
            nullable=False, server_default="0",
        ),
        sa.Column(
            "daily_unrealised_pnl_inr", sa.Numeric(18, 4),
            nullable=False, server_default="0",
        ),
        sa.Column(
            "breaches", postgresql.JSONB(),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        schema="algo",
    )

    op.create_table(
        "kill_switch",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default="false",
        ),
        sa.Column("set_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(256), nullable=True),
        schema="algo",
    )


def downgrade() -> None:
    op.drop_table("kill_switch", schema="algo")
    op.drop_table("risk_state", schema="algo")
    op.drop_index(
        "ix_algo_positions_run_id", table_name="positions", schema="algo",
    )
    op.drop_table("positions", schema="algo")
    op.drop_index("ix_algo_runs_user_id_started_at", table_name="runs", schema="algo")
    op.drop_index("ix_algo_runs_strategy_id", table_name="runs", schema="algo")
    op.drop_table("runs", schema="algo")
    op.drop_index(
        "ix_algo_strategies_user_id", table_name="strategies", schema="algo",
    )
    op.drop_table("strategies", schema="algo")
    op.drop_index(
        "ix_algo_instruments_our_ticker", table_name="instruments", schema="algo",
    )
    op.drop_index(
        "ix_algo_instruments_tradingsymbol", table_name="instruments", schema="algo",
    )
    op.drop_table("instruments", schema="algo")
    op.drop_table("broker_credentials", schema="algo")
    op.execute("DROP SCHEMA IF EXISTS algo")

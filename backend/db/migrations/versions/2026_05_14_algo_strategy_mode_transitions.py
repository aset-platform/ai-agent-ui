"""algo: strategy_mode_transitions audit table + mode CHECK constraint.

Revision ID: b8c9d0e1f2a3
Revises: a4b5c6d7e8f9
Create Date: 2026-05-14

Adds:
  - algo.strategy_mode_transitions — per-promote / per-auto-demote
    audit row. Carries who promoted, from→to mode, optional reason,
    and an ast_hash snapshot of the strategy AST at transition time.
    FK→strategies(id) ON DELETE SET NULL so forensic trail survives
    a hard-delete of the source strategy.
  - CHECK on algo.strategies.mode restricting to {draft, paper, live}.

These power the promotion-workflow gates: draft→paper requires a
fresh backtest + walk-forward, paper→live requires a fresh paper
run. Subsequent re-promotions to live after auto-demote can be
bypassed when the strategy has ever held mode='live'.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_mode_transitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "strategy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "algo.strategies.id", ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column("user_email", sa.String(255), nullable=False),
        # NULL on the very first transition (the initial 'draft'
        # state is implicit; only real promotions get a from_mode).
        sa.Column("from_mode", sa.String(16), nullable=True),
        sa.Column("to_mode", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("bypass_used", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        # sha256 hex of the canonical AST JSON at transition time.
        # Used for the audit log + drift-detection in future tools;
        # not enforced at runtime today.
        sa.Column("ast_hash", sa.String(64), nullable=True),
        sa.Column(
            "transitioned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "to_mode IN ('draft','paper','live')",
            name="ck_strategy_mode_transitions_to_mode",
        ),
        sa.CheckConstraint(
            "from_mode IS NULL OR from_mode IN ('draft','paper','live')",
            name="ck_strategy_mode_transitions_from_mode",
        ),
        schema="algo",
    )
    op.create_index(
        "ix_algo_strategy_mode_transitions_strategy_id",
        "strategy_mode_transitions",
        ["strategy_id"],
        schema="algo",
    )
    op.create_index(
        "ix_algo_strategy_mode_transitions_recent",
        "strategy_mode_transitions",
        ["strategy_id", "transitioned_at"],
        schema="algo",
        postgresql_using="btree",
        postgresql_ops={"transitioned_at": "DESC"},
    )

    # Tighten algo.strategies.mode to the lifecycle set. Existing
    # rows are all 'draft' so the constraint adds cleanly.
    op.execute(
        "ALTER TABLE algo.strategies "
        "ADD CONSTRAINT ck_strategies_mode "
        "CHECK (mode IN ('draft','paper','live'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE algo.strategies DROP CONSTRAINT ck_strategies_mode"
    )
    op.drop_index(
        "ix_algo_strategy_mode_transitions_recent",
        table_name="strategy_mode_transitions",
        schema="algo",
    )
    op.drop_index(
        "ix_algo_strategy_mode_transitions_strategy_id",
        table_name="strategy_mode_transitions",
        schema="algo",
    )
    op.drop_table(
        "strategy_mode_transitions", schema="algo",
    )

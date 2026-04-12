"""add recommendation tables

Revision ID: ede952a36b38
Revises: d5e6f7a8b9c0
Create Date: 2026-04-12 06:41:13.006934
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'ede952a36b38'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the stocks schema if it doesn't exist
    op.execute("CREATE SCHEMA IF NOT EXISTS stocks")

    op.create_table(
        'recommendation_runs',
        sa.Column(
            'run_id', sa.UUID(as_uuid=False),
            server_default=sa.text('gen_random_uuid()'),
            nullable=False,
        ),
        sa.Column(
            'user_id', sa.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column('run_date', sa.Date(), nullable=False),
        sa.Column(
            'run_type', sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            'portfolio_snapshot',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            'health_score', sa.Float(), nullable=False,
        ),
        sa.Column(
            'health_label', sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            'health_assessment', sa.Text(), nullable=True,
        ),
        sa.Column(
            'candidates_scanned', sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            'candidates_passed', sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            'llm_model', sa.String(length=50),
            nullable=True,
        ),
        sa.Column(
            'llm_tokens_used', sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            'duration_secs', sa.Float(), nullable=True,
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('run_id'),
        schema='stocks',
    )
    op.create_index(
        'ix_rec_runs_user_date',
        'recommendation_runs',
        ['user_id', 'run_date'],
        unique=False,
        schema='stocks',
    )

    op.create_table(
        'recommendations',
        sa.Column(
            'id', sa.UUID(as_uuid=False),
            server_default=sa.text('gen_random_uuid()'),
            nullable=False,
        ),
        sa.Column(
            'run_id', sa.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column(
            'tier', sa.String(length=20), nullable=False,
        ),
        sa.Column(
            'category', sa.String(length=25),
            nullable=False,
        ),
        sa.Column(
            'ticker', sa.String(length=20), nullable=True,
        ),
        sa.Column(
            'action', sa.String(length=15), nullable=False,
        ),
        sa.Column(
            'severity', sa.String(length=10),
            nullable=False,
        ),
        sa.Column(
            'rationale', sa.Text(), nullable=False,
        ),
        sa.Column(
            'expected_impact', sa.Text(), nullable=True,
        ),
        sa.Column(
            'data_signals',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            'price_at_rec', sa.Float(), nullable=True,
        ),
        sa.Column(
            'target_price', sa.Float(), nullable=True,
        ),
        sa.Column(
            'expected_return_pct', sa.Float(),
            nullable=True,
        ),
        sa.Column(
            'index_tags', sa.ARRAY(sa.String()),
            nullable=True,
        ),
        sa.Column(
            'status', sa.String(length=15),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            'acted_on_date', sa.Date(), nullable=True,
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['run_id'],
            ['stocks.recommendation_runs.run_id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        schema='stocks',
    )
    op.create_index(
        'ix_recs_run_id', 'recommendations',
        ['run_id'], unique=False, schema='stocks',
    )
    op.create_index(
        'ix_recs_status_created', 'recommendations',
        ['status', 'created_at'],
        unique=False, schema='stocks',
    )
    op.create_index(
        'ix_recs_ticker_status', 'recommendations',
        ['ticker', 'status'],
        unique=False, schema='stocks',
    )

    op.create_table(
        'recommendation_outcomes',
        sa.Column(
            'id', sa.UUID(as_uuid=False),
            server_default=sa.text('gen_random_uuid()'),
            nullable=False,
        ),
        sa.Column(
            'recommendation_id', sa.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column(
            'check_date', sa.Date(), nullable=False,
        ),
        sa.Column(
            'days_elapsed', sa.Integer(), nullable=False,
        ),
        sa.Column(
            'actual_price', sa.Float(), nullable=False,
        ),
        sa.Column(
            'return_pct', sa.Float(), nullable=False,
        ),
        sa.Column(
            'benchmark_return_pct', sa.Float(),
            nullable=False,
        ),
        sa.Column(
            'excess_return_pct', sa.Float(),
            nullable=False,
        ),
        sa.Column(
            'outcome_label', sa.String(length=15),
            nullable=False,
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['recommendation_id'],
            ['stocks.recommendations.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'recommendation_id', 'days_elapsed',
            name='uq_rec_outcome_rec_days',
        ),
        schema='stocks',
    )


def downgrade() -> None:
    op.drop_table(
        'recommendation_outcomes', schema='stocks',
    )
    op.drop_index(
        'ix_recs_ticker_status',
        table_name='recommendations', schema='stocks',
    )
    op.drop_index(
        'ix_recs_status_created',
        table_name='recommendations', schema='stocks',
    )
    op.drop_index(
        'ix_recs_run_id',
        table_name='recommendations', schema='stocks',
    )
    op.drop_table('recommendations', schema='stocks')
    op.drop_index(
        'ix_rec_runs_user_date',
        table_name='recommendation_runs', schema='stocks',
    )
    op.drop_table(
        'recommendation_runs', schema='stocks',
    )
    op.execute("DROP SCHEMA IF EXISTS stocks")

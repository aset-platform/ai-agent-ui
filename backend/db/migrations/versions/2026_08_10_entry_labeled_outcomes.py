"""Add algo.entry_labeled_outcomes — recurring labeled training set for
the Release-2 entry-strength gate (ASETPLTFRM-480 / PRE-6).

Grain: ONE row per candidate signal — (user_id, strategy_id, ticker,
trade_date, mode). Captures BOTH filled candidates (real outcome) and
rejected/unfilled candidates (counterfactual outcome reconstructed from
bars), so the R2 gate can be calibrated across the whole candidate
stream rather than only the few that filled under a narrow allow-list.

Populated by (a) the PRE-1 one-off historical backfill and (b) the
recurring daily materialization job (PRE-6). Mutable (outcomes settle
late) -> Postgres, not Iceberg. Idempotent upsert on the natural key.

Revision ID: 2026_08_10_labeled_outcomes
Revises: 2026_07_01_closed_trades
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "2026_08_10_labeled_outcomes"
down_revision = "2026_07_01_closed_trades"
branch_labels = None
depends_on = None

_TABLE = "entry_labeled_outcomes"
_SCHEMA = "algo"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        # --- identity / grain ---
        sa.Column(
            "id", pg.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "strategy_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("algo.strategies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        # candidate signal date (the RSI2<=5 day); grain axis
        sa.Column("trade_date", sa.Date, nullable=False),
        # representative snapshot ts + which OR-trigger leg fired
        sa.Column("signal_ts_ns", sa.BigInteger, nullable=True),
        sa.Column("trigger", sa.String(16), nullable=True),
        # --- entry features (from entry_strength_snapshot + recompute) ---
        sa.Column("rsi2_at_entry", sa.Numeric(10, 4), nullable=True),
        sa.Column("dist_sma50_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("dist_sma200_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("ret_1d_prior", sa.Numeric(10, 4), nullable=True),
        sa.Column("ret_3d_prior", sa.Numeric(10, 4), nullable=True),
        sa.Column("gap_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("breadth_oversold", sa.Integer, nullable=True),
        sa.Column("breadth_total", sa.Integer, nullable=True),
        # --- QM/ESS dims (join from stocks.entry_quality_daily) ---
        sa.Column("qm_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("ess_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("ess_gate_passed", sa.Boolean, nullable=True),
        sa.Column("qm_mdd_pctile", sa.Numeric(8, 4), nullable=True),
        sa.Column("qm_rs_pctile", sa.Numeric(8, 4), nullable=True),
        sa.Column("qm_sharpe_pctile", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "ess_absorption_volume_score", sa.Numeric(8, 4), nullable=True,
        ),
        sa.Column(
            "ess_selling_deceleration_score", sa.Numeric(8, 4),
            nullable=True,
        ),
        sa.Column(
            "ess_trend_stability_score", sa.Numeric(8, 4), nullable=True,
        ),
        # --- fill / decision ---
        sa.Column(
            "filled", sa.Boolean, nullable=False, server_default="false",
        ),
        # why it did NOT fill (ticker_not_allowed, falling_knife_veto,
        # insufficient_capital_qty_zero, cooldown, ...); NULL if filled
        sa.Column("rejection_reason", sa.String(32), nullable=True),
        sa.Column("buy_event_id", sa.String(64), nullable=True),
        sa.Column("sell_event_id", sa.String(64), nullable=True),
        sa.Column("entry_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("exit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("exit_reason", sa.String(32), nullable=True),
        # --- outcome ---
        # 'real' (filled trade) | 'counterfactual' (reconstructed if bought)
        sa.Column("outcome_kind", sa.String(16), nullable=True),
        # 'intraday15m' | 'daily' | 'none'
        sa.Column("outcome_src", sa.String(16), nullable=True),
        sa.Column("mfe_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("mae_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("realised_pnl_inr", sa.Numeric(14, 2), nullable=True),
        sa.Column("return_pct", sa.Numeric(10, 4), nullable=True),
        # false until the trade closes / counterfactual horizon reached —
        # drives the daily job's re-materialize-open pass (late outcomes)
        sa.Column(
            "outcome_settled", sa.Boolean, nullable=False,
            server_default="false",
        ),
        # NULL until settled; realised_pnl_inr > 0 once known
        sa.Column("label_win", sa.Boolean, nullable=True),
        # --- bookkeeping ---
        sa.Column(
            "dry_run", sa.Boolean, nullable=False, server_default="false",
        ),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "strategy_id", "ticker", "trade_date", "mode",
            name="uq_entry_labeled_outcomes_signal",
        ),
        schema=_SCHEMA,
    )
    # primary read path (calibration pulls per strategy/mode over a window)
    op.create_index(
        "ix_elo_lookup", _TABLE,
        ["strategy_id", "mode", "trade_date"], schema=_SCHEMA,
    )
    # the daily job's re-materialize-open pass
    op.create_index(
        "ix_elo_unsettled", _TABLE,
        ["strategy_id", "outcome_settled"], schema=_SCHEMA,
    )
    # label distribution / filtering
    op.create_index(
        "ix_elo_label", _TABLE,
        ["strategy_id", "filled", "label_win"], schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_elo_label", table_name=_TABLE, schema=_SCHEMA)
    op.drop_index("ix_elo_unsettled", table_name=_TABLE, schema=_SCHEMA)
    op.drop_index("ix_elo_lookup", table_name=_TABLE, schema=_SCHEMA)
    op.drop_table(_TABLE, schema=_SCHEMA)

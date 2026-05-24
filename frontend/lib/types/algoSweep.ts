// Mirrors backend/algo/backtest/sweep_types.py.

export interface SweepableField {
  key: string;
  label: string;
  field_type: "int" | "decimal";
  min_value: string;  // string-encoded to preserve precision
  max_value: string;
}

export type SweepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export interface SweepVariantSummary {
  variant_index: number;
  swept_value: number | string;
  walkforward_run_id: string;
  avg_pnl_pct: string;
  avg_win_rate_pct: string;
  avg_max_drawdown_pct: string;
  sharpe: string;
  dsr: string;
  n_trades: number;
  status: "completed" | "failed" | "skipped";
  error_text: string | null;
}

export interface SweepResult {
  run_id: string;
  base_strategy_id: string;
  swept_field: string;
  swept_values: (number | string)[];
  variants: SweepVariantSummary[];
  cross_variant_pbo: string | null;
  returns_matrix_shape: [number, number];
  winner_variant_index: number | null;
  started_at: string;
  completed_at: string | null;
  status: SweepStatus;
  error_text?: string | null;
}

export interface SweepConfig {
  base_strategy_id: string;
  period_start: string;
  period_end: string;
  train_days?: number;
  test_days?: number;
  step_days?: number;
  initial_capital_inr?: string;
  regime_stratified?: boolean;
  swept_field: string;
  swept_values: (number | string)[];
  interval_sec?: number;
}

export interface SweepRow {
  run_id: string;
  base_strategy_id: string;
  status: SweepStatus;
  started_at: string | null;
  completed_at: string | null;
}

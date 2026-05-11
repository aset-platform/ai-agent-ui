/**
 * Three sample-strategy templates used as starting points by
 * the visual builder. Each is a complete, valid Strategy AST
 * — backend will accept POSTed verbatim.
 */

import type { StrategyAst } from "@/hooks/useStrategies";

const _common = {
  universe: {
    type: "scope",
    scope: "watchlist",
    filter: { ticker_type: ["stock"], market: "india" },
  },
  schedule: { type: "bar_close", interval: "1d", time: "15:25 IST" },
  rebalance: { type: "daily", max_positions: 10 },
  risk: {
    per_trade: { stop_loss_pct: 5, max_qty: 100 },
    portfolio: { max_exposure_pct: 80, max_concentration_pct: 25 },
    daily: { max_loss_pct: 2, max_open_positions: 10 },
  },
};

function randomId(): string {
  return crypto.randomUUID();
}

export const TEMPLATES: { key: string; label: string; ast: StrategyAst }[] = [
  {
    key: "blank",
    label: "Blank (hold)",
    ast: {
      id: randomId(),
      name: "New strategy",
      ..._common,
      root: { type: "hold" },
    } as unknown as StrategyAst,
  },
  {
    key: "golden_cross",
    label: "Golden cross",
    ast: {
      id: randomId(),
      name: "Golden cross v1",
      ..._common,
      root: {
        type: "if",
        cond: {
          type: "and",
          operands: [
            {
              type: "compare",
              left: { feature: "today_ltp" },
              op: ">",
              right: { feature: "sma_50" },
            },
            {
              type: "compare",
              left: { feature: "today_ltp" },
              op: ">",
              right: { feature: "sma_200" },
            },
            {
              type: "compare",
              left: { feature: "golden_cross_days_ago" },
              op: "<=",
              right: { literal: 10 },
            },
          ],
        },
        then: { type: "set_target_weight", weight: 0.20 },
        else: { type: "exit", scope: "all_open" },
      },
    } as unknown as StrategyAst,
  },
  {
    key: "mean_reversion",
    label: "Mean reversion (RSI)",
    ast: {
      id: randomId(),
      name: "Mean reversion v1",
      ..._common,
      root: {
        type: "if",
        cond: {
          type: "compare",
          left: { feature: "rsi" },
          op: "<",
          right: { literal: 30 },
        },
        then: { type: "set_target_weight", weight: 0.10 },
        else: { type: "exit", scope: "this_symbol" },
      },
    } as unknown as StrategyAst,
  },

  // Regime-tailored templates (v3) — mirror of the JSON files
  // under backend/algo/strategy/templates/. Keep in sync if
  // either side changes (single source of truth = a follow-up
  // for v3.1: GET /v1/algo/strategy/templates).
  {
    key: "regime_bull_momentum",
    label: "BULL — Momentum + Trend",
    ast: {
      id: randomId(),
      name: "BULL — Momentum + Trend",
      ..._common,
      rebalance: { type: "daily", max_positions: 10 },
      risk: {
        per_trade: { stop_loss_pct: 8, max_qty: 1000 },
        portfolio: {
          max_exposure_pct: 80, max_concentration_pct: 12,
        },
        daily: { max_loss_pct: 5, max_open_positions: 10 },
      },
      root: {
        type: "if",
        cond: {
          type: "and",
          operands: [
            {
              type: "compare",
              left: { feature: "regime_label" },
              op: "==", right: { literal: "BULL" },
            },
            {
              type: "compare",
              left: { feature: "mom_12_1" },
              op: ">", right: { literal: 0.10 },
            },
            {
              type: "compare",
              left: { feature: "adx_14" },
              op: ">", right: { literal: 25 },
            },
            {
              type: "compare",
              left: { feature: "distance_from_sma200" },
              op: ">", right: { literal: 0.0 },
            },
            {
              type: "compare",
              left: { feature: "volume_x_avg_20" },
              op: ">", right: { literal: 1.0 },
            },
          ],
        },
        then: { type: "set_target_weight", weight: 0.08 },
        else: { type: "exit", scope: "this_symbol" },
      },
    } as unknown as StrategyAst,
  },
  {
    key: "regime_sideways_meanrev_quality",
    label: "SIDEWAYS — Mean Reversion + Quality",
    ast: {
      id: randomId(),
      name: "SIDEWAYS — Mean Reversion + Quality",
      ..._common,
      rebalance: { type: "daily", max_positions: 8 },
      risk: {
        per_trade: { stop_loss_pct: 5, max_qty: 800 },
        portfolio: {
          max_exposure_pct: 60, max_concentration_pct: 10,
        },
        daily: { max_loss_pct: 3, max_open_positions: 8 },
      },
      root: {
        type: "if",
        cond: {
          type: "and",
          operands: [
            {
              type: "compare",
              left: { feature: "regime_label" },
              op: "==", right: { literal: "SIDEWAYS" },
            },
            {
              type: "compare",
              left: { feature: "realized_vol_60d" },
              op: "<", right: { literal: 0.30 },
            },
            {
              type: "between",
              value: { feature: "rsi" },
              low: { literal: 30 },
              high: { literal: 50 },
            },
            {
              type: "between",
              value: { feature: "distance_from_sma200" },
              low: { literal: -0.05 },
              high: { literal: 0.10 },
            },
          ],
        },
        then: { type: "set_target_weight", weight: 0.075 },
        else: { type: "exit", scope: "this_symbol" },
      },
    } as unknown as StrategyAst,
  },
  {
    key: "regime_bear_defensive_lowvol",
    label: "BEAR — Defensive Low-Vol Quality",
    ast: {
      id: randomId(),
      name: "BEAR — Defensive Low-Vol Quality",
      ..._common,
      rebalance: { type: "daily", max_positions: 5 },
      risk: {
        per_trade: { stop_loss_pct: 6, max_qty: 500 },
        portfolio: {
          max_exposure_pct: 40, max_concentration_pct: 10,
        },
        daily: { max_loss_pct: 2.5, max_open_positions: 5 },
      },
      root: {
        type: "if",
        cond: {
          type: "and",
          operands: [
            {
              type: "compare",
              left: { feature: "regime_label" },
              op: "==", right: { literal: "BEAR" },
            },
            {
              type: "compare",
              left: { feature: "stress_prob" },
              op: "<", right: { literal: 0.5 },
            },
            {
              type: "compare",
              left: { feature: "beta_to_nifty" },
              op: "<", right: { literal: 0.7 },
            },
            {
              type: "compare",
              left: { feature: "realized_vol_60d" },
              op: "<", right: { literal: 0.20 },
            },
            {
              type: "compare",
              left: { feature: "rs_vs_nifty_3m" },
              op: ">", right: { literal: 1.0 },
            },
          ],
        },
        then: { type: "set_target_weight", weight: 0.08 },
        else: { type: "exit", scope: "this_symbol" },
      },
    } as unknown as StrategyAst,
  },
];

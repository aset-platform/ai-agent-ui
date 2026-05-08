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
        else: { type: "hold" },
      },
    } as unknown as StrategyAst,
  },
];

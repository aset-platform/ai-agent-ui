/**
 * Type literals mirroring the backend AST node ``type``
 * discriminator. No semantic logic — the visual builder uses
 * this only to render a labelled palette + dispatch
 * renderers per node type.
 */

export type AstNodeType =
  // Condition
  | "compare"
  | "and"
  | "or"
  | "not"
  | "crossover"
  | "between"
  // Action
  | "buy"
  | "sell"
  | "exit"
  | "hold"
  | "set_target_weight"
  // Composite
  | "if"
  | "select_top_n"
  | "weighted";

export type AstNodeFamily = "condition" | "action" | "composite";

export const AST_NODE_FAMILY: Record<AstNodeType, AstNodeFamily> = {
  compare: "condition",
  and: "condition",
  or: "condition",
  not: "condition",
  crossover: "condition",
  between: "condition",
  buy: "action",
  sell: "action",
  exit: "action",
  hold: "action",
  set_target_weight: "action",
  if: "composite",
  select_top_n: "composite",
  weighted: "composite",
};

export const AST_NODE_LABEL: Record<AstNodeType, string> = {
  compare: "Compare",
  and: "AND",
  or: "OR",
  not: "NOT",
  crossover: "Crossover",
  between: "Between",
  buy: "Buy",
  sell: "Sell",
  exit: "Exit",
  hold: "Hold",
  set_target_weight: "Set target weight",
  if: "If / then / else",
  select_top_n: "Select top N",
  weighted: "Weighted",
};

// REGIME-3 — strategy↔regime binding metadata.
export type RegimeLabel = "bull" | "sideways" | "bear";

export const REGIME_LABELS: RegimeLabel[] = [
  "bull",
  "sideways",
  "bear",
];

/**
 * Backend GET /v1/algo/strategies/:id response shape (REGIME-3).
 * Wraps the AST in a sibling object so optional metadata can ride
 * alongside without breaking the strict ``Strategy`` AST schema.
 */
export interface StrategyResponse {
  strategy: {
    id: string;
    name: string;
    [key: string]: unknown;
  };
  applicable_regimes: RegimeLabel[];
}

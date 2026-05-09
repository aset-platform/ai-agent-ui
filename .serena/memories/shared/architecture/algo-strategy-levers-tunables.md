# Strategy edit UX — Levers panel + tunable AST walker

Non-technical users edit strategies through form controls. Visual builder tree (`AstTreeView`) and JSON pane (`JsonPane`) are **read-only by design**.

## User contract

User explicitly stated: "fine to keep readonly and copy paste only, but we need to give some levers to edit the metrics of any strategy."

Translation:
- Don't add interactivity to the visual builder tree or JSON pane.
- Every tunable parameter (numeric or categorical) must surface in `StrategyLeversPanel` as a typed input with range validation.

## Two lever sources

### 1. Top-level structural fields (static)

Hard-coded form controls in `StrategyLeversPanel.tsx`:

| Group | Field | Control |
|---|---|---|
| Universe | scope | dropdown (discovery / watchlist / portfolio) |
| Universe | filter.market | dropdown (india / us / all) |
| Universe | filter.ticker_type | multi-checkbox (≥1 enforced) |
| Rebalance | max_positions | number 1-50 |
| Risk · per-trade | stop_loss_pct, max_qty | numbers |
| Risk · portfolio | max_exposure_pct, max_concentration_pct | numbers (steps 5%) |
| Risk · daily | max_loss_pct, max_open_positions | numbers |

### 2. Inside-tree tunables (auto-discovered)

`strategyTunables.walkTunables(strategy.root)` traverses the rule tree and yields every numeric leaf the user can sensibly tune without changing rule logic:

```ts
type Tunable = {
  path: string;        // dot-path under root, e.g. "cond.operands[2].right.literal"
  label: string;       // breadcrumb-aware ("rsi < ?", "Target weight (× equity)")
  kind: "weight" | "shares" | "literal";
  value: number;
  min?, max?, step?
};
```

Recognised tunables:
- `set_target_weight.weight` (0..1, step 0.01)
- `buy.qty.shares` / `sell.qty.shares` (≥ 1)
- `compare.right.literal` (numeric only — string literals skipped because changing them alters rule semantics)

`setByPath(root, path, value)` performs shallow-clone deep-set so React state updates work cleanly.

## Adding a new tunable node type

1. Add a `visit()` branch in `strategyTunables.ts` for the new node type.
2. The lever appears in the panel's "Strategy logic" group automatically across all strategies. No per-strategy code.

## Save flow

The panel patches the in-memory `StrategyAst` via `onChange`. Save uses the existing PUT `/v1/algo/strategies/{id}` button at the bottom of `StrategyBuilder` — no new endpoint. JSON pane on the right reflects changes live so users see the diff before saving.

## Key files

- `frontend/components/algo-trading/builder/StrategyLeversPanel.tsx`
- `frontend/components/algo-trading/builder/strategyTunables.ts`
- `frontend/components/algo-trading/builder/StrategyBuilder.tsx` (mounts the panel above the tree)

## Tests

- `__tests__/StrategyLeversPanel.test.tsx` — render + patch + collapse + ticker_type ≥1 enforcement
- `__tests__/strategyTunables.test.ts` — walk + setByPath for all node shapes

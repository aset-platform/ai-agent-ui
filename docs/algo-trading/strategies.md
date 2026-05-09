# Strategies

A strategy is a JSON AST validated by Pydantic
(`backend/algo/strategy/ast.py`). Stored in
`algo.strategies.ast_json` (JSONB) per user. Edited through the
`strategies` tab.

## AST shape

```json
{
  "id": "uuid",
  "name": "human label",
  "universe": {
    "type": "scope",
    "scope": "discovery | watchlist | portfolio",
    "filter": {
      "ticker_type": ["stock", "etf"],
      "market": "india | us | all"
    }
  },
  "schedule": {
    "type": "bar_close",
    "interval": "1d",
    "time": "15:25 IST"
  },
  "rebalance": {
    "type": "daily",
    "max_positions": 10
  },
  "root": { /* condition / action / composite tree */ },
  "risk": {
    "per_trade":  { "stop_loss_pct": 5, "max_qty": 100 },
    "portfolio":  { "max_exposure_pct": 80, "max_concentration_pct": 25 },
    "daily":      { "max_loss_pct": 2, "max_open_positions": 10 }
  }
}
```

## Node families

| Family | Nodes | Returns |
|---|---|---|
| Condition | `compare`, `and`, `or`, `not` | `bool` |
| Action | `buy`, `sell`, `exit`, `hold`, `set_target_weight` | order intent |
| Composite | `if` | dispatches to `then` or `else` |

The v1 evaluator stubs `crossover`, `between`, `select_top_n`,
and `weighted` to `{"type": "hold"}` — full support arrives in
a future slice. Prefer `compare` + `and` patterns instead.

## Universe resolution

`backend/algo/backtest/universe.resolve_universe(user, strategy)`
runs a two-stage pipeline:

1. **`_scoped_tickers(scope)`** — same helper that powers the
   Insights tabs. Pro/superuser get the full platform universe
   for `discovery`; everyone gets `watchlist ∪ holdings` for
   `watchlist`; `portfolio` is holdings only.
2. **`_apply_filter(candidates, markets, ticker_types)`** —
   trims the candidate set by `filter.market` (using
   `detect_market(ticker)`) and `filter.ticker_type` (against
   `stock_master.ticker_type`). `market="all"` short-circuits
   the market gate. Tickers missing from `stock_master` are
   dropped (no OHLCV anyway).

## Visual builder

`frontend/components/algo-trading/builder/StrategyBuilder.tsx`
shows three columns:

| Column | Purpose |
|---|---|
| Left aside | Templates list + node palette (read-only stub in v1) |
| Center | Name input + Levers panel + AST tree view |
| Right aside | JSON pane (read-only with "Paste JSON" escape hatch) |

The tree view (`AstTreeView`) and JSON pane are **read-only by
design**. Editing happens through the Levers panel.

## Strategy Levers panel

Non-technical edit surface. Renders form controls for every
tunable parameter so users can iterate on a strategy without
touching JSON.

### Top-level fields (static controls)

| Group | Field | Control |
|---|---|---|
| Universe | scope | dropdown |
| Universe | filter.market | dropdown (india / us / all) |
| Universe | filter.ticker_type | multi-checkbox (≥1 enforced) |
| Rebalance | max_positions | number 1-50 |
| Risk · per-trade | stop_loss_pct, max_qty | numbers |
| Risk · portfolio | max_exposure_pct, max_concentration_pct | numbers (step 5%) |
| Risk · daily | max_loss_pct, max_open_positions | numbers |

### Inside-tree tunables (auto-discovered)

`strategyTunables.walkTunables(strategy.root)` traverses the
rule tree and yields every numeric leaf the user can sensibly
tune without changing rule logic. Walked node types:

- `set_target_weight.weight` (0..1, step 0.01) → label `"Target weight (× equity)"`
- `buy.qty.shares` / `sell.qty.shares` → label `"Buy/Sell qty (shares)"`
- `compare.right.literal` (numeric only) → label `"<feature> <op> ?"` (e.g. `"rsi < ?"`, `"golden_cross_days_ago <= ?"`)

Each tunable carries a JSON-pointer-ish `path` rooted at
`strategy.root` (e.g. `"cond.operands[2].right.literal"`).
`setByPath` deep-sets the new value with shallow-clone
semantics so React state updates work cleanly.

**Adding a new tunable node type:** append a `visit()` branch
in `strategyTunables.ts` and the lever appears in the panel
automatically across every strategy. No per-strategy code.

## Concrete example: Golden Cross v1

```json
{
  "name": "Golden cross v1",
  "universe": {
    "type": "scope", "scope": "discovery",
    "filter": { "ticker_type": ["stock"], "market": "india" }
  },
  "schedule": { "type": "bar_close", "interval": "1d", "time": "15:25 IST" },
  "rebalance": { "type": "daily", "max_positions": 10 },
  "root": {
    "type": "if",
    "cond": {
      "type": "and",
      "operands": [
        { "type": "compare", "left": {"feature": "today_ltp"},
          "op": ">", "right": {"feature": "sma_50"} },
        { "type": "compare", "left": {"feature": "today_ltp"},
          "op": ">", "right": {"feature": "sma_200"} },
        { "type": "compare", "left": {"feature": "golden_cross_days_ago"},
          "op": "<=", "right": {"literal": 10} }
      ]
    },
    "then": { "type": "set_target_weight", "weight": 0.10 },
    "else": { "type": "exit", "qty": { "all": true } }
  },
  "risk": {
    "per_trade":  { "stop_loss_pct": 5, "max_qty": 1000 },
    "portfolio":  { "max_exposure_pct": 80, "max_concentration_pct": 25 },
    "daily":      { "max_loss_pct": 2, "max_open_positions": 10 }
  }
}
```

The Levers panel surfaces all four user-facing controls:
- Target weight (× equity) → 0.10
- `golden_cross_days_ago <= ?` → 10
- All 7 risk caps + universe scope/market/ticker_type +
  rebalance.max_positions

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/algo/strategies` | List user's strategies |
| GET | `/v1/algo/strategies/{id}` | Single strategy AST |
| POST | `/v1/algo/strategies` | Create |
| PUT | `/v1/algo/strategies/{id}` | Update full AST |
| DELETE | `/v1/algo/strategies/{id}` | Archive (soft-delete via `archived_at`) |

All gated `pro_or_superuser`.

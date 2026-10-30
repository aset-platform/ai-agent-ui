# RSI(14) Trend-Pullback Swing Strategy — Design

Date: 2026-09-05

## Purpose

A new strategy template for 3-9 month trend-following swing trades,
evaluated first via backtest. Sibling to the existing RSI(2) Connors
mean-reversion family, but on a materially longer horizon: enters on
a shallow RSI(14) pullback within an established uptrend, exits on
trend failure rather than a fixed hold time.

## Entry logic

All conditions AND'd (universe → pullback → reversal → confirmation,
per the user's original 4-stage spec):

| Stage | Rule | AST condition | Feature |
|---|---|---|---|
| Universe (trend) | Close > SMA200 | `distance_from_sma200 > 0` | existing |
| Universe (trend) | SMA50 > SMA200 | `sma_50 > sma_200` | existing |
| Universe (trend) | SMA200 slope > 0 | `sma200_slope > 0` | existing |
| Pullback | RSI14 < 50 | `rsi_14 < 50` | existing |
| Reversal | RSI14[t] > RSI14[t-1] | `rsi_14_delta_1bar > 0` | **new** |
| Confirmation | Close > High[1] | `dist_from_prev_day_high_pct > 0` | existing |

On all 6 true: `set_target_weight(0.20)`.

## Exit logic

Evaluated when entry conditions are false and a position is open:

| Rule | AST condition |
|---|---|
| Major trend failure (immediate, overrides RSI) | `distance_from_sma200 < 0` |
| Soft trend weakness | `bars_below_sma50 >= 2 AND rsi_14 < 45` |

Either true → `exit(this_symbol)`. Otherwise `hold`.

## Full AST shape

```
root:
  if:
    cond: AND(
      distance_from_sma200 > 0,
      sma_50 > sma_200,
      sma200_slope > 0,
      rsi_14 < 50,
      rsi_14_delta_1bar > 0,
      dist_from_prev_day_high_pct > 0,
    )
    then: set_target_weight(0.20)
    else:
      if:
        cond: OR(
          distance_from_sma200 < 0,
          AND(bars_below_sma50 >= 2, rsi_14 < 45),
        )
        then: exit(this_symbol)
        else: hold
```

## New features

Both computed in `backend/algo/features/daily_engine.py::compute_daily_features`,
alongside the existing `rsi_14`, `sma_50`, etc. computation for the
same bar series — no new data source, no new persistence path. They
flow to backtest/paper/live automatically via the existing
`daily_features_daily_compute` scheduled job (per the auto-discovery,
no-whitelist persistence pattern already governing this table).

### `rsi_14_delta_1bar`

- Type: float, source: `technical`, no `scale` (raw oscillator delta).
- Formula: `rsi_14[i] - rsi_14[i-1]`. `None` if either side is `None`
  (RSI not yet warm, per `wilder_rsi`'s existing warmup behavior) or
  `i == 0`.
- Generic 1-bar momentum-delta primitive — reusable for any future
  "oscillator turning up/down" rule, not RSI(14)-specific in
  implementation (parametrize by the already-computed `rsi_14` series
  the function holds).

### `bars_below_sma50`

- Type: int, source: `technical`, no `scale` (raw count).
- Semantics: consecutive count of daily bars (ending at and including
  the current bar) where `close < sma_50`. Resets to 0 the bar the
  close is `>= sma_50`. Mirrors `golden_cross_bars_ago`'s existing
  "counter that resets on state flip" shape in the same function.
- `None`/absent while `sma_50` itself is not yet warm (first 50 bars).

## Catalog + registry changes

- `backend/algo/strategy/features.py`: add both `Feature(...)` entries
  under the existing Technical section, `source="technical"`.
- `frontend/components/algo-trading/strategyFeatureCatalog.ts`: mirror
  both entries (kept in sync by `test_feature_registry_sync.py`).
- `backend/algo/strategy/feature_warmup.py`: `rsi_14_delta_1bar` → 15
  (14 for RSI itself + 1 prior bar), `bars_below_sma50` → 50 (needs
  `sma_50` warm).

## Template file

`backend/algo/strategy/templates/rsi14_trend_pullback_swing_v1.json`

- `universe`: `{"type": "scope", "scope": "discovery", "filter": {"ticker_type": ["stock"], "market": "india"}}`
- `schedule`: `{"type": "bar_close", "interval": "1d", "time": "15:25 IST"}`
- `rebalance`: `{"type": "daily", "max_positions": 5}`
- `product`: `"CNC"`
- `risk`:
  - `per_trade`: `{"stop_loss_pct": 8.0, "max_qty": 10000}`
  - `portfolio`: `{"max_exposure_pct": 100.0, "max_concentration_pct": 25.0}`
  - `daily`: `{"max_loss_pct": 5.0, "max_open_positions": 5}`

No `_research_` filename tag — that convention is reserved for the
`mid_trade_regime_check` opt-in trap (mean-reversion strategies must
not enable it), which this strategy does not touch. This template is
a normal new-strategy candidate, backtest-first like RSI(2) v1 was.

## Testing

- Unit tests for both new primitives in the `daily_engine.py` test
  module: happy path (delta sign correct across a rise/fall pair,
  counter increments/resets correctly across a below/above/below
  sequence) + not-yet-warm case (both features absent/`None` when
  their dependency SMA/RSI isn't warm yet).
- One sample backtest case exercising the new template end-to-end
  (per the `daily_engine.py` module docstring's existing "step 4"
  convention for shipping a non-obvious feature), confirming the
  entry/exit AST evaluates as expected against a small synthetic bar
  series with a known pullback-and-reversal shape.
- `test_feature_registry_sync.py` covers catalog/frontend-mirror
  drift automatically — no new test needed there.

## Out of scope

- No paper/live promotion in this pass — backtest-research only, per
  the `walkforward-dsr-paper-promotion` gate convention (walk-forward
  CV with DSR ≥ 0.95 before any promotion is considered).
- No changes to the intraday feature engine (`engine.py`) — this
  strategy is daily-cadence only.
- No F&O / MIS universe handling — plain equity `CNC` swing, same as
  the RSI(2) Connors family.

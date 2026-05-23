# RSI(2) Connors Daily v1 — §6.4 G4 Tune Attempt (stop_loss 5% → 3%, ex-DIACABS)

| | |
|---|---|
| Date | 2026-05-23 |
| Predecessors | docs/research/2026-05-22-rsi2-connors-daily-baseline.md, docs/research/2026-05-23-rsi2-connors-ex-diacabs.md |
| Tag | rsi2-connors-daily-stop3-ex-diacabs |
| Change | stop_loss_pct: 5.0 → 3.0 (spec §6.4 G4 single-iteration tune) |

## Three-way comparison

| Gate | Threshold | Baseline (w/ DIACABS) | Ex-DIACABS | stop3 + ex-DIACABS | Pass (final) |
|---|---|---|---|---|---|
| G1: Trade count | ≥ 200 | 600 | 988 | 988 | PASS |
| G2: CAGR | ≥ 8% | 8.66% | 8.44% | 8.44% | PASS |
| G3: Win rate (ex-stops) | ≥ 60% | 60.67% | 62.85% | 62.85% | PASS |
| G4: Max drawdown | ≤ 15% | -24.26% | -19.89% | -19.89% | **FAIL** |
| G5: Concentration | ≤ 20% | 101.22% | 12.62% | 12.62% | PASS |

## Verdict

**G4 still failing despite tune — stop_loss_pct has no effect because stop enforcement is not yet implemented in the backtest runtime.**

## Root cause finding

`stop_loss_pct` is stored in `StrategyRisk.per_trade` (AST schema) and serialised into the `risk_payload` dict, but `RiskEngine.gate()` in `backend/algo/paper/risk_engine.py` never reads the field. The backtest runner has no stop-check loop that compares open-position drawdown against the configured threshold and force-exits the position. As a result, changing `stop_loss_pct` 5.0 → 3.0 produced byte-identical results:

- trades: 988 → 988
- CAGR: 8.44% → 8.44%
- win_rate: 62.85% → 62.85%
- max_drawdown: -19.89% → -19.89%

`win_rate_pct == win_rate_summary_pct` (both 62.85) confirms zero stop_loss exit_reason rows were generated — the G3 ex-stops filter was a no-op.

A second run was made applying weight reduction (0.20 → 0.15) as the alternative §6.4 option. That run produced different trade count (550 vs 988), much higher CAGR (21.6%), but failed BOTH G4 (-18.06%) and G5 (JAIBALAJI.NS at 61.04%), surfacing a concentration outlier masked at 0.20 weight. The weight-reduced template was reverted; only `stop_loss_pct 3.0` remains committed.

## Numerical commentary

- New DD: -19.89% (unchanged from ex-DIACABS run — stop not enforced)
- Delta in win rate: 0 (no stop-out exits generated)
- Delta in trade count: 0 (988)
- Delta in CAGR: 0 (8.44%)
- Template edit committed: `stop_loss_pct 3.0` is present and correct for when stop enforcement is wired; the test asserting `stop_loss_pct == 3.0` passes.

## Next action

File a v2 spec ticket with two structural fixes required in parallel:

1. **Wire stop enforcement in the backtest runner** — add a per-bar open-position sweep that compares `(entry_price - current_low) / entry_price` against `risk.per_trade.stop_loss_pct / 100`; emit exit at that bar's open with `exit_reason="stop_loss"`. Without this, any stop_loss_pct tune is cosmetic.

2. **Add ADTV filter to the universe** — the weight reduction run exposed that JAIBALAJI.NS (low-float, illiquid) dominates P&L at lower position sizes. A structural `adtv_5cr_filter` (≥ ₹5 Cr 30d ADTV) in the universe spec removes this class of outlier before sizing decisions.

Until both fixes land, paper promotion is **blocked on G4**. The `stop_loss_pct 3.0` field in the template is forward-correct and should be kept.

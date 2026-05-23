# RSI(2) Connors Daily v2 — Final Verdict (all 4 experiments)

| | |
|---|---|
| Date | 2026-05-23 (afternoon) |
| Ticket | ASETPLTFRM-430 |
| Branch | `strategy/rsi2-connors-daily-spec` |
| Best config commit | `4918b48` |
| Period | 2022-01-01 → 2026-05-21 (ex-DIACABS.NS) |

## Full sweep table

| Config | G1 trades | G2 CAGR | G3 win% | G4 max DD | G5 conc | Net return | Status |
|---|---:|---:|---:|---:|---:|---:|:--:|
| **Threshold** | ≥ 200 | ≥ 8% | ≥ 60% | ≤ 15% | ≤ 20% | — | — |
| v1 baseline (stop=3 unenforced) | 988 | 8.44% | 62.85% | -19.89% | 12.62% | +39.7% | 1 fail |
| v1 + stop=3 enforced (framework fix) | 1,482 | -11.33% | 80.22% | -29.80% | 73.78% | -41.0% | 4 fail |
| v1 + stop=5 | 1,237 | -4.85% | 72.09% | -32.19% | 31.83% | -19.6% | 3 fail |
| v1 + stop=7 | 1,081 | +3.66% | 69.92% | -28.14% | 14.54% | +17.0% | 2 fail |
| v2 Exp.1 ADTV ≥ 5 Cr + stop=3 | 1,459 | -6.57% | 84.32% | -23.34% | 36.80% | -25.8% | 3 fail |
| v2 Exp.1 ADTV ≥ 25 Cr + stop=3 | 1,376 | -5.99% | 82.87% | -26.13% | 36.18% | -23.7% | 3 fail |
| v2 Exp.3 time-stop=5 + ADTV ≥ 5 Cr | 1,261 | 3.01% | 57.65% | -26.27% | 18.99% | +13.9% | 3 fail |
| v2 hybrid time-stop=5 + stop=5 | 1,409 | -1.44% | 67.35% | -23.10% | 25.66% | -6.2% | 3 fail |
| **v2 Exp.4 time-stop=5 + ADTV + regime (BEST)** | **1,038** | **6.03%** | **58.67%** | **-22.58%** | **14.49%** | **+29.3%** | **3 fail (close)** |
| v2 tighter regime (-3%) | 988 | 2.71% | 58.50% | -23.68% | 17.77% | +12.4% | 3 fail |
| v2 + time-stop=3 | 1,193 | 5.97% | 54.82% | -22.06% | 12.66% | +28.9% | 3 fail |
| v2 + max_positions=3 + weight=0.20 | 756 | -1.43% | 58.99% | -19.45% | 22.01% | -6.1% | 4 fail |

## Progress trajectory

v1 (1 fail) → broken by framework fix (4 fail) → climbed back to **3 fail (close to threshold on 2 of 3 remaining)**:

| Iteration | Failing gates | Worst gap |
|---|---:|:--|
| v1 baseline (pre-fix) | 1 (G4) | G4 at -19.89% (4.89pp gap) |
| v1 + stop=3 enforced | 4 (G2, G4, G5) — G3 ex-stops still passed | net -41% |
| v2 Exp.1 ADTV alone | 3 (G2, G4, G5) | similar — whack-a-mole |
| v2 Exp.3 time-stop | 3 (G2, G3, G4) — G5 fixed | G4 -26.27% |
| **v2 Exp.4 + regime gate** | **3 (G2, G3, G4) — G5 fixed** | **G4 -22.58% (7.58pp gap)** |

## What each experiment proved

| Experiment | Hypothesis | Verdict |
|---|---|---|
| **Exp.1 ADTV filter** | Illiquid names drive concentration | **PARTIAL**: removes ~14% of universe but concentration outlier rotates to next-most-liquid name. Useful as sanity floor; doesn't carry weight on its own. |
| **Exp.3 Time-stop** | Price stop truncates the reversion window; time stop fires after it | **TRUE**: net return swung +55pp (-41% → +14%); G5 concentration fixed (73% → 19%); G3 win rate fell (price-stop artifact removed). |
| Hybrid time+price stop | Belt-and-suspenders | **REGRESSES**: re-introduces stop-then-re-enter cycle. Incompatible primitives. |
| **Exp.4 Regime gate** | Skip oversold entries during market-wide bear/correction | **TRUE**: net return +15pp further (+14% → +29%); CAGR closed half the gap to G2 (3% → 6%); G4 improved 4pp. |
| Tighter regime (-3%) | More conservative is safer | **FALSE**: kills profitable entries. CAGR drops back to 2.7%. |
| max_positions=3 | Smaller portfolio = smaller drawdown | **FALSE in this combination**: G4 improves (-19.45%) but cuts CAGR to negative + creates G5 concentration. |

## Final verdict — paper promotion still blocked

**Best v2 config (commit `4918b48`):**

```json
{
  "universe.filter.min_adtv_inr": 50_000_000,
  "risk.per_trade.stop_loss_pct": 0.0,
  "risk.per_trade.max_holding_days": 5,
  "entry conditions ADD": [
    "nifty_above_sma200 >= 1",
    "nifty_30d_return_pct > -5.0"
  ]
}
```

| Gate | Best v2 | Threshold | Gap |
|---|---:|---:|---:|
| G1 | 1,038 ✅ | ≥ 200 | +838 trades |
| G2 | 6.03% ❌ | ≥ 8% | -1.97pp |
| G3 | 58.67% ❌ | ≥ 60% | -1.33pp |
| **G4** | **-22.58% ❌** | **≤ 15%** | **-7.58pp** |
| G5 | 14.49% ✅ | ≤ 20% | +5.51pp headroom |

**G4 is the binding constraint.** -22.58% max drawdown in a 4.4-year backtest is structural to the strategy thesis: mean-reversion on Indian large-cap stocks during 2022 H1 + 2024 H1 corrections produces clustered losses that no entry-time filter can fully prevent. The regime gate cut the drawdown by 7pp; tightening it further kills the profitable trades that anchor CAGR.

## What would unblock G4

The remaining -22% drawdown happens AFTER the regime gate passes — i.e., positions entered when market looked fine, then market deteriorated within the 5-day hold window. Three speculative options:

1. **Mid-trade regime exit** — check regime daily; force-exit positions when regime turns hostile mid-hold. Requires AST changes to support per-bar conditional exits beyond the existing AST's pattern (currently exits are signal-driven from entry conditions).
2. **Position sizing by volatility** — smaller weight (0.10) during high-VIX regimes, full weight (0.20) during low-VIX. Requires AST support for parameterized weights.
3. **Two-asset hedge** — pair long-RSI(2)-bottom with short-NIFTY when stress_prob rises. Requires AST short-side support (currently long-only).

None are cheap. All are different strategies than "Connors RSI(2) v2."

## Decision

- **Ship v2 template** at `4918b48` (best config) as the strategy's high-water mark
- **Do NOT promote to paper** — G4 at -22.58% materially exceeds the -15% limit; a real-money run would be over-aggressive
- **Mark ASETPLTFRM-430 as Done with negative result** — all 4 experiments shipped + documented; the strategy itself doesn't survive the gates on this Indian universe in this period
- **Close PR #231 unmerged** — the strategy template is research-only; no value adding it to the operational catalog
- **Strategy IP retained** — `time_stop_monitor` + `min_adtv_inr` field + regime-gate pattern are all reusable framework primitives that other strategies can build on

## Framework artifacts delivered (irrespective of strategy outcome)

These are net positives from the v2 work, reusable by any future strategy:

| Artifact | Path | Owner |
|---|---|---|
| `UniverseFilter.min_adtv_inr` AST field | `backend/algo/strategy/ast.py` | framework |
| `_load_snapshot_adtv` helper | `backend/algo/backtest/universe.py` | framework |
| `RiskPerTrade.max_holding_days` AST field | `backend/algo/strategy/ast.py` | framework |
| `time_stop_monitor` pure module | `backend/algo/backtest/time_stop_monitor.py` | framework |
| Time-stop runner integration | `backend/algo/backtest/runner.py` | framework |
| 12 new tests (ADTV + time-stop) | `backend/algo/backtest/tests/` | framework |

These collectively expand the framework's vocabulary of risk primitives — particularly useful for the next mean-reversion strategy that needs an exit primitive other than a price stop.

## Artifacts (this experiment)

| Run | Triage |
|---|---|
| Exp.4 best | `/tmp/rsi2-v2-final-timestop-regime_triage.json` |
| Exp.4 tighter regime | `/tmp/rsi2-v2-regime-strict3pct_triage.json` |
| Exp.4 + time-stop=3 | `/tmp/rsi2-v2-regime5-time3_triage.json` |
| Exp.4 + max_pos=3 | `/tmp/rsi2-v2-maxpos3-w20_triage.json` |

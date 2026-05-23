# RSI(2) Connors Daily v4 — Mid-Trade Regime Exit (Negative Result)

| | |
|---|---|
| Date | 2026-05-23 (evening) |
| Ticket | ASETPLTFRM-435 |
| Branch | `strategy/rsi2-connors-v4-mid-trade-regime` |
| v3 baseline | `docs/research/2026-05-23-rsi2-connors-v3-final.md` |
| Period | 2022-01-01 → 2026-05-21 (4.4yr) |
| Universe | NSE discovery ∩ ADTV ≥ ₹5 Cr ∩ warmup, ex-DIACABS = 508 tickers |

## TL;DR — negative result

Mid-trade regime exit (force-close all open positions when the broader market regime turns hostile) REGRESSES every gate except G3:

| Gate | v3 (production) | v4 cd=-5% (canonical) | v4 cd=-10% (looser) |
|---|---:|---:|---:|
| G1 trades | 1,073 ✅ | 1,094 ✅ | 1,091 ✅ |
| G2 CAGR | **8.95% ✅** | **1.16% ❌** | **1.81% ❌** |
| G3 win% ex-stops | 67.68% ✅ | 66.10% ✅ | 65.74% ✅ |
| **G4 max DD** | **−14.63% ✅** | **−16.60% ❌** | **−16.71% ❌** |
| G5 concentration | 9.7% ✅ | 20.37% ❌ (APOLLO) | 17.69% ✅ |
| Net return | **+45.6%** | **+5.18%** | +8.19% |
| Gates failed | **0** | **3** | **2** |

## Why it regresses — the structural mismatch

Mid-trade regime exit was designed to attack the residual v3 max-DD floor by force-closing positions before cluster-decay completed. Empirically, **max DD got *worse*** (−14.63% → −16.60%). Counter-intuitive at first; structurally inevitable.

**Mean-reversion strategies live on regime-hostile days.** Connors-style RSI(2) buys oversold stocks. Oversold conditions coincide with regime-hostile signals (`nifty_above_sma200 = 0`, `nifty_30d_return_pct < -5`). The strategy thesis is "regime is bad, individual names are even worse, expect snap-back to mean."

When the mid-trade regime exit fires, it's force-closing exactly the trades that are about to revert profitably. The exits land near the bottom of the swing — we sell when others sell, then miss the rebound.

The regime gate at ENTRY time is correct: don't OPEN new exposures during panic. But once you're in, your thesis IS "panic resolves." Pulling the position during panic violates the entry premise.

## Three datapoints — all negative

| Variant | net return | G2 CAGR | G4 DD | regime-exit fires |
|---|---:|---:|---:|---:|
| v3 (no mid-trade exit) | +45.6% | 8.95% | -14.63% | 0 |
| v4 cd=-5% | +5.18% | 1.16% | **-16.60%** | 66 |
| v4 cd=-10% | +8.19% | 1.81% | **-16.71%** | small |

Loosening the threshold reduces the firing frequency but the small number of fires that DO occur still land at bad times (because the threshold is calibrated to catch broad-market panics — exactly when RSI(2) wants to be buying). Even a single misplaced portfolio-wide kill at the wrong day eats the cumulative edge.

## Verdict on this hypothesis

**v4 mid-trade regime exit does not work for RSI(2) Connors.** This is well-supported by data, not a calibration miss. Hypothesis is rejected for this strategy class.

The mechanism MIGHT work for trend-following or breakout strategies where the thesis is "ride the trend, exit when trend breaks" — in those classes regime-hostile is the actual broken-thesis signal. RSI(2) is the opposite — regime-hostile is the buying signal.

## What's still on the table for v4 follow-ups

The ticket listed three sub-experiments. E1 (this work) is now a confirmed negative result. Two remain:

### E2 — Vol-adjusted position sizing (DIFFERENT mechanism)

Instead of force-exiting positions during regime flips, **size positions smaller when regime is hostile**. Same trades fire, smaller exposure during cluster-decay days. Different machinery — needs `set_target_weight` parameterized by a feature reference.

Conservative estimate: G4 improves 2-3 pp from smaller positions on bad days; CAGR may dip 1-2 pp from smaller positions on good days too. Net could land at all-5-pass.

### E3 — Trailing stop activated by profit (different mechanism)

Once a position crosses +2% intraday, raise the stop to entry. Locks in winners that would otherwise reverse during a regime flip. Per-position state (high-water mark since entry), not portfolio-wide.

Conservative estimate: avg_win goes up (winners locked in), G4 improves (no more "winner → loser" reversals), G2 may dip slightly (some slow reverters get stopped at break-even instead of completing).

Both are different ticket scopes than this v4. File separately if pursued.

## Framework artifact retained

Even though the strategy-level result is negative, the framework primitive shipped is reusable:

| Primitive | Path | Reusable by |
|---|---|---|
| `Strategy.mid_trade_regime_check: ConditionNode \| None` | `backend/algo/strategy/ast.py` | Any trend-following / breakout strategy |
| `backend/algo/backtest/regime_exit_monitor.py` | (new) | Same — pure module |
| Runner integration (per-bar regime evaluation) | `runner.py` | All future strategies |
| Cooldown vocab extended to `regime_exit` | `cooldown_monitor.py` | All strategies using cooldown |

These were correct to build. The v4 template demonstrates them but documents the negative result so others don't repeat the experiment on mean-reversion strategies.

## Decision

- **Production strategy remains RSI(2) v3** (paper-promotion-ready, all 5 gates pass)
- **v4 template retained for reproducibility** of the negative result (research-only; not promotion candidate)
- **ASETPLTFRM-435 closes as Done with negative result + framework win**
- **v5 / future iterations**: vol-adjusted sizing (E2) or trailing stop (E3) — file separate tickets if pursued

## Artifacts

| Tag | Triage |
|---|---|
| v3 (production baseline) | `/tmp/v3-cd7_triage.json` |
| v4 -5% (canonical) | `/tmp/rsi2-v4-mid-trade-regime_triage.json` |
| v4 -10% (looser) | `/tmp/rsi2-v4-loose-threshold_triage.json` |

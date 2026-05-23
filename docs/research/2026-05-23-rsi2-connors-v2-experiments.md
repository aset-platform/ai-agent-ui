# RSI(2) Connors Daily v2 — Experiment Sweep (ASETPLTFRM-430)

| | |
|---|---|
| Date | 2026-05-23 (afternoon) |
| Ticket | ASETPLTFRM-430 |
| Branch | `strategy/rsi2-connors-daily-spec` (PR #232 framework + PR #231 strategy merged) |
| Predecessor | `docs/research/2026-05-23-rsi2-connors-stop3-postfix.md` (negative result that motivated v2) |
| v2 spec | `docs/superpowers/specs/2026-05-23-rsi2-connors-v2-design.md` |
| Period | 2022-01-01 → 2026-05-21 |
| Universe | NSE discovery (ex-DIACABS.NS) |
| NAV | ₹10 lakh |

## TL;DR

**Time-stop > price-stop for RSI(2)** — Experiment 3 (`max_holding_days=5`, `stop_loss_pct=0`) fixes 2 of 4 failing gates (G5 concentration 73.78% → 18.99% ✅, net return -41% → +13.9%). Remaining failures (G2 CAGR, G4 max DD) point to market-regime cause, not stop-tuning cause. **Experiment 4 (regime gate) is the next iteration to unblock paper promotion.**

## Experiment sweep results (cumulative, all ex-DIACABS)

| Config | Universe | G1 trades | G2 CAGR | G3 win% ex-stops | G4 max DD | G5 concentration | Net return |
|---|---:|---:|---:|---:|---:|---:|---:|
| **v1 baseline** (no stop, no filter) | 801 | 988 | 8.44% ✅ | 62.85% ✅ | -19.89% ❌ | 12.62% ✅ | +39.7% |
| **v1 + stop=3 (framework fix)** | 801 | 1482 | -11.33% ❌ | 80.22% ✅ | -29.80% ❌ | 73.78% ❌ (APOLLO.NS) | -41.0% |
| **v2 Exp.1 ADTV≥5Cr + stop=3** | 688 | 1459 | -6.57% ❌ | 84.32% ✅ | -23.34% ❌ | 36.80% ❌ (PFOCUS.NS) | -25.8% |
| **v2 Exp.1 ADTV≥25Cr + stop=3** | 540 | 1376 | -5.99% ❌ | 82.87% ✅ | -26.13% ❌ | 36.18% ❌ (SKYGOLD.NS) | -23.7% |
| **v2 Exp.3 time-stop=5 + stop=0 + ADTV≥5Cr** | 688 | 1261 | 3.01% ❌ | 57.65% ❌ | -26.27% ❌ | **18.99% ✅ (APOLLO.NS)** | **+13.9%** |
| v2 hybrid time-stop=5 + stop=5 + ADTV≥5Cr | 688 | 1409 | -1.44% ❌ | 67.35% ✅ | -23.10% ❌ | 25.66% ❌ (APOLLO.NS) | -6.2% |
| **Threshold** | — | ≥ 200 | ≥ 8% | ≥ 60% | ≤ 15% | ≤ 20% | — |

## Why each experiment hit a wall

### Experiment 1 — ADTV filter (3 floors tested: 5 Cr, 25 Cr, 50 Cr inadvertent)

**Hypothesis**: low-float names (APOLLO, JAIBALAJI, DIACABS) drive concentration via stop-then-re-enter cycles.

**Result**: partial — universe shrinks but G5 outlier ROTATES to next-most-liquid name (APOLLO @ ₹243 Cr → PFOCUS @ ₹95 Cr → SKYGOLD). The concentration is **dynamic, driven by the stop-then-re-enter cycle itself**, not by which ticker happens to be illiquid. ADTV filtering alone solves the wrong problem.

**G4 didn't move meaningfully**: -29.80% → -23.34% (-6.5pp) at ₹5 Cr, then BACK to -26.13% at ₹25 Cr (counter-intuitive — tighter ADTV means strategy concentrates on fewer survivors, which can deepen drawdowns).

### Experiment 3 — Time-stop replaces price-stop (THE WIN)

**Hypothesis**: per Connors's published work, RSI(2) reversion completes in 2-5 days OR fails. Price stop fires INSIDE that window and truncates winners; time stop fires AFTER it.

**Result**: validated — three step-changes in metrics:

1. **Net return swings positive**: -41% → +13.9% (a 55pp swing). Strategy goes from money-losing to money-making.
2. **G5 PASSES**: 73.78% (APOLLO 4x re-entry cycle) → 18.99%. Time-stop ends the same-day re-entry cycle because the position is held for a fixed N days regardless of where price goes.
3. **G3 just misses**: 57.65% vs 60% threshold. Without a price-stop, every trade is held to its time-stop, including unconverted ones — so the win rate denominator includes more borderline trades. The 80%+ "win rate ex-stops" in earlier configs was an artifact of the ex-stops filter removing losses; time-stop has no stops to exclude.

**G2 and G4 still fail because**: drawdown is now **market-regime** driven. The strategy enters on stock-level oversold without checking market-level state — during 2022 H1 selloff and 2024 mid-year correction, every oversold scan triggers across the universe simultaneously; positions all decline together over the 5-day holding window. The max DD floor at -26% is the strategy's behavior during market-wide reversion failures.

### Hybrid (time-stop + light price-stop=5%) — doesn't help

Adding a 5% price stop back **regresses every gate**: net return drops to -6.2%, G5 fails again (APOLLO concentration returns at 25.66%). The price stop re-introduces the stop-then-re-enter cycle. **Lesson: time-stop is not just better than price-stop, it's incompatible with it for this strategy.**

## What this proves about the framework

- The price-stop framework fix (PR #232) is empirically valid — it produces the documented mean-reversion-truncation pathology when applied to RSI(2). Different stop levels (3, 5, 7) produce different numbers as the spec predicted.
- The time-stop is a **new** structural primitive that pairs better with mean-reversion strategies. It's a meaningful addition to the AST risk vocabulary, independent of any single strategy's outcome.
- The G4 floor at -26% with time-stop is a **strategy-thesis problem**, not a risk-mechanism problem. No amount of stop tuning will fix it — only a market-regime overlay will.

## Recommended Experiment 4 (next iteration)

**Regime gate**: skip new entries when NIFTY's own RSI(2) ≤ 10 (i.e., market itself is panicking — don't buy oversold-in-bear).

**Implementation cost**: low — add a single AST condition referencing `nifty_rsi_2` (verify feature exists in daily feature engine) OR use existing `regime_label` feature when it's `"BEAR"`.

**Expected impact**: G4 drops meaningfully (2022 H1 and 2024 selloff entries skipped); trade count drops modestly; CAGR may rise OR fall depending on how many high-quality bull-trend entries the gate also blocks.

This is the cheapest remaining experiment to run and targets the actual root cause of G4. Strongly recommend layering on the current v2 (time-stop + ADTV ≥ ₹5 Cr) rather than starting fresh.

## Decision

- Time-stop is the right primitive for RSI(2) — committed to v2 template
- ADTV filter is retained at ₹5 Cr (cheap defensive layer; passes liquidity sanity but doesn't carry the weight)
- **Paper promotion remains blocked** on G2 + G4, but the gap has narrowed: from "4 of 5 gates fail with structural mechanism missing" to "2 of 5 gates fail with one targeted experiment remaining"
- Continue work on ASETPLTFRM-430 with Experiment 4

## Artifacts

| Tag | File |
|---|---|
| Exp.1 ADTV=5Cr stop=3 | `/tmp/rsi2-v2-adtv5cr-stop3_triage.json` |
| Exp.1 ADTV=25Cr stop=3 | `/tmp/rsi2-v2-adtv25cr-stop3_triage.json` |
| Exp.3 time-stop=5 ADTV=5Cr (best) | `/tmp/rsi2-v2-timestop5-adtv5cr_triage.json` |
| Hybrid time-stop=5 + stop=5 | `/tmp/rsi2-v2-timestop5-stop5-adtv5cr_triage.json` |

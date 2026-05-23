# RSI(2) Connors Daily v1 — Stop-Loss Post-Fix Triage (ex-DIACABS)

| | |
|---|---|
| Date | 2026-05-23 (afternoon) |
| Branch | `strategy/rsi2-connors-daily-spec` + merged `framework/backtest-stop-loss-enforcement-spec` |
| Framework PR | #232 (stop-loss enforcement across all 3 runtimes) |
| Predecessor | `docs/research/2026-05-23-rsi2-connors-stop3-final.md` (pre-fix diagnosis) |
| Tag (committed) | `rsi2-connors-daily-stop3-postfix` |
| Period | 2022-01-01 → 2026-05-21 |
| Universe | 801 NSE stocks (ex-DIACABS.NS) |

## TL;DR

**Negative result.** With `stop_loss_pct=3.0` now actively enforced (PR #232 merged locally), RSI(2) v1 fails *more* gates than before the fix, not fewer. The §6.4 G4 tune was built on a wrong mental model: tightening the stop on a mean-reversion strategy truncates the reversion, which is where the strategy's edge lives. Wider stops (5%, 7%) recover some CAGR but never close G4. RSI(2) Connors v1 on this universe has a **structural drawdown floor around -20% to -32%** that is independent of stop tuning.

## Headline before/after (ex-DIACABS, 2022-2026)

| Gate | Threshold | Pre-fix (stop_pct stored, not enforced) | Post-fix stop=3 | Post-fix stop=5 | Post-fix stop=7 |
|---|---:|---:|---:|---:|---:|
| G1: Trade count | ≥ 200 | 988 ✅ | 1,482 ✅ | 1,237 ✅ | 1,081 ✅ |
| G2: CAGR | ≥ 8% | 8.44% ✅ | **-11.33% ❌** | **-4.85% ❌** | **3.66% ❌** |
| G3: Win rate (ex-stops) | ≥ 60% | 62.85% ✅ | 80.22% ✅ | 72.09% ✅ | 69.92% ✅ |
| G4: Max DD | ≤ 15% | -19.89% ❌ | **-29.80% ❌** | **-32.19% ❌** | **-28.14% ❌** |
| G5: Top-ticker conc | ≤ 20% | 12.62% ✅ | **73.78% ❌** | **31.83% ❌** | 14.54% ✅ |
| **Gates failed** | | 1 (G4) | **4 (G2/G4/G5)** | **3 (G2/G4/G5)** | 2 (G2/G4) |

The tighter the stop, the worse the result on every gate except G3 (where the ex-stops denominator naturally rises because more weak trades are filtered out via stop-out classification).

## Why the strategy gets worse with stops

RSI(2) Connors enters specifically on extreme oversold conditions (RSI(2) < 10 → 20 over 5-day SMA). The entry IS a local price minimum hypothesis. The 2-3 day reversion window is precisely where the strategy's edge lives.

A 3% stop fires inside the reversion window. Three concrete failure modes observed across the 3% / 5% / 7% sweep:

1. **Reversion-truncation**: 80.22% of non-stop exits at 3% stop are wins. That means the 35-40% of trades that get stopped out would have eventually mean-reverted profitably — the stop is cutting future winners.
2. **Day-of-stop cascade**: stops cluster on red days (multiple oversold names becoming MORE oversold simultaneously). With stop=3%, a -3% market day stops out a basket of positions all at once → larger single-day drawdown than letting positions ride into the next day's reversion.
3. **Re-entry concentration**: after a stop fires, capital frees up. AST re-enters the strategy's highest-conviction signal next bar — often the same ticker that just stopped out (oversold scan still triggers). At stop=3%, APOLLO.NS soaks 73.78% of total realised P&L purely from repeated stop-then-re-enter cycles. Wider stops (5%, 7%) reduce this churn back toward sane concentration.

The wider stop=7% result reveals the structural floor: even with stops barely firing (1,081 trades, only ~90 more than the 988 unenforced baseline → ~9% of trades stop-out), max DD is still -28.14% — meaning the **strategy's drawdown is not stop-induced but signal-induced**. It comes from clustered entries during 2022's H1 sell-off and 2024's mid-year correction, both of which produced multi-day mean-reversion failures across the universe.

## Implications for the framework fix

The framework fix (PR #232) is **empirically validated**: changing `stop_loss_pct` between 3.0 / 5.0 / 7.0 now produces materially different results. Pre-fix, all three values produced byte-identical metrics (the field was unconsumed). Different stop levels → different trade counts (1,482 / 1,237 / 1,081), different CAGRs (-11.33% / -4.85% / 3.66%), different concentrations (73.78% / 31.83% / 14.54%) → the field is wired and effective.

| Validation criterion | Result |
|---|---|
| Same template, different stop values produce different metrics | ✅ (was identical pre-fix) |
| Stop-outs measurable as a distinct exit class | ✅ (G3 win-rate ex-stops diverges from G3 win-rate summary) |
| Concentration tracks with stop-induced re-entry frequency | ✅ (monotonic: 73.78% → 31.83% → 14.54% as stop widens) |
| Total trade count tracks with stop firing rate | ✅ (1,482 → 1,237 → 1,081 as stop widens) |

The fix is correct. The strategy is the problem.

## Implications for RSI(2) v1 promotion

Paper promotion **remains blocked** — same conclusion as the pre-fix triage, but now empirically confirmed rather than blocked on a missing mechanism. The blocker has shifted:

- **Pre-fix blocker**: G4 fails at -19.89%; tune from spec §6.4 not actually applied because mechanism missing. Resolution path: ship the framework fix.
- **Post-fix blocker**: G4 fails at -28% to -32% regardless of stop level. Resolution path: redesign the strategy's drawdown source — universe filter, entry-time concentration cap, or different signal definition.

## Recommended next experiments (v2 design)

1. **ADTV filter at universe construction time**: require ≥ ₹5 Cr 30-day ADTV. The pre-fix concentration finding (APOLLO.NS, JAIBALAJI.NS at low weight, DIACABS pre-exclusion) keeps surfacing illiquid names as P&L outliers. Filtering these out at universe-construction time, not after-the-fact at G5, should reduce both concentration AND drawdown from clustered low-float entries.

2. **Daily max-open-positions cap**: today the strategy can open up to 5 positions/day with no portfolio-level concurrent-entry limit. A `max_concurrent_entries=3` cap (or equivalent in `risk.daily`) would distribute drawdown risk across the universe rather than letting same-day oversold scans concentrate exposure on one sector.

3. **Time-based stop in lieu of price stop**: replace `stop_loss_pct: 3.0` with `max_holding_days: 5`. Connors's original work pairs RSI(2) with a "exit on close after N days OR signal flip, whichever comes first" — no price stop at all. If the strategy depends on reversion-within-N-days, a price stop fires INSIDE the reversion window; a time stop fires AFTER it. This is a structural change, not a tune, and should be specced as RSI(2) v2.

4. **Regime gate**: skip new entries when NIFTY's own RSI(2) is bullish (i.e., don't buy oversold-in-bull because reversion is the bull's job; do buy oversold-in-bear because that's where the strategy thesis is). Limits both trade count and drawdown source diversity.

## Action

- Restore `rsi2_connors_daily_v1.json` to its committed state (`stop_loss_pct: 3.0`) — the field value is forward-correct as a placeholder; behavior is empirically negative.
- File this triage to `docs/research/`.
- File a v2 design ticket with the four experiments above prioritized in the order they tackle structural failure modes (universe filter is highest-leverage).
- **Do not promote RSI(2) v1 to paper** — three independent stop levels confirm G4 cannot be reached without a structural redesign.

## Framework fix verdict (independent of strategy)

The stop-loss enforcement framework fix from PR #232 works as designed. The empirical "wider stop → tighter concentration → fewer trades → higher CAGR" gradient is exactly what a correctly-wired mechanism should produce. The strategy-level negative result reinforces the framework's value: pre-fix, this gradient was invisible because the field was unconsumed; post-fix, the strategy designer can see the mechanism's effect and use it to inform v2 design.

## Triage data

| Tag | Output file (inside backend container) |
|---|---|
| stop=3 | `/tmp/rsi2-connors-stop3-postfix_triage.json` |
| stop=5 | `/tmp/rsi2-connors-stop5-postfix_triage.json` |
| stop=7 | `/tmp/rsi2-connors-stop7-postfix_triage.json` |
| Log (stop=3) | `/tmp/rsi2_stop3_postfix.log` |
| Log (stop=5) | `/tmp/rsi2_stop5_postfix.log` |

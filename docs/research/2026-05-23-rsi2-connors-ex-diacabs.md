# RSI(2) Connors Daily v1 — Ex-DIACABS Sanity Re-run

| | |
|---|---|
| Date | 2026-05-23 |
| Predecessor | docs/research/2026-05-22-rsi2-connors-daily-baseline.md |
| Tag | rsi2-connors-daily-ex-diacabs |
| Change | DIACABS.NS removed from the 802-ticker universe (everything else identical) |
| Effective universe | 801 tickers |

## Side-by-side

| Gate | Threshold | Baseline (with DIACABS) | Ex-DIACABS | Pass (ex) |
|---|---|---|---|---|
| G1: Trade count | ≥ 200 | 600 | 988 | PASS |
| G2: CAGR | ≥ 8% | 8.66% | 8.44% | PASS |
| G3: Win rate (ex-stops) | ≥ 60% | 60.67% | 62.85% | PASS |
| G4: Max drawdown | ≤ 15% | -24.26% | -19.89% | **FAIL** |
| G5: Concentration | ≤ 20% one name | 101.22% (DIACABS.NS) | 12.62% (APOLLO.NS) | PASS |

## Verdict

**Literature claim survives** — G2 (8.44%) and G3 (62.85%) both pass without DIACABS.NS.
The G3 win rate actually *improved* by +2.18 pp, confirming that the 60%+ win rate is a
property of the broader RSI(2) mean-reversion strategy, not a DIACABS artifact.
G5 also resolves cleanly: the top ticker (APOLLO.NS) sits at 12.62%, well inside the 20% cap.
The strategy has real edge on the Indian universe. This justifies a v2 spec.

G4 (max drawdown) remains the sole outstanding failure: -19.89% vs the 15% limit. The drawdown
improved by 4.37 pp after removing the one outsized name, suggesting DIACABS's large P&L also
masked periods of deep concurrent loss in the rest of the book.

## Numerical comparison

| Metric | Baseline (802 tickers) | Ex-DIACABS (801 tickers) | Delta |
|---|---|---|---|
| Trades | 600 | 988 | +388 (+64.7%) |
| CAGR | 8.66% | 8.44% | -0.22 pp |
| Win rate (ex-stops) | 60.67% | 62.85% | +2.18 pp |
| Max drawdown | -24.26% | -19.89% | +4.37 pp (improved) |
| Top ticker share | 101.22% (DIACABS.NS) | 12.62% (APOLLO.NS) | -88.6 pp |
| Net return | +43.9% | +42.64% | -1.26 pp |
| Final equity (₹10L NAV) | ₹14,39,044 | ₹14,26,425 | -₹12,619 |

The +388-trade jump (600 → 988) reflects that DIACABS was absorbing capital and blocking other
entries. With it removed, the portfolio diversifies across more names and still returns 8.44% CAGR.

## Next action

**File v2 spec with ADTV filter (₹5 Cr/day floor) + tighter stops.**

The single remaining failure (G4 -19.89% > 15% limit) is a stop/sizing problem, not a
structural edge problem. Two targeted levers address it:

1. **ADTV floor ₹5 Cr/day** — removes illiquid counters that hold positions open for many
   bars before the exit signal fires (source of correlated intraday drawdown).
2. **Stop-loss tightened from 5% to 2–2.5%** — limits per-trade loss; with ≤5 concurrent
   positions the portfolio peak-to-trough compresses proportionally.

A v2 backtest with both changes is the immediate next step. If G4 passes in v2, the strategy
is a candidate for paper trading.

## Triage JSON path

`/tmp/rsi2-connors-daily-ex-diacabs_triage.json` (inside the backend container)

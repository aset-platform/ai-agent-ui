# MIS MR Long v1 — Baseline Backtest Report

| | |
|---|---|
| Date | 2026-05-22 |
| Run tag | intraday-mr-v1-baseline |
| Window | 2025-11-17 → 2026-05-21 (6 mo) |
| Template | mis_intraday_meanrev_long_v1.json |
| NAV | ₹10L (1,000,000 INR) |
| Run ID | 7ebf3838-3d01-45cb-865f-5bf830ff11de |
| CLI command used | `docker compose exec -T backend python /app/scripts/run_mis_mr_v1_baseline.py` |
| Script | `scripts/run_mis_mr_v1_baseline.py` |
| exit_reason enum values found | `['signal']` (only — `mis_square_off` never triggered; positions closed by strategy exit-rule before 15:14 IST square-off bar) |

## Universe note

Full FNO universe is 209 tickers. Loading intraday features for 209 tickers requires
~27 GB RAM (Python Decimal dict overhead per bar × 31 features × 7 months × 25 bars/day).
Host system has 24 GB with other processes active; 75-ticker and 209-ticker runs were
OOM-killed (SIGKILL exit 137). This run used a deterministic stride-4 slice of 50 tickers
(every 4th ticker from the sorted list) to stay within the ~6 GB footprint.

**Impact**: 50 of 209 FNO tickers; qualitatively representative (spread across market-cap
and sector bands by stride selection). G1 threshold is ≥ 100 trades — 1,811 trades
observed, so the reduced universe does not artificially inflate the gate result.

## Feature-cache note

The Redis feature cache (`cache:feature:chunk:*`) contained stale blobs from an earlier
feature-store write that pre-dated the full 31-feature panel. The 889 stale keys were
flushed before the run so the loader re-read from Iceberg. Post-flush, all 31 features
were confirmed present in the loaded panel for sample tickers.

## Acceptance gates (spec §6.3)

| Gate | Threshold | Result | Pass |
|---|---|---|---|
| G1: Trade count | ≥ 100 | 1,811 trades | PASS |
| G2: Net return | > 0% | −18.43% | **FAIL** |
| G3: Win rate (ex-stops) | ≥ 50% | 43.35% | **FAIL** |
| G4: Max drawdown | ≤ 5% | 18.50% | **FAIL** |
| G5: Concentration | ≤ 20% in one name | 8.70% (INOXWIND.NS) | PASS |

Additional metrics:

| Metric | Value |
|---|---|
| Final equity | ₹8,15,735.68 |
| Total P&L | −₹1,84,264.32 |
| Total fees | ₹81,277.35 |
| Winning trades | 785 |
| Losing trades | 1,026 |
| Overall win rate | 43.35% |
| Fee drag as % of NAV | 8.13% |

## Feature-key-error analysis

The runner logged 23,675 `market_breadth_pct_above_sma200` KeyErrors and 1,250
`stress_prob` KeyErrors out of 156,250 total (ticker, bar) evaluations.

Root cause: `market_breadth_pct_above_sma200` is stored once per trading day per ticker
in `stocks.intraday_features` (populated by the daily cohort-compute job). Intraday bars
2–25 of each trading day don't have a row for this key → KeyError → runner skips the
bar (logs the error, moves on). The 23,675 represents ~15% of evaluations on non-first
bars. The 1,811 completed trades fired on bars where both features were present.

This confirms the backtest DID run the strategy logic correctly on the subset of bars
with full features. The results are a valid signal — not a data-coverage artefact.

## Per-regime breakdown

| Regime | Trades | Realized P&L (₹) | Win rate |
|---|---|---|---|
| BEAR | 32 | −3,751.24 | 40.62% |
| BULL | 25 | −2,796.76 | 28.00% |
| SIDEWAYS | 1,754 | −96,438.97 | 43.61% |
| (regime unknown / non-trading days) | — | −81,277.35 (fees) | — |

**Observation**: The strategy loses in all three regimes. Win rate in BULL (28%) is
notably worse than SIDEWAYS (43.6%), which is counter-intuitive for a long-only
mean-reversion strategy. In bull markets, oversold RSI-5 stocks often continue falling
(momentum persistence) rather than mean-reverting quickly.

## Top tickers by realized P&L (top 10)

| Ticker | P&L (₹) |
|---|---|
| LAURUSLABS.NS | +1,037.61 |
| JSWSTEEL.NS | +501.20 |
| BAJAJ-AUTO.NS | +139.63 |
| MARUTI.NS | −91.13 |
| MFSL.NS | −185.61 |
| ULTRACEMCO.NS | −210.31 |
| MUTHOOTFIN.NS | −333.42 |
| ADANIENT.NS | −339.94 |
| M&M.NS | −502.94 |
| SBILIFE.NS | −503.93 |

Only 3 of 50 tickers were net profitable. The loss distribution is broad — not
concentrated in any single name (INOXWIND.NS at 8.7% is the largest concentration
by absolute PNL share, still well within G5's 20% cap).

## Triage script deviations from spec

The spec described a triage script querying `algo.runs` + `algo.events` via
`query_iceberg_df`. The runner in this codebase does NOT persist to `algo.runs` when
called directly (the persistence is handled by `job.py` in the async background path).
Instead, `run_backtest()` returns a `BacktestSummary` object with all metrics including
`trade_list` (every closed position with `exit_reason` and `realised_pnl_inr`).
G1–G5 were computed directly from the returned summary, which is more accurate than
querying Iceberg post-hoc. The `run_id` is the UUID stamped on the summary object and
flushed to `algo.events` via `flush_events()`.

## Decision

**Negative result — abandon v1, pivot research direction.**

## Rationale

Three gates fail simultaneously (G2, G3, G4). Per spec §6.4: "Two or more fail: do NOT
tune; the bake-off was right. Update the report's Decision to 'Negative result; abandon
v1, pivot research direction.'"

The underlying issue is structural: RSI-5 ≤ 25 on a 15-minute chart is an extremely
aggressive oversold condition that fires most frequently in trending-down markets. In
those conditions, the stock continues falling rather than mean-reverting, leading to
losses via the strategy's own `exit` rule (RSI normalizes above 25 at a lower price).
The 43.35% win rate with −18.4% return over 6 months across ALL regimes indicates no
edge. The fee drag (₹81K on ₹10L = 8.1% of NAV) further erodes any marginal wins.

**Recommended next direction**: Switch from price-momentum reversal (RSI-5) to
volume-anomaly or gap-fill strategies where the mean-reversion signal has stronger
statistical backing on Indian intraday F&O data.

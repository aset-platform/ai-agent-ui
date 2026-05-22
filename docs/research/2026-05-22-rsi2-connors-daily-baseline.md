# RSI(2) Connors Daily v1 — Baseline Backtest Report

| | |
|---|---|
| Date | 2026-05-22 |
| Run tag | rsi2-connors-daily-baseline |
| Window | 2022-01-01 → 2026-05-21 (~4.4 yr) |
| Template | `rsi2_connors_daily_v1.json` |
| NAV | ₹10L (1,000,000 INR) |
| Universe | broad NSE stock registry, 802 stocks |
| Script | `scripts/run_rsi2_connors_baseline.py` |

## Run metrics

| Metric | Value |
|---|---|
| Final equity | ₹14,39,044 |
| Net return | +43.9% |
| CAGR | 8.66% |
| Total trades (closed) | 600 |
| Win rate (ex-stop-losses) | 60.67% |
| Max drawdown (daily P&L) | -24.26% |
| Top ticker (DIACABS.NS) concentration | 101.22% of total P&L |

## Acceptance gates (spec §6.3)

| Gate | Threshold | Result | Pass? |
|---|---|---|---|
| G1: Trade count | ≥ 200 | 600 trades | PASS |
| G2: CAGR | ≥ 8% | 8.66% | PASS |
| G3: Win rate (ex-stops) | ≥ 60% | 60.67% | PASS |
| G4: Max drawdown | ≤ 15% | -24.26% [^1] | **FAIL** |
| G5: Concentration | ≤ 20% in one name | DIACABS.NS at 101.22% | **FAIL** |

## Caveats and observations

### Feature backfill gap

The backtest runner's on-the-fly indicator computation (`backend/algo/backtest/indicators.py`) did not include `rsi_2`, `rsi_5`, or `distance_from_sma5` at the time Tasks 1–3 were shipped. These are emitted by the Feature Engine (`daily_engine.py`) into `stocks.daily_features`, but the daily backtest path uses in-memory OHLCV-derived computation, not the Iceberg feature store.

As part of this task, `indicators.py` was patched to add the missing features (`rsi_2`, `rsi_5`, `distance_from_sma5`) inline, consistent with the existing `rsi_14` / `sma_N` pattern. The fix:

- Added `sma_5` to the default SMA windows (was `(20, 50, 200)` → now `(5, 20, 50, 200)`)
- Added `rsi_5_series = _wilder_rsi(closes, 5)` and `rsi_2_series = _wilder_rsi(closes, 2)`
- Added `distance_from_sma5 = (close - sma_5) / sma_5` per bar

Residual feature-key-errors in the run log:
- `'Feature not in context: distance_from_sma200'`: 3,169 bars (factor library data absent for those ticker/dates — conservative, skips entry)
- `'Feature not in context: rsi_2'`: 399 bars (warmup period bars before RSI(2) settles — expected)
- `'Feature not in context: distance_from_sma5'`: 311 bars (warmup period)
- `'Feature not in context: stress_prob'`: 9 bars (regime history gaps)

None of these inflate returns — they cause missed entry signals, not spurious fills.

### G5: DIACABS.NS concentration

DIACABS.NS (Dia Cables & Accessories) contributed 101.22% of total realised P&L, meaning all other positions combined were net-negative. This is a single-name concentration risk — likely a multi-bagger position or a short-lived illiquid counter that happened to fire the RSI(2) signal during a corporate-action-driven spike. The 101.22% figure exceeds the 20% gate by 5x, making it a structural issue rather than borderline.

### G4: Max drawdown -24.26%

The drawdown of -24.26% exceeds the 15% gate significantly. The strategy holds positions for multiple bars when the exit signal (`distance_from_sma5 > 0`) has not fired, and the 5% stop-loss per trade with up to 5 concurrent positions means the portfolio drawdown can compound. The 2022 Nifty correction period likely accounts for a large portion of the drawdown.

[^1]: G4 uses realized P&L only. Open-position MTM is excluded; true peak-to-trough including open marks is likely larger.

## Comparison to v4 baseline

| | v4 (Bull Momentum, existing) | Connors RSI(2) v1 (this run) |
|---|---|---|
| Win rate | 53.6% | 60.67% |
| CAGR | ~12% annualised (ex-election week) | 8.66% |
| Max drawdown | not published | -24.26% |
| Approach | Momentum (trending) | Mean reversion (oversold bounce) |
| G1 trades | n/a | PASS (600) |
| G2 CAGR | PASS | PASS |
| G3 win rate | PASS | PASS |
| G4 drawdown | PASS | **FAIL** |
| G5 concentration | PASS | **FAIL** |

The Connors strategy improves win rate (+7 pp) but degrades risk-adjusted performance — higher drawdown and extreme single-name concentration.

## Decision

**Negative result — abandon v1, pivot research direction.**

Two gates failed (G4 and G5). Per spec §6.4, the one-iteration tune rule does not apply when two or more gates fail. No template changes were made.

## Rationale

A single ticker (DIACABS.NS) at 101.22% of total realized P&L means that name's wins exceeded every other ticker's contribution combined. The G3 win-rate pass at 60.67% likely reflects DIACABS-driven trades; the apparent edge is **unproven** on the broader universe, not merely "risky".

The -24.26% max drawdown is a separate failure on risk management criteria. The stop-loss / position sizing combination is insufficient to limit portfolio-level drawdown during trending bear phases — the strategy holds positions for multiple bars until `distance_from_sma5 > 0` fires, and 5% stops across up to 5 concurrent positions allow portfolio drawdown to compound. The 2022 Nifty correction period likely accounts for a significant portion.

The G5 failure (DIACABS.NS at 101.22% concentration) further suggests the universe contains low-liquidity counters that distort aggregate P&L statistics — a filter on minimum ADTV or minimum market-cap would be a prerequisite for any v2 attempt. The combination of these two failures, with the unproven-edge concern primary, means the strategy is not investable without further structural changes (e.g., ADTV filter, tighter stops, volatility-adjusted position sizing).

## Possible v2 directions (if research continues)

1. **ADTV floor**: exclude counters with 3-month average daily traded value below ₹5 Cr — eliminates illiquid outliers like DIACABS.NS.
2. **Tighter stop**: reduce `stop_loss_pct` from 5% to 2.5%, then re-evaluate G4.
3. **Volatility-adjusted sizing**: use `atr_14` to size positions rather than fixed 20% weight — reduces drawdown in high-vol regimes.
4. **RSI(2) ≤ 3 entry** (Connors' deepest oversold): fewer trades, but higher expected mean-reversion magnitude per trade.

These are deferred to a hypothetical v2 ticket; v1 is closed as a negative result.

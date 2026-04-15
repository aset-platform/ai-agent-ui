# Forecast Data Enrichment & Sanity Gates — Design Spec

**Date**: 2026-04-15
**Sprint**: 7 (Apr 16–22)
**Jira**: ASETPLTFRM-302 (sanity gates), ASETPLTFRM-203 (partial — data enrichment only)
**Status**: Draft
**Estimate**: 13–18 SP

---

## Problem Statement

1. **13% of tickers produce broken forecasts** — 97 tickers predict >50%
   price drop (10 predict -100%), 44 predict >100% gain. Root cause:
   Prophet's linear trend extrapolation fails on volatile stocks with
   parabolic price histories. No sanity gates exist to catch or flag
   these.

2. **Rich data sits unused** — `analysis_summary` (25 technical fields),
   `piotroski_scores` (747 stocks), and `quarterly_results` are in
   Iceberg but never fed to Prophet. Only 4 regressor categories are
   active (market, macro, sentiment, analyst).

3. **No confidence signal** — users see Bullish/Bearish/Neutral but have
   no way to know whether a forecast is reliable or garbage.

---

## Scope

### In Scope

- Volatility-regime adaptive Prophet configuration (3 buckets)
- Log-transform + logistic growth for volatile tickers
- Wire Tier 1 data as Prophet regressors (analysis_summary, Piotroski,
  quarterly fundamentals)
- Compute Tier 2 features (sector index relative strength, volume-price
  features, calendar effects)
- Post-Prophet technical bias adjustment (RSI/MACD dampener)
- Composite confidence score + Level 2 UI badge with explanations
- Sanity gates to reject/flag broken forecasts

### Out of Scope (deferred)

- NeuralProphet migration → Sprint 8
- XGBoost ensemble activation → Sprint 8
- FinBERT batch sentiment → Sprint 8
- Tier 3 external data (FII/DII, PCR, FRED, RBI) → separate story
- New Iceberg tables (features computed on-the-fly from existing tables)
- Frontend forecast chart redesign (only adding confidence badge)

---

## Section 1: Volatility-Regime Adaptive Prophet

### Classification

Tickers classified into 3 regimes using `analysis_summary.annualized_volatility`:

| Regime   | Annualized Vol | Growth   | Transform | cps  | changepoint_range |
|----------|---------------|----------|-----------|------|-------------------|
| Stable   | < 30%         | linear   | none      | 0.01 | 0.80              |
| Moderate | 30–60%        | linear   | log(y)    | 0.10 | 0.85              |
| Volatile | > 60%         | logistic | log(y)    | 0.25 | 0.90              |

- `cps` = `changepoint_prior_scale`
- Classification based on latest `analysis_summary` row for the ticker
- Fallback: if no analysis_summary, default to Moderate regime

### Logistic Growth Bounds (Volatile Regime)

- `cap = ATH_2yr * 1.5` — all-time high over last 2 years × 1.5
- `floor = recent_low_1yr * 0.3` — 1-year low × 0.3
- Recalculated per forecast run from OHLCV data
- Both cap and floor applied to both training data and future dataframe

### Log Transform

- Applied for Moderate and Volatile regimes
- `y_train = np.log(y)` before fit
- `forecast = np.exp(predicted)` after predict
- Confidence intervals also exponentiated
- Guarantees non-negative predictions without the crude `clip(lower=0.01)`

### New Module

`backend/tools/_forecast_regime.py`:
- `classify_regime(ticker, analysis_summary_row) → RegimeConfig`
- `build_prophet_config(regime_config) → dict` (passed to Prophet constructor)
- `compute_logistic_bounds(ohlcv_df) → (cap, floor)`

---

## Section 2: Tier 1 Regressors — Existing Data

All sourced from existing Iceberg tables. No new ingestion.

### From `analysis_summary` (daily, slow-moving)

| Feature | Computation | Prophet Regressor Name |
|---------|-------------|----------------------|
| Volatility regime score | `annualized_volatility / 100` (normalized 0-1) | `volatility_regime` |
| Trend strength | `(bull_phase_pct - bear_phase_pct) / 100` (-1 to +1) | `trend_strength` |
| Support/resistance position | `(price - support) / (resistance - support)` (0-1) | `sr_position` |

### From `piotroski_scores` (quarterly)

| Feature | Computation | Prophet Regressor Name |
|---------|-------------|----------------------|
| Fundamental strength | `f_score / 9` (normalized 0-1) | `piotroski` |

### From `quarterly_results` (quarterly)

| Feature | Computation | Prophet Regressor Name |
|---------|-------------|----------------------|
| Revenue growth | `(rev_q - rev_q_prev) / abs(rev_q_prev)` (capped ±1) | `revenue_growth` |
| EPS growth | `(eps_q - eps_q_prev) / abs(eps_q_prev)` (capped ±1) | `eps_growth` |

### Future Value Projection

All Tier 1 signals are slow-moving (quarterly or daily with low
variance). For the 9-month forecast horizon, held constant at last
known value — standard practice for fundamental regressors in Prophet.

### Loading Strategy

Single batch DuckDB read per scope (india/us) at forecast start — same
pattern as existing VIX/macro bulk load in `_forecast_shared.py`.
Scope-keyed cache with 10-minute TTL. Zero per-ticker reads.

---

## Section 3: Tier 2 Computed Features

Derived from data we already have or can trivially pull via yfinance.

### Sector Index Relative Strength (new ingestion — minimal)

**Sector indices to ingest** (stored in existing `ohlcv` table):
- India: `^NSEBANK`, `^CNXIT`, `^CNXPHARMA`, `^CNXFMCG`, `^CNXAUTO`
- US: `XLK`, `XLF`, `XLE`, `XLV`, `XLY`

**Sector mapping**: `company_info.sector` → sector index ticker.
Fallback to broad market index if sector not mapped.

**Computation**:
```
sector_relative_strength = ticker_20d_return - sector_index_20d_return
```

Positive = outperforming sector. 20d rolling mean is slow-moving
enough to project forward (natural decay toward 0).

### Volume-Price Features (from existing OHLCV)

| Feature | Computation | Regressor Name |
|---------|-------------|----------------|
| Volume anomaly | `volume / volume_sma_20 - 1` | `volume_anomaly` |
| OBV trend | slope of OBV over 20 days | `obv_trend` |

For forecast horizon: held at 0 (neutral) — volume anomalies are
transient signals.

### Calendar Features (deterministic — known perfectly)

| Feature | Computation | Regressor Name |
|---------|-------------|----------------|
| Day of week | Mon=0, Fri=4 (ordinal, normalized 0-1) | `day_of_week` |
| Month of year | Jan=0, Dec=11 (ordinal, normalized 0-1) | `month_of_year` |
| F&O expiry proximity | Days until last Thursday of month (India only, 0-1) | `expiry_proximity` |
| Earnings proximity | Days since/until nearest earnings date (normalized 0-1) | `earnings_proximity` |

Calendar features are the only ones that generate true future values.
All others held constant or set to neutral.

### New Module

`backend/tools/_forecast_features.py`:
- `compute_tier1_features(ticker, analysis_row, piotroski_row, quarterly_rows) → DataFrame`
- `compute_tier2_features(ticker, ohlcv_df, sector_index_df, company_info_row, earnings_dates) → DataFrame`
- `build_future_features(last_known_features, future_dates) → DataFrame`
- `get_sector_index_mapping(market) → dict[str, str]`

---

## Section 4: Post-Prophet Technical Bias Adjustment

Fast-moving technicals (RSI, MACD, OBV divergence) cannot be Prophet
regressors but carry signal at forecast time. Applied as a post-Prophet
adjustment layer.

### Adjustment Rules

| Signal | Condition | Adjustment | Rationale |
|--------|-----------|------------|-----------|
| RSI > 75 | Overbought | Dampen bullish by 15% | Mean-reversion expected |
| RSI < 25 | Oversold | Dampen bearish by 15% | Bounce expected |
| MACD bearish crossover | Signal line cross | Dampen bullish by 8% | Momentum shifting down |
| MACD bullish crossover | Signal line cross | Dampen bearish by 8% | Momentum shifting up |
| Volume spike + price drop | Vol > 2× SMA20 + neg day | Amplify bearish by 5% | Distribution signal |
| Volume spike + price rise | Vol > 2× SMA20 + pos day | Amplify bullish by 5% | Accumulation signal |

### Application

```python
bias = 1.0 + sum(applicable_adjustments)
adjusted_forecast = prophet_forecast * bias
```

**Constraints**:
- Total adjustment capped at ±15%
- Applied only to first 30 days of forecast (technical signals decay)
- Linear taper: full effect at day 1, zero at day 30
- Adjustment logged in forecast metadata for transparency

### Implementation

Function `apply_technical_bias()` in `backend/tools/_forecast_regime.py`:
- Input: Prophet forecast DataFrame, current technical state from
  `analysis_summary`
- Output: adjusted forecast DataFrame + bias metadata dict
- Called after Prophet predict, before results are written

---

## Section 5: Composite Confidence Score & UI

### Score Formula

```python
confidence = (
    0.25 * direction_score      # directional accuracy from CV (0-1)
  + 0.25 * mase_score           # 1 - min(MASE, 2) / 2
  + 0.20 * coverage_score       # 1 - abs(actual_coverage - 0.80)
  + 0.15 * interval_score       # 1 - min(interval_width / price, 1)
  + 0.15 * data_completeness    # fraction of available regressors
)
```

### Badge Mapping

| Score     | Badge  | Color  | Meaning |
|-----------|--------|--------|---------|
| ≥ 0.65   | High   | Green  | Reliable — model fits well, good coverage |
| 0.40–0.64 | Medium | Yellow | Directional guidance only |
| < 0.40   | Low    | Red    | Unreliable — treat with caution |

### Rejection Gate

Forecasts scoring below **0.25** are stored but **not shown** on the
forecast chart. UI displays: "Forecast unavailable — insufficient
model confidence". Admin Data Health page can see rejected forecasts
for debugging.

### UI — Level 2 Badge with Explanations

- Badge top-right of forecast chart (same position as existing
  Bullish/Bearish/Neutral pill)
- Click/hover expands a card showing:
  - "Directional accuracy: 62%"
  - "Forecast error (MASE): 0.84 (beats naive)"
  - "Prediction interval coverage: 78%"
  - "Data signals: 7 of 9 available"
  - Brief reason if Low: e.g., "High volatility stock with limited
    analyst coverage"

### Storage

Two new columns in `forecast_runs` Iceberg table (schema evolution):
- `confidence_score FLOAT` — composite score 0-1
- `confidence_components STRING` — JSON with 5 sub-scores + reason text

No new table needed.

---

## Section 6: Integration — Pipeline Flow

### Updated Batch Forecast Pipeline

```
┌──────────────────────────────────────────────────────┐
│                 Batch Forecast Job                     │
│                 (executor.py)                          │
├──────────────────────────────────────────────────────┤
│                                                        │
│  1. BULK PRE-LOAD (existing + new)                    │
│     ├─ OHLCV batch read (existing)                    │
│     ├─ VIX, index, macro regressors (existing)        │
│     ├─ analysis_summary batch read (NEW)              │
│     ├─ piotroski_scores batch read (NEW)              │
│     ├─ quarterly_results batch read (NEW)             │
│     └─ sector index OHLCV batch read (NEW)            │
│                                                        │
│  2. PER-TICKER LOOP (parallel, ThreadPool)            │
│     ├─ Classify volatility regime (NEW)               │
│     │   → stable / moderate / volatile                │
│     ├─ Configure Prophet per regime (NEW)             │
│     │   → growth mode, cps, log-transform             │
│     ├─ Build regressors DataFrame                     │
│     │   ├─ Market: VIX, index return (existing)       │
│     │   ├─ Macro: treasury, oil, dollar (existing)    │
│     │   ├─ Sentiment: avg_score (existing)            │
│     │   ├─ Analyst: targets, EPS (existing)           │
│     │   ├─ Tier 1: trend, SR position, f-score,      │
│     │   │   revenue growth, EPS growth (NEW)          │
│     │   └─ Tier 2: sector RS, volume anomaly,         │
│     │       calendar, earnings proximity (NEW)         │
│     ├─ Train Prophet + predict                        │
│     ├─ Apply technical bias adjustment (NEW)          │
│     │   → RSI/MACD/volume dampener, 30d taper         │
│     ├─ Compute accuracy (CV, reuse if <30d)           │
│     ├─ Compute confidence score (NEW)                 │
│     └─ Accumulate results                             │
│                                                        │
│  3. BULK WRITE                                        │
│     ├─ forecast_runs (+ confidence cols) (EVOLVED)    │
│     └─ forecasts series (existing)                    │
└──────────────────────────────────────────────────────┘
```

### New Files

| File | Purpose |
|------|---------|
| `backend/tools/_forecast_regime.py` | Volatility classification, Prophet config builder, technical bias adjustment |
| `backend/tools/_forecast_features.py` | Tier 1 + Tier 2 feature computation, sector mapping, calendar features |

### Modified Files

| File | Change |
|------|--------|
| `backend/tools/_forecast_model.py` | Accept regime config dict instead of hardcoded params |
| `backend/tools/_forecast_shared.py` | Add Tier 1/2 regressors to bulk load + merge pipeline |
| `backend/tools/_forecast_accuracy.py` | Add confidence score computation + MASE metric |
| `backend/jobs/executor.py` | Wire regime classification + new regressors into batch loop |
| `stocks/create_tables.py` | Evolve `forecast_runs` schema (2 new columns) |
| `stocks/repository.py` | Add batch readers for analysis_summary, piotroski by ticker list |
| `frontend/components/charts/ForecastChart.tsx` | Confidence badge + expandable explanation card |
| `backend/tools/forecasting_tool.py` | Wire regime + features for single-ticker chat forecast |

### Performance Impact

- Pre-load adds 3 more DuckDB batch reads (~1-2s total, cached)
- Per-ticker: regime classification is O(1) lookup, feature computation
  is lightweight DataFrame ops
- Technical bias adjustment is a single vectorized multiply
- Confidence score is 5 arithmetic operations
- **Net estimate**: <5% increase in total forecast pipeline runtime

---

## Data Flow Summary

```
Existing Iceberg Tables              New Computations
─────────────────────              ─────────────────
ohlcv ──────────────────┐
analysis_summary ───────┤         ┌─ Regime Classification
piotroski_scores ───────┤         │  (stable/moderate/volatile)
quarterly_results ──────┤         │
sentiment_scores ───────┤    ──►  ├─ Tier 1 Regressors
company_info ───────────┤         │  (trend, SR, f-score, growth)
                        │         │
yfinance (sector ETFs)──┤         ├─ Tier 2 Features
yfinance (earnings) ────┘         │  (sector RS, vol anomaly, calendar)
                                  │
                                  ├─ Adaptive Prophet
                                  │  (regime-specific config)
                                  │
                                  ├─ Technical Bias Adjustment
                                  │  (RSI/MACD dampener, 30d taper)
                                  │
                                  └─ Confidence Score
                                     (MASE, direction, coverage, data)
```

---

## Acceptance Criteria

1. Zero tickers predict negative prices (log-transform eliminates this)
2. Tickers predicting >±50% change are either:
   - Correctly volatile (high vol regime, logistic bounds constrain), or
   - Flagged Low Confidence with explanation
3. Confidence badge visible on all forecast charts
4. Forecasts scoring <0.25 hidden from users with explanation message
5. All Tier 1 regressors (6 signals) wired into Prophet
6. All Tier 2 features (6 signals) computed and wired
7. Technical bias adjustment applied with 30-day taper
8. Batch forecast pipeline runtime increase <10%
9. Sector indices ingested for both India and US markets
10. Single-ticker chat forecast uses same regime + features

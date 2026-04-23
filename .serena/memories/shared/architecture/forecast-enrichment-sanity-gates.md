# Forecast Enrichment & Sanity Gates (Sprint 7)

## Volatility-Regime Adaptive Prophet

Tickers classified by `annualized_volatility_pct` from `analysis_summary`:

| Regime | Vol | Growth | Transform | cps | changepoint_range |
|--------|-----|--------|-----------|-----|-------------------|
| stable | <30% | linear | none | 0.01 | 0.80 |
| moderate | 30-60% | linear | log(y) | 0.10 | 0.85 |
| volatile | ≥60% | logistic | log(y) | 0.25 | 0.90 |

Module: `backend/tools/_forecast_regime.py`
- `classify_regime(annualized_vol)` → "stable"/"moderate"/"volatile"
- `build_prophet_config(regime)` → dict for Prophet constructor
- `compute_logistic_bounds(ohlcv_df)` → (cap, floor)
- `apply_technical_bias(forecast_df, analysis_row)` → (df, meta)

## Prophet Regressors (11 total)

7 original: vix, index_return, sentiment, treasury_10y, oil_price, dollar_index, yield_spread
4 enrichment: revenue_growth, volume_anomaly, trend_strength, sr_position

9 dropped (|beta| < 0.0015): day_of_week, month_of_year, expiry_proximity,
earnings_proximity, volatility_regime, eps_growth, piotroski,
sector_relative_strength, obv_trend. Still computed for confidence score.

Module: `backend/tools/_forecast_features.py`
Enrichment: `backend/tools/_forecast_shared.py` → `_enrich_regressors()`

## Sanity Gates

1. Log-transform exp cap: `np.exp(last_log_y ± 1.5)` → max 4.5x price
2. Extreme series skip: >200% deviation → series not written to Iceberg
3. Frontend warning: replaces target cards for extreme predictions
4. NaN MAPE → Low confidence (0.37) not Medium (0.60)
5. Data Health: ROW_NUMBER(computed_at DESC) for latest run per ticker

## Confidence Score

Formula: 0.25×direction + 0.25×mase + 0.20×coverage + 0.15×interval + 0.15×data_completeness
Badges: High (≥0.65), Medium (0.40-0.64), Low (<0.40), Rejected (<0.25)
Module: `backend/tools/_forecast_accuracy.py` → `compute_confidence_score()`
UI: Analysis page header + ForecastChartWidget (Portfolio dashboard)

## FinBERT Sentiment

Module: `backend/tools/_sentiment_finbert.py`
Config: `sentiment_scorer: str = "finbert"` in Settings
Wired in: `score_headlines()` routes to FinBERT when config says finbert
Model: ProsusAI/finbert, CPU-only, lazy singleton, batch_size=16
Dependencies: transformers>=4.40, torch (CPU-only via --index-url)

## XGBoost Ensemble

Casing fix: `tech.columns = [c.lower() for c in tech.columns]`
13 features: prophet_yhat + 7 market/macro + 5 technical (RSI, MACD, BB upper/lower, ATR)

## Performance

- Batch DuckDB reads for quarterly + piotroski (was N+1 anti-pattern)
- Single bulk merge instead of 20 per-column merges
- Low-data skip: <730 rows → 30-day cadence even on forced runs
- India forced run: ~46 min (was ~90 min with 20 regressors)

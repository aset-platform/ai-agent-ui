# Forecast Column Name Mismatches (Sprint 7 Bugs)

## Analysis Summary Column Names
- `annualized_volatility_pct` NOT `annualized_volatility`
- `support_levels` NOT `support_level` (stores JSON array like `[2346.2, 2348.0, 2350.2]`)
- `resistance_levels` NOT `resistance_level` (same JSON array format)

## XGBoost Technical Indicator Casing
- `compute_indicators()` returns Title-case: `RSI_14`, `MACD`, `BB_Upper`, `BB_Lower`, `ATR_14`
- `_FEATURES` list expects lowercase: `rsi_14`, `macd`, `bb_upper`, `bb_lower`, `atr_14`
- Fix: `tech.columns = [c.lower() for c in tech.columns]` after load
- Location: `backend/tools/_forecast_ensemble.py`

## OHLCV DataFrame Format Mismatch
- Executor's `_ohlcv_from_cached()` returns yfinance format: DatetimeIndex + Title-case columns
- `compute_tier2_features()` expected `date` column + lowercase columns
- Fix: normalize columns and ensure `date` column exists in `_forecast_features.py`

## Log-Transform Reference Price
- `_generate_forecast()` receives `prophet_df` with RAW prices (not log-transformed)
- Log-transform happens inside `_train_prophet_model()` on a COPY
- Must apply `np.log(prophet_df["y"])` before computing exp cap bounds
- Location: `backend/tools/_forecast_model.py`

## Forecast Run Dedup
- Multiple runs on same `run_date` → `run_date` dedup picks random one
- Fix: use `computed_at` (exact UTC timestamp) for dedup
- Location: `stocks/repository.py` → `get_dashboard_forecast_runs()`, `get_latest_forecast_run()`

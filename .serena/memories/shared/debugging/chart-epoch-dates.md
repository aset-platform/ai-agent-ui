# TradingView Chart Crash — Epoch Dates (1970-01-01)

## Problem
TradingView lightweight-charts crashes with: `Assertion failed: data must be asc ordered by time, index=1, time=0, prev time=0`

## Root Cause
Corrupted rows in Iceberg `technical_indicators` and `ohlcv` tables with `date=1970-01-01` (epoch zero). TradingView converts these to `time=0` and crashes on duplicate zero timestamps.

## Fix (3 layers)

### 1. Data Cleanup
Delete corrupted rows from Iceberg and re-append clean data per ticker.

### 2. Frontend Date Validation
Reject pre-1980 dates at 3 levels in chart data processing:
```typescript
const validDate = /^(19[89]\d|2\d{3})-/;  // 1980+ only
```
Applied in: `aggregateOHLCV()`, `aggregateIndicators()`, `filterNull()` output, per-series data.

### 3. toTime() Hardening
`toTime()` slices input to `YYYY-MM-DD` format before passing to TradingView. Prevents time-of-day components from causing sort issues.

## Prevention
Always validate date ranges when ingesting OHLCV data. The `stock_refresh.py` pipeline should reject rows with dates before 1980.
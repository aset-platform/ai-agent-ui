# Iceberg Epoch Date Corruption (1970-01-01)

## Symptom
TradingView chart crashes: `Assertion failed: data must be asc ordered by time, index=1, time=0, prev time=0`

## Root Cause
Corrupted rows with `date=1970-01-01` in `technical_indicators` and `ohlcv` Iceberg tables. These originated from a yfinance ingestion where missing dates defaulted to epoch zero. TradingView converted these to `time=0` and crashed on duplicate timestamps.

## Fix

### Data Cleanup
Delete corrupted rows per ticker, then re-append clean data:
```python
repo._delete_rows("technical_indicators", ticker, date_col="date")
# Re-ingest from yfinance
```

### Frontend Hardening (3 levels)
1. **Date regex**: `/^(19[89]\d|2\d{3})-/` rejects pre-1980 dates
2. **filterNull output**: strip invalid dates after aggregation
3. **Per-series data**: `toTime()` slices to YYYY-MM-DD only

### Prevention
Always validate date ranges before Iceberg writes:
```python
if df["date"].min() < pd.Timestamp("1980-01-01"):
    df = df[df["date"] >= "1980-01-01"]
```

## Key File
- `frontend/components/charts/StockChart.tsx` — date validation at 3 levels

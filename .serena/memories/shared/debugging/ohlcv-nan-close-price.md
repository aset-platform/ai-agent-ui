# OHLCV NaN Close Price Bug

## Problem
yfinance can return a partial row for the current trading day with close=NaN.
float(ohlcv.iloc[-1]["close"]) returns NaN which propagates as 0.00 through calculations.
Portfolio hero section showed Rs0.00 total value.

## Fix
Always dropna(subset=["close"]) before taking iloc[-1]:
```python
valid = ohlcv.dropna(subset=["close"])
if valid.empty:
    return None
cur_price = float(valid.iloc[-1]["close"])
```

## Affected Files (all fixed)
- backend/tools/portfolio_tools.py — _current_price()
- backend/dashboard_routes.py — home endpoint (watchlist sparkline)
- backend/dashboard_routes.py — portfolio forecast endpoint
- backend/tools/forecast_tools.py — portfolio forecast tool
- auth/endpoints/ticker_routes.py — GET /users/me/portfolio

## Related
- ASETPLTFRM-162 (bug ticket, fixed Mar 24 2026)
- Also see shared/debugging/ohlcv-freshness-gate for related OHLCV issues
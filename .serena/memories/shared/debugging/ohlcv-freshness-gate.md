# OHLCV Freshness Gate — Skip vs Fetch

## Location
`dashboard/services/stock_refresh.py`, Step 1 of `run_full_refresh()`

## The Rule
Skip OHLCV fetch **only if data exists for today**: `latest_date >= date.today()`

## The Gotcha
Previously the gate was `latest_date >= date.today() - timedelta(days=1)`, which means:
- If latest OHLCV date is **yesterday** (e.g., market closed yesterday, today's data now available), the fetch is **skipped**
- User clicks refresh → pipeline runs → all 6 steps "succeed" → but OHLCV data is stale
- The refresh button shows a green checkmark but nothing actually updated

## Why It Matters
- Indian markets close at 3:30 PM IST. After close, yfinance has today's data available. With the `- 1 day` gate, the fetch skips because yesterday's data satisfies the condition.
- US markets close at 4 PM ET. Same problem applies.
- Weekends: Saturday with Friday data → `Friday >= Saturday - 1` = True → skips. But this is harmless since no new data exists anyway.

## Correct Gate
```python
_ohlcv_fresh = (
    _latest_date is not None
    and _latest_date >= date.today()
)
```

Running refresh twice on the same day correctly skips (idempotent). Running the day after correctly fetches new data.

## Other Gates (Correct, No Change Needed)
- **Technical analysis**: `analysis_date == date.today()` — skip only if done today
- **Prophet forecast**: `run_date >= date.today() - timedelta(days=7)` — skip if run within 7 days (training is expensive)

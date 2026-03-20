# Cash-Flow-Adjusted Portfolio Metrics

## The Problem

When a portfolio has ongoing capital contributions (user buys more stocks over time), raw market value always trends upward. Metrics computed on raw value are wildly inflated because they conflate **returns** with **new capital**.

Example: Portfolio goes from ₹60k → ₹310k over 10 months. Looks like +416% return. But the user invested ₹320k total — the portfolio is actually **losing 3%**.

## Correct Formulas

All metrics must strip cash flows using the `invested_value` series (cumulative cost basis per date).

### Daily Return (cash-flow neutral)
```python
cashflow = invested[d] - invested[d-1]  # new capital today
pnl = value[d] - value[d-1] - cashflow
daily_return = pnl / value[d-1] * 100
```
Without this, a day where the user adds ₹100k appears as a +50% "return."

### Total Return (invested basis)
```python
total_return = (last_value - last_invested) / last_invested * 100
```
NOT `(last_value - first_value) / first_value` — that includes all contributions.

### Annualized Return
```python
gain_ratio = last_value / last_invested
annualized = (gain_ratio ** (252 / n_trading_days) - 1) * 100
```

### Max Drawdown (gain% series)
Track the gain percentage `(value - invested) / invested` over time, then measure peak-to-trough on that series:
```python
for d in dates:
    gain_pct = (value[d] - invested[d]) / invested[d] * 100
    peak = max(peak, gain_pct)
    drawdown = gain_pct - peak
    max_dd = min(max_dd, drawdown)
```
Using raw value peak-to-trough would show fake drawdowns when the user hasn't invested in a while and the market dips.

### Sharpe Ratio & Best/Worst Day
Use the cash-flow-adjusted daily returns — no additional correction needed since the daily return series is already clean.

## Implementation

In `backend/dashboard_routes.py`, `_build_portfolio_performance()`:
- The `values` tuple is `(date, market_value, invested_value)` per day
- `invested_value` = sum of `qty × buy_price` for all lots active on that date
- `buy_price` uses `_safe_float()` with OHLCV fallback for NULL Iceberg prices

## Key Rule

**Never compute portfolio metrics on raw market value when there are capital flows.** Always use the invested_value series to isolate organic returns from contributions.

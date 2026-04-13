# Iceberg Freshness Checks — Chat Agent Tools

## Convention
All chat agent tools that fetch from external APIs (yfinance) MUST check Iceberg freshness first. Only call yfinance if data is stale.

## Current Freshness Windows (Apr 13, 2026)

| Tool | Table | Freshness | Before |
|------|-------|-----------|--------|
| `get_stock_info` | company_info | 7 days (`max_age_days=7`) | same-day only |
| `analyse_stock_price` | analysis_summary | 7 days (age check) | same-day only |
| `forecast_stock` | forecast_runs | 7 days (cooldown) | already 7 days |
| `fetch_stock_data` | ohlcv registry | smart delta (date_range_end) | already smart |
| `fetch_quarterly_results` | quarterly_results | 7 days | already 7 days |
| `get_dividend_history` | dividends | 90 days (ex_date recency) | NO check (always yfinance) |
| `get_ticker_news` | — | always fresh | always fresh (expected) |
| `get_analyst_recommendations` | Redis | 24h cache | already cached |

## Key Method
`repo.get_latest_company_info_if_fresh(ticker, date.today(), max_age_days=7)` — returns cached dict or None.
The `max_age_days` param was added Apr 13 (default 7). Covers weekends + holidays.

## Why 7 Days
- Daily scheduler runs Mon-Fri, skips weekends/holidays
- 7-day window ensures Friday's data serves through Monday
- Prevents unnecessary yfinance calls during market closures
- Dividends use 90 days because they're quarterly/annual events

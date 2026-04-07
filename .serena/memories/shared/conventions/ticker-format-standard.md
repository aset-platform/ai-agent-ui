# Ticker Format Standard

## Rule: All Indian stocks use .NS suffix everywhere

The system standardized on `.NS` format (yfinance convention) for Indian stocks.
This applies to: Iceberg tables, PG stock_registry, frontend, scheduler, chat tools.

## stock_master table
- `symbol` — canonical, no suffix (RELIANCE) — identity/FK anchor only
- `yf_ticker` — with suffix (RELIANCE.NS) — used for ALL data operations
- `nse_symbol` — plain (RELIANCE) — for jugaad-data NSE source only

## Market Detection
Use `backend/market_utils.py` — `detect_market(ticker, registry_market)`.
NEVER add local suffix-only checks. Import from market_utils.

Priority:
1. `.NS` / `.BO` suffix → "india"
2. registry_market in (NSE, BSE, INDIA) → "india"
3. Default → "us"

## Currency
Market-derived currency takes precedence for Indian stocks:
- market == "india" → always "INR" (yfinance sometimes returns USD for .NS tickers)
- market == "us" → use company_info currency or default "USD"

# Portfolio ↔ Watchlist Sync

## Problem
Adding a stock via the portfolio "+" button writes to `stocks.portfolio_transactions` but does NOT auto-link to `auth.user_tickers`. This causes:
1. Dashboard watchlist widget shows "0 tickers" despite having portfolio stocks
2. Unlink button returns 404 (ticker not in `user_tickers`)
3. Analytics page shows stocks as "Portfolio" tier but they can't be unlinked

## Architecture
Two separate systems manage user stocks:
- **Watchlist** (`auth.user_tickers`) — link/unlink via `DELETE /users/me/tickers/{ticker}`
- **Portfolio** (`stocks.portfolio_transactions`) — add/edit/delete via `/users/me/portfolio`

The dashboard watchlist widget reads from `user_tickers`. The analytics page reads from both.

## Fix
- `auth/endpoints/ticker_routes.py` → `add_portfolio_holding()` auto-links to watchlist via `repo.link_ticker()`
- `unlink_ticker()` no longer throws 404 for portfolio-only tickers — returns success

## Backfill
For tickers added before the fix: `scripts/backfill_portfolio_links.py`

## Key Files
- `auth/endpoints/ticker_routes.py` — link/unlink + portfolio CRUD
- `frontend/components/widgets/WatchlistWidget.tsx` — reads `data.value?.tickers`
- `frontend/app/(authenticated)/dashboard/page.tsx` — filters by `marketFilter` (india/us)

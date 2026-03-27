# Unified Analytics Page — 3-Tier Stock Cards

## Overview
The Analytics Home (`/analytics`) and Marketplace/Link Stock pages were merged into a single
card-based page showing ALL tickers from the registry with 3 visual tiers.

## Card Tiers (sorted in this order)
1. **Portfolio** (emerald/teal gradient accent) — stocks user owns
   - Shows: sparkline, sentiment glow badge, price + change, annualized return
   - Portfolio data row: quantity, avg price, gain/loss %
   - Buttons: Refresh, Unlink, +Portfolio, Analyse (icon buttons)
2. **Watchlist** (indigo/violet gradient accent) — linked but not owned
   - Same as portfolio minus holdings row
   - Buttons: Refresh, Unlink, +Portfolio, Analyse
3. **Unlinked** (muted, no accent) — available but not tracked
   - Shows: basic price, market badge
   - "Link to Watchlist" full-width CTA button

## Data Sources
- `useRegistry()` → all tickers (base data for all cards)
- `useUserTickers()` → linked ticker set
- `usePortfolio()` → portfolio holdings (determines tier)
- `useWatchlist()` → enrichment (change%, sparkline) for linked
- `useAnalysisLatest()` → enrichment (sentiment, returns) for linked

## Page Features
- **Toolbar**: search, market pills (All/India/US), Select All checkbox, bulk actions (3-dot menu)
- **Sub-filter pills**: All / Portfolio / Watchlist / Unlinked (with counts)
- **Grid**: 3 cols desktop, 2 tablet, 1 mobile — 6 cards per page with pagination
- **Checkbox**: bottom-right of card, visible on hover + when selected
- **AddStockModal**: opens pre-filled when +Portfolio clicked on any card

## Extracted Hooks
- `frontend/hooks/useTickerRefresh.ts` — POST start + poll status pattern
- `frontend/hooks/useLinkUnlink.ts` — link/unlink with optimistic SWR cache

## Old Marketplace
- `analytics/marketplace/page.tsx` replaced with `redirect("/analytics")`
- "Link Stock" removed from sidebar nav and HeroSection quick actions

## Jira: ASETPLTFRM-204

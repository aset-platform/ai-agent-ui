# Algo Portfolio Dashboard Tab (Epic B)

**Shipped:** 2026-05-24, PR #243, merge SHA `538024e` on dev.

## What it does

Third "Algo" tab in the dashboard `WatchlistWidget` showing
currently-open algo-attributed positions (intraday MIS +
overnight CNC) with Symbol / Qty / Avg / LTP / PnL% /
Strategy / Days-held columns. Pro+superuser gated; general
users don't see the tab at all.

## New endpoint

`GET /v1/algo/portfolio/positions` —
`backend/algo/routes/portfolio.py::_get_algo_positions_impl`:

1. Cache check on `cache:algo:portfolio:positions:{user_id}`
   (TTL 60s).
2. `_build_kite_for_user(user_id)` — reuses Epic A's helper.
   On RuntimeError (missing/expired creds) → empty
   `positions=[]` with `market_open` flag (FE renders
   empty-state cleanly for users without Kite connected).
3. `asyncio.gather` of `to_thread(kc.positions)` +
   `to_thread(kc.holdings)`.
4. Net positions filtered to `qty != 0`, holdings filtered to
   `settled + t1 > 0`.
5. `_fetch_strategy_attribution(uid, symbols,
   since_date=_ATTRIBUTION_SINCE)` — extended with kwarg-only
   `since_date` so CNC overnight positions opened on prior
   trading days stay attributed (`_ATTRIBUTION_SINCE =
   "2024-01-01"`).
6. Drop rows without `strategy_id` (algo-attributed ONLY).
7. Sort by `(-pnl_inr, tradingsymbol)`.
8. `cache.set` with `model_dump_json()` after compute (TTL-only
   invalidation in v1; no write-through).

## Wire shape

```python
class AlgoPositionRow(BaseModel):
    tradingsymbol: str               # "INFY"
    internal_ticker: str             # "INFY.NS"
    product: Literal["MIS", "CNC"]
    quantity: int                    # absolute value
    avg_price: Decimal
    last_price: Decimal
    pnl_inr: Decimal                 # signed
    pnl_pct: Decimal
    strategy_id: UUID
    strategy_name: str
    entry_ts: datetime | None        # tz-aware UTC
    days_held: int                   # max(0, today_ist - entry_ist)
    t1_pending: bool                 # CNC settled=0 + t1>0

class AlgoPositionsResponse(BaseModel):
    positions: list[AlgoPositionRow]
    as_of: datetime                  # server fetch time
    market_open: bool                # drives FE refresh cadence
```

## Frontend

- `frontend/lib/types/algoPortfolio.ts` — TS mirror, Decimals
  as `string` (project convention).
- `frontend/hooks/useAlgoPositions.ts` — SWR hook, conditional
  `refreshInterval` (5s during market hours, 60s otherwise —
  driven by `market_open` field from response).
- `frontend/components/widgets/algo/AlgoPositionsTab.tsx` —
  table + loading + error + empty-state amber card with deep
  link to `/algo-trading/strategies?tab=live`.
- `frontend/components/widgets/WatchlistWidget.tsx` — new
  `algoTabEnabled?: boolean` prop (default `false`); third tab
  button + body branch conditionally rendered.
- `frontend/app/(authenticated)/dashboard/DashboardClient.tsx`
  — derives `algoTabEnabled` from
  `profile?.role === "pro" || profile?.role === "superuser"`.

## Reused / extended helpers

- `_build_kite_for_user(user_id)` from Epic A's
  `backend/algo/live/budget.py` — raises `RuntimeError` on
  missing creds (NOT `HTTPException` — the portfolio impl
  catches `RuntimeError` to render empty-state).
- `_fetch_strategy_attribution(user_id, symbols, *,
  since_date=None)` at `backend/algo/routes/live.py:484` —
  added kwarg-only `since_date` so the today-only default for
  the existing LiveDashboard positions endpoint is preserved;
  the dashboard Algo tab passes the 2024-01-01 floor.

## Tests

- 11 backend tests in `test_portfolio_routes.py`
- 6 vitest tests (`useAlgoPositions` hook smoke + 3
  `AlgoPositionsTab` + 2 `WatchlistWidget` algo-tab gating)
- 1 E2E smoke at `e2e/tests/frontend/dashboard-algo-tab.spec.ts`

## Out of scope (v1, deferred)

Closed positions, slide-over with strategy AST detail,
group-by-strategy subtotals, multi-broker, multi-currency,
sortable headers, CSV download, pagination, per-row close
buttons.

## Spec / plan

- `docs/superpowers/specs/2026-05-24-algo-portfolio-tab-design.md`
- `docs/superpowers/plans/2026-05-24-algo-portfolio-tab.md`

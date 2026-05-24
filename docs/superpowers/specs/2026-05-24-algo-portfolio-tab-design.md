# Algo Portfolio Tab — Design

**Status:** Draft → review
**Date:** 2026-05-24
**Owner:** Abhay Kumar Singh (asequitytrading@gmail.com)
**Epic:** B (sibling to A: Order Budget Reservation, shipped as PR #242)

---

## 1. Goal

Surface the user's currently-open algo positions in the dashboard's
existing portfolio widget — so that on the home page the user can
toggle between **Portfolio · Watchlist · Algo** without leaving the
dashboard. The Algo tab is read-only, shows both intraday (MIS) and
overnight (CNC) holdings, and includes per-row strategy attribution
+ days-held so the user can answer "what is my algo book holding
right now, by which strategy, for how long?" at a glance.

## 2. Background

Today the dashboard's portfolio widget (`WatchlistWidget`) exposes
two tabs: `portfolio` (manually-tracked holdings) and `watchlist`.
Algo-originated positions are surfaced separately on
`/algo-trading/strategies?tab=live` (`LiveDashboard`), via
`OpenPositionsWidget` (intraday MIS only, no CNC overnight). The
user requested that the dashboard's portfolio widget gain a third
"Algo" tab as an at-a-glance view of the algo book without
navigating to the algo-trading section.

Why this matters: algo-originated capital is invisible on the
dashboard today. A user with ₹50k in algo CNC overnight + ₹30k in
manual portfolio currently sees the dashboard hero show only the
manual ₹30k; their algo capital is one click away on a separate
page. Making the algo book a first-class tab on the home page
restores end-to-end visibility.

## 3. Decisions matrix

| # | Question | Decision |
|---|---|---|
| 1 | Scope of "open positions" | **Currently-open ONLY** — intraday MIS + overnight CNC. Closed positions excluded; that history lives in LiveDashboard's Recent Fills + Attribution tab. |
| 2 | Algo vs manual | **Algo-attributed only.** A Kite position without an `algo.events.order_filled_live` row is NOT shown. Manual brokerage buys via the Zerodha Kite app do not appear on the Algo tab. |
| 3 | Tab placement | **Third tab inside the existing `WatchlistWidget`** (`portfolio · watchlist · algo`). |
| 4 | Columns per row | Symbol · Qty · Avg · LTP · PnL % · Strategy · Days held. |
| 5 | Backend wiring | **New combined endpoint** `GET /v1/algo/portfolio/positions` returning both MIS + CNC merged + attributed + days-held computed. |
| 6 | LTP refresh | 5 s during market hours, 60 s off-hours (reuses the `useLivePortfolioTotals` cadence). |
| 7 | Row click | Selects the ticker — same behavior as the existing portfolio / watchlist tabs (drives the right-side analysis widgets). No bespoke slide-over in v1. |
| 8 | Empty state | "No algo positions — go to Algo Trading" amber card + deep link to `/algo-trading/strategies?tab=live`. |
| 9 | Tab visibility | **Pro / superuser only.** General users never see the tab. |

## 4. Out of scope (v1)

- Closed / today's exited positions (LiveDashboard > Recent Fills owns that history).
- Bespoke slide-over with strategy AST / event-log detail on row click.
- Group-by-strategy view + per-strategy P&L subtotals (LiveDashboard > Attribution tab covers this).
- Multi-broker / multi-account support (single Kite connection assumption holds).
- Multi-currency. INR only.
- Click-to-sort column headers (fixed sort by `pnl_inr DESC`).
- CSV download.
- Pagination (assumes < 50 open positions).
- Per-row "close now" / panic-close buttons (LiveDashboard's PanicCloseButton + Positions tab own this surface).
- WebSocket push for live updates (polling is fine at this cadence).
- General-user upsell ("Upgrade to Pro to use algo") — pro/superuser gating is hard; general users never see the tab.

## 5. Architecture

```
┌────────────────────────────────────────────────────────────┐
│ Dashboard (frontend/app/(authenticated)/dashboard/)        │
│  WatchlistWidget                                           │
│   tabs: portfolio | watchlist | algo*                      │
│         (* visible to pro / superuser only)                │
│         └─ AlgoPositionsTab ──> useAlgoPositions           │
└──────────────────────────────────────┬─────────────────────┘
                                       │ apiFetch
                                       ▼
┌────────────────────────────────────────────────────────────┐
│ GET /v1/algo/portfolio/positions  (pro_or_superuser)       │
│   backend/algo/routes/portfolio.py                         │
│                                                            │
│  parallel asyncio.gather:                                  │
│   ├─ kc.positions().get("net") ─► intraday MIS rows        │
│   └─ kc.holdings()             ─► CNC overnight rows       │
│                                                            │
│  merge → join _fetch_strategy_attribution(uid, symbols)    │
│     ▶ drop rows with no algo attribution                   │
│  compute days_held from attribution.entry_ts_utc (IST midnight)│
│  return AlgoPositionRow[] + as_of + market_open            │
│                                                            │
│  Redis cache: cache:algo:portfolio:positions:{user_id}     │
│               TTL 60s (TTL_STABLE per §5.13)               │
└────────────────────────────────────────────────────────────┘
```

**Reused helpers (no new code paths):**

- `_build_kite_for_user(user_id)` from Epic A's `backend/algo/live/budget.py` — pinned `dry_run=False` real-mode KiteClient, raises `RuntimeError` if creds missing.
- `_fetch_strategy_attribution(uid, symbols)` from `backend/algo/routes/live.py` — joins `algo.events` `order_filled_live` rows by tradingsymbol → `{symbol: {strategy_id, strategy_name, entry_ts_utc, reason}}`.
- `_to_internal_ticker(tradingsymbol)` from `backend/algo/live/position_hydration.py` — maps Kite tradingsymbol (e.g. `INFY`) to internal form (e.g. `INFY.NS`).
- `is_market_open_ist()` from `backend/algo/live/reconciliation.py`.

**New backend file:** `backend/algo/routes/portfolio.py` (~180 LOC):

- `AlgoPositionRow` + `AlgoPositionsResponse` Pydantic models (§6).
- `_get_algo_positions_impl(*, user_id) -> AlgoPositionsResponse` — pure async function delegated to by the FastAPI handler. Testable without HTTP harness (Epic A's lift-to-module-level pattern).
- `_row_from_position(...)` / `_row_from_holding(...)` adapters from Kite raw dicts → `AlgoPositionRow`.
- `create_portfolio_router() -> APIRouter` — single endpoint `GET /positions` under prefix `/algo/portfolio`.

**Backend wiring:**

- `backend/algo/routes/__init__.py` — add `create_portfolio_router` export.
- `backend/routes.py` — `app.include_router(create_portfolio_router(), prefix="/v1")` next to Epic A's `create_budget_router()`.
- `backend/cache.py` `_CACHE_INVALIDATION_MAP` — when an `algo.events` `order_filled_live` row is written, invalidate `cache:algo:portfolio:positions:{user_id}`. The map key is the table identifier; the helper computes the per-user pattern.

**New frontend files:**

- `frontend/lib/types/algoPortfolio.ts` — TS shapes mirroring backend Pydantic.
- `frontend/hooks/useAlgoPositions.ts` — SWR hook, `refreshInterval` 5_000 during market hours, 60_000 otherwise (computed from response `market_open` field).
- `frontend/components/widgets/algo/AlgoPositionsTab.tsx` — table body + empty state + click handler that calls `onSelectTicker(internal_ticker)`.
- `frontend/components/widgets/algo/AlgoPositionRow.tsx` — single row component.

**Frontend patches:**

- `frontend/components/widgets/WatchlistWidget.tsx`:
  - Extend `WidgetTab` type to `"portfolio" | "watchlist" | "algo"`.
  - New prop `algoTabEnabled: boolean`.
  - Conditional tab button (only when `algoTabEnabled`).
  - New tab body branch rendering `<AlgoPositionsTab onSelectTicker={onSelectTicker} />`.
- `frontend/app/(authenticated)/dashboard/DashboardClient.tsx`:
  - Read `useProfile()` → derive `role`.
  - `algoTabEnabled = role === "pro" || role === "superuser"`.
  - Pass `algoTabEnabled` prop down.

## 6. Data contract

### `AlgoPositionRow` (Pydantic, `backend/algo/routes/portfolio.py`)

```python
class AlgoPositionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tradingsymbol: str               # e.g. "INFY"
    internal_ticker: str             # e.g. "INFY.NS"
    product: Literal["MIS", "CNC"]
    quantity: int
    avg_price: Decimal
    last_price: Decimal
    pnl_inr: Decimal                 # qty * (ltp - avg)
    pnl_pct: Decimal                 # 100 * (ltp - avg) / avg, 0 when avg == 0
    strategy_id: UUID
    strategy_name: str
    entry_ts: datetime | None        # UTC tz-aware; None when attribution
                                     # is partial
    days_held: int                   # 0 for today; floor((today_ist -
                                     # entry_date_ist).days), clamped ≥ 0
    t1_pending: bool = False         # SEBI T+1: holdings row with
                                     # quantity=0 + t1_quantity > 0
```

### `AlgoPositionsResponse`

```python
class AlgoPositionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: list[AlgoPositionRow]
    as_of: datetime                  # server-side fetch time, UTC
    market_open: bool                # drives the FE refresh cadence
```

### Filtering rule

If `_fetch_strategy_attribution` returns no row for a
`(internal_ticker, …)`, drop the position. Only attributed rows are
included.

### Sort

Server-side sort: `pnl_inr DESC`, then `tradingsymbol ASC` as
tiebreak. Frontend renders the response order as-is.

### `days_held` computation

```python
IST = ZoneInfo("Asia/Kolkata")  # already in reconciliation.py

def _days_held(entry_ts: datetime | None) -> int:
    if entry_ts is None:
        return 0
    today_ist = datetime.now(IST).date()
    entry_ist = entry_ts.astimezone(IST).date()
    return max(0, (today_ist - entry_ist).days)
```

### TypeScript mirror

```typescript
// frontend/lib/types/algoPortfolio.ts
export interface AlgoPositionView {
  tradingsymbol: string;
  internal_ticker: string;
  product: "MIS" | "CNC";
  quantity: number;
  avg_price: string;          // Decimal as string per project convention
  last_price: string;
  pnl_inr: string;
  pnl_pct: string;
  strategy_id: string;
  strategy_name: string;
  entry_ts: string | null;    // ISO-8601 UTC
  days_held: number;
  t1_pending: boolean;
}

export interface AlgoPositionsResponse {
  positions: AlgoPositionView[];
  as_of: string;
  market_open: boolean;
}
```

## 7. UI surface

### Tab strip (in `WatchlistWidget`)

```
┌─────────────────────────────────────────────────────────┐
│ [Portfolio]  [Watchlist]  [Algo]              + (add)  │
│  ─────────                                              │
│                                                         │
│  …existing portfolio table…                             │
└─────────────────────────────────────────────────────────┘
```

When `algoTabEnabled === false`, the `[Algo]` button is not rendered (general users see the existing two tabs only).

### Algo tab populated

```
┌─────────────────────────────────────────────────────────┐
│ [Portfolio]  [Watchlist]  [Algo]                        │
│                            ─────                        │
│                                                         │
│ Symbol     Qty  Avg     LTP    PnL%   Strategy   Days  │
│ ───────── ──── ────── ────── ─────── ─────────  ─────  │
│ INFY.NS    50  1500.0 1572.5  +4.83% RSI(2) v3    1   │
│ TCS.NS     20  3450.5 3401.2  -1.43% Mean Rev MIS  0   │
│ HDFCBANK   30  1680.0 1721.4  +2.46% Bollinger    7   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

- Symbol = `tradingsymbol` (without `.NS`); on click → `onSelectTicker(internal_ticker)`.
- Days column shows `0` for today; positive integers for older.
- PnL % color: emerald ≥ 0, rose < 0.
- Strategy name truncates to ~14 chars with title-tooltip on hover.
- T+1 pending rows render `Qty` as `0+10 T+1` (settled + t1 styled italic).

### Empty state

```
┌─────────────────────────────────────────────────────────┐
│ [Portfolio]  [Watchlist]  [Algo]                        │
│                            ─────                        │
│                                                         │
│  ⚠ No algo positions open.                              │
│                                                         │
│  Live algo trading places intraday + overnight          │
│  positions that show up here. Set up a live strategy →  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

Amber `bg-amber-50 / dark:bg-amber-950/30` card matching the BudgetPanel empty state from Epic A. The link target is `/algo-trading/strategies?tab=live`.

### Loading state

Three skeleton rows in slate-200 / dark:slate-700, matching the existing portfolio-tab skeleton.

### Error state

Rose-coloured single-row banner ("Algo positions unavailable") at the top of the tab body. SWR auto-retries; no manual retry button.

## 8. Caching

| Layer | Key | TTL | Invalidation |
|---|---|---|---|
| Redis (backend) | `cache:algo:portfolio:positions:{user_id}` | 60s | Implicit via TTL only — no write-through invalidation in v1. The 60s ceiling is acceptable on the dashboard surface (the user isn't watching this during a fill). The existing `/v1/algo/live/positions` endpoint follows the same pattern. |
| SWR (frontend) | `${API_URL}/algo/portfolio/positions` | n/a (SWR-managed) | `refreshInterval` 5s / 60s. `revalidateOnFocus: false`. `mutate()` not called from anywhere (no writes from this surface). |

If the 60 s ceiling proves too coarse in practice, follow-up: call `cache.invalidate("cache:algo:portfolio:positions:{user_id}")` from `LiveRuntime` right after a successful `algo.events.order_filled_live` write. Same pattern as `backend/algo/routes/webhooks.py:362` (`cache.invalidate(f"cache:algo:postbacks:{user_id}")` after a Kite postback). Deferred to keep v1 small.

## 9. Testing strategy

### Backend — `backend/algo/tests/test_portfolio_routes.py` (6 tests)

| # | Test | Verifies |
|---|---|---|
| 1 | `test_returns_empty_when_no_kite_positions` | Empty Kite response → empty rows |
| 2 | `test_filters_out_unattributed_positions` | 2 Kite positions, only 1 attributed → 1 row |
| 3 | `test_merges_mis_and_cnc_into_one_response` | MIS in `net`, CNC in `holdings`, both attributed → 2 rows, correct products |
| 4 | `test_days_held_zero_for_today_entry` | `entry_ts` = today IST midnight → `days_held == 0` |
| 5 | `test_days_held_three_for_three_calendar_days_ago` | `entry_ts` = 3 IST midnights ago → `days_held == 3` |
| 6 | `test_t1_pending_flagged_on_cnc_settling` | `holdings` row with `quantity=0` + `t1_quantity=10` → `t1_pending == True`, qty = 10 |

Patches `_build_kite_for_user`, `_fetch_strategy_attribution`, `get_cache`. No real Kite or PG.

### Frontend — `frontend/components/widgets/algo/__tests__/AlgoPositionsTab.test.tsx` (3 tests)

| # | Test | Verifies |
|---|---|---|
| 1 | `renders_rows_when_positions_present` | Hook returns 2 rows → both rendered with correct symbol/qty/strategy |
| 2 | `renders_empty_state_with_deep_link` | Empty array → "No algo positions" + link target `/algo-trading/strategies?tab=live` |
| 3 | `row_click_calls_on_select_ticker` | Row click → `onSelectTicker(internal_ticker)` called once |

Plus extend existing `WatchlistWidget` test with one assertion: tab button is NOT rendered when `algoTabEnabled === false`.

### E2E — `e2e/tests/frontend/dashboard-algo-tab.spec.ts` (1 smoke)

```typescript
test("algo tab renders on dashboard for superuser", async ({page}) => {
  await page.goto("/dashboard");
  await page.getByTestId("dashboard-watchlist-tab-algo").click();
  await expect(
    page.getByTestId("dashboard-algo-positions-table"),
  ).toBeVisible();
});
```

Pre-authenticated superuser storage state. Doesn't depend on the user having algo positions on the env (asserts the table container is visible — even if its body is the empty-state card).

### Manual smoke (post-merge)

- Login as superuser with real algo positions
- Click Algo tab — verify MIS + CNC rows appear
- Verify `days_held` matches `algo.events` history
- Verify LTP refreshes every 5 s during market hours
- Verify PnL % sign + color match expectation
- Verify row click selects the ticker (right-side widgets update)
- Verify general user (separate login) sees only `portfolio · watchlist` tabs

## 10. Performance budget

- Backend: 1 cached endpoint, 60s TTL. Kite calls happen at most every 60 s per user; the Pandas-free pure-async path is well under 100 ms p99 on warm cache.
- Frontend: hook adds ~3 KB gz to the dashboard bundle (one SWR hook + a small component tree). LCP unchanged (the Algo tab is below the fold).
- Polling: 5 s on a small (< 1 KB) JSON response is comfortable.
- No new Iceberg writes. No new PG writes.

## 11. Open invariants (lock in)

- v1 is **read-only** — no buttons, no writes from this tab.
- **Algo attribution is authoritative.** A Kite position without an `algo.events.order_filled_live` row is NOT shown — even if the user is certain they opened it via algo. Avoids edge cases where the event log is incomplete (e.g. an algo restart that lost the postback).
- **Cache invalidation does not refresh attribution.** A fill triggers cache clear; the next fetch re-joins attribution fresh.
- **`as_of`** is server-side fetch time, NOT a Kite snapshot time (Kite doesn't expose one).
- **No new env vars.** All knobs (cache TTL, refresh interval) are code constants.

## 12. Companion epics still pending

- **Epic C**: Watchlist bulk ops + universe binding for strategies that pick `universe=watchlist`.

## 13. References

- Sibling epic: **A — Order Budget Reservation** (PR #242, branch `feature/algo-order-budget-reservation`).
- Existing helpers reused: `_build_kite_for_user` (Epic A), `_fetch_strategy_attribution` (`backend/algo/routes/live.py`), `_to_internal_ticker` (`position_hydration.py`).
- Project conventions: CLAUDE.md §5.3 (SWR data fetch), §5.13 (Redis caching), §5.7 (pro_or_superuser gating).

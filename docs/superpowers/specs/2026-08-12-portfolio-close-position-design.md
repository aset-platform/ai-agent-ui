# Portfolio — Close Position + Realized-P&L History — Design

**Status:** Design approved.

---

## 1. Motivation

The portfolio list supports add / edit / view / delete on open holdings, but
there is **no way to close a sold position**. Deleting a holding you sold
throws away the fact you ever held it and the realized gain/loss. Users who
sell need to record the sale, see realized P&L, and keep a history of closed
positions.

## 2. Current state (as-is)

- `stocks.portfolio_transactions` (Iceberg, append-only) stores one row per
  transaction with a **`side` column (`BUY`/`SELL`)** — but only `BUY` rows
  are ever written or read. `get_portfolio_holdings` filters `side='BUY'`.
- The list shows **one row per BUY lot** (`transaction_id`); `avg_price` is
  that lot's price. Edit (`update_portfolio_transaction`) and delete
  (`delete_portfolio_transaction`) already do copy-on-write on this table.
- CRUD: `POST/PUT/DELETE /users/me/portfolio[/{transaction_id}]`.
- No closed / sold / realized-P&L concept exists for the user portfolio.

## 3. Scope

1. **Close a position** (whole or partial) from a new row action → a
   Close-Position modal capturing sell qty / price / date / fees, with a live
   realized-P&L preview.
2. **Realized P&L is stored** per closed lot (lot-exact cost basis).
3. **Two portfolio tabs** — **Portfolio (Open)** (today's list) and
   **Portfolio (Closed)** (new realized-P&L history), alongside Watchlist and
   Algo.

## 4. Data model

**New PG table `portfolio_closed_positions`** (mutable/queryable OLTP → PG per
CLAUDE.md §5.1; no Iceberg schema evolution). Alembic migration.

| column | type | notes |
|---|---|---|
| `id` | uuid / str PK | |
| `user_id` | str, not null, indexed | |
| `ticker` | str, not null | |
| `quantity` | numeric, not null | shares closed in this record |
| `buy_price` | numeric, not null | cost basis = source lot's price |
| `sell_price` | numeric, not null | |
| `buy_date` | date, nullable | source lot `trade_date` if known |
| `sell_date` | date, not null | |
| `fees` | numeric, not null, default 0 | |
| `realized_pnl` | numeric, not null | `(sell_price − buy_price)·qty − fees` |
| `realized_pnl_pct` | numeric, nullable | `realized_pnl / (buy_price·qty)` |
| `currency` | str, not null | from source lot |
| `market` | str, not null | from source lot |
| `source_transaction_id` | str, nullable | the BUY lot closed against |
| `notes` | str, nullable | |
| `created_at` | timestamptz, default now | |

Index: `(user_id, sell_date DESC)` for the history list.

## 5. Backend

### 5.1 Close endpoint — `POST /users/me/portfolio/{transaction_id}/close`
Body: `{ quantity: number, sell_price: number, sell_date: str (ISO),
fees?: number, notes?: str }`.

Flow (single handler, in the same router as the existing portfolio CRUD):
1. Load the BUY lot by `(transaction_id, user_id)`; 404 if absent.
2. Validate `0 < quantity ≤ lot.quantity` (per-lot close only); else 400.
3. `cost = lot.price · quantity`; `proceeds = sell_price · quantity`;
   `realized_pnl = proceeds − cost − fees`;
   `realized_pnl_pct = realized_pnl / cost` (cost > 0).
4. Insert the `portfolio_closed_positions` row (currency/market/buy_date from
   the lot).
5. **Reduce the source lot** — reuse existing repo methods:
   - partial (`quantity < lot.quantity`) → `update_portfolio_transaction`
     (set `quantity = lot.quantity − quantity`; price/avg unchanged).
   - full (`quantity == lot.quantity`) → `delete_portfolio_transaction`.
6. Invalidate `cache:portfolio:*` and `cache:dash:*` for the user
   (mirrors the add/edit/delete invalidation).
7. Return the created closed record (incl. realized P&L).

Tools/repo return error strings; the route raises `HTTPException` (§5.1
backend rule).

### 5.2 Closed-list endpoint — `GET /users/me/portfolio/closed`
Returns `{ closed: ClosedPosition[], totals: { realized_pnl, invested_closed,
proceeds, ... } }`, newest `sell_date` first. Cache under
`cache:portfolio:closed:{user_id}` (TTL_STABLE=300).

### 5.3 Repo
Add `add_closed_position(row)` and `get_closed_positions(user_id)` on the PG
side (async, via `_pg_session()` / the existing portfolio route's session
pattern). Open-holdings read is unchanged (the lot reduce keeps the BUY-sum
correct).

## 6. Frontend

### 6.1 Tabs
Replace the single **Portfolio** tab with **Portfolio (Open)** and
**Portfolio (Closed)**; keep Watchlist and Algo. Open = today's list; Closed =
the realized-P&L history.

### 6.2 Close action + modal
- New **row "close" icon** (e.g. an `XCircle` / exit glyph) in each open-lot
  row's action group (next to refresh / view / delete), with a `data-testid`
  (§5.14) and an accessible label ("Close position").
- Clicking opens **`ClosePositionModal`**, mounted ONCE via
  `PortfolioActionsProvider` and dispatched through `usePortfolioActions()`
  (§5.6); `z-[70]`. Fields: quantity (default = full lot, capped at lot qty),
  sell price, sell date (default today), fees (default 0), notes. **Live
  realized-P&L preview** `(sell_price − lot.avg_price)·qty − fees`, colored by
  sign, currency via `tickerCurrency(ticker)` (never hardcode ₹).
- `closeHolding(transactionId, body)` added to `usePortfolio`
  (`apiFetch` + POST); on success `mutate()` the open list AND revalidate the
  closed list.

### 6.3 Closed tab view
- `useClosedPositions` SWR hook (`apiFetch`, `revalidateOnFocus:false`,
  2-min dedup — §5.3) → `GET /users/me/portfolio/closed`.
- List rows: ticker, qty, buy→sell price, realized P&L (₹ + %), sell date.
  A realized-P&L **total** at the top. If the catalog reaches ≥8 columns,
  adopt §5.4 (`useColumnSelection` + `<ColumnSelector>` +
  `<DownloadCsvButton>` + header sort + locked ticker column).

## 7. Non-goals (YAGNI)
- Per-lot close only (the row you click) — no cross-lot FIFO/auto-matching.
- No editing a closed record (delete-and-redo if wrong) — a delete-closed
  endpoint MAY be added but is out of scope unless asked.
- No brokerage tax-lot modeling beyond the single `fees` field.
- No SELL rows written to `portfolio_transactions` (closed history lives in
  the new PG table; the open ledger stays BUY-only, reduced in place).

## 8. Testing (§4.4.26 — happy + ≥1 error path)
- Backend: full close removes the lot + inserts closed row with correct
  realized P&L; partial close reduces lot qty + inserts closed row; `quantity
  > lot.quantity` → 400; unknown `transaction_id` → 404; cache invalidated.
- Frontend (vitest): modal computes the realized-P&L preview correctly
  (incl. fees, loss case); Closed tab renders totals; e2e testid present.

## 9. Rollout
- New Alembic table + new route/model → **backend restart** to load
  (standard per §6.2; NOT an Iceberg schema evolution). A restart drops the
  live Kite WS — coordinate; user reconnects from the Algo UI after.
- `redis-cli FLUSHALL` after deploy (cache-touching change, §4.5).

## 10. Open questions
- Should closed positions be deletable (mis-entry correction)? Deferred —
  add a `DELETE /users/me/portfolio/closed/{id}` only if asked.
- Should the Open tab show a small "realized P&L to date" chip sourced from
  the closed total? Nice-to-have, deferred.

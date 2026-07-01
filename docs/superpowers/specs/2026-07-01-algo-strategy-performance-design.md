# Strategy Performance page — mode-aware filters + trade-level metrics

Date: 2026-07-01
Status: approved (brainstorm), pending implementation plan

## 1. Problem

The Strategies → Performance tab (`frontend/components/algo-trading/PerformanceTab.tsx`,
backed by `GET /v1/algo/performance/runs`) only reads `algo.runs` (Postgres).
Only **backtest** and **walk-forward** runs write rows to `algo.runs` — the
**paper** and **live** runtimes never do (confirmed in
`shared/architecture/strategy-promotion-workflow`: "Paper runtime does NOT
create algo.runs rows — it emits events to Iceberg `algo.events`"; same is
true for live). So today's page is structurally blind to paper/live
performance, has no mode or strategy filter, and cannot answer the basic
question "how is my live strategy actually doing — win rate, biggest
losing trade, which tickers are dragging it down."

## 2. Goals

1. Mode filter: Backtest / Walk-forward / Paper / Live pills, **default
   Live**.
2. Strategy filter: dropdown scoped to the selected mode, **default All**.
   - Backtest / Walk-forward → unfiltered (all strategies; matches the
     existing convention that backtest/walk-forward pickers show all 3
     promotion states).
   - Paper → strategies currently in `mode ∈ {paper, live}` (a strategy
     graduated to live still shows its paper history).
   - Live → strategies currently `mode = live` only (per 1a's literal
     spec). Reuses the existing `filterStrategiesByMode` helper in
     `frontend/hooks/useStrategies.ts` — no new filtering logic needed.
3. Time range: lookback presets (7d / 30d / 90d / All, default 30d) **plus**
   a custom date-range option (native `<input type="date">` From/To pair,
   mirroring the existing pattern in `WalkForwardSubTab.tsx` /
   `BacktestRunForm.tsx` — no new date-picker library).
4. Metrics, per strategy: total trades, win rate %, total realised PnL,
   biggest winning trade, biggest losing trade, avg win, avg loss, profit
   factor. Max drawdown % only where a true capital baseline exists
   (backtest/walk-forward); `null` for paper/live (see §7).
5. Trade-level drill-down + per-ticker breakdown when a single strategy is
   selected, to support universe tuning (which tickers to drop).

## 3. Non-goals (this iteration)

- Equity-curve chart for paper/live (blocked on a per-strategy capital
  baseline, which doesn't exist today — `algo.user_budget.allocated_inr`
  is account-wide, not per-strategy).
- Sharpe/Sortino, max consecutive-loss streak — buildable later on top of
  the new table without schema changes; not required for this pass.
- Any change to `backend/algo/live/runtime.py` or
  `backend/algo/paper/runtime.py` hot paths. This feature is entirely a
  read-path + a new offline batch job; it must not touch the live/paper
  runtimes (per CLAUDE.md's repeated live-trading-is-sensitive guidance).

## 4. Architecture

```
algo.events (Iceberg, unchanged — no new writes, no live-page queries)
     │  order_filled / order_filled_live, mode ∈ {paper, live}
     ▼
NEW daily batch job "algo_closed_trades_rollup" (16:30 IST, Mon–Fri)
     │  FIFO-pairs BUY→SELL fills per (user, strategy, ticker), reusing
     │  the pairing logic already in routes/attribution.py (today scoped
     │  to a single day only — extracted into a shared helper and
     │  generalised to a trailing 400-day window, matching the existing
     │  OHLCV warmup convention).
     │  Idempotent upsert, ON CONFLICT (buy_event_id, sell_event_id)
     │  DO NOTHING — safe to re-run, self-healing.
     ▼
NEW table: algo.closed_trades (Postgres)
     │
     ▼
NEW endpoint: GET /v1/algo/performance/summary
  mode=backtest|walkforward|paper|live, strategy_id?,
  lookback=7d|30d|90d|all  OR  start=&end=
     │  backtest/walkforward → algo.runs.summary_json.trade_list
     │                          (BacktestSummary already has equity_curve +
     │                          capital-based max_drawdown_pct — reused
     │                          as-is, just filtered/aggregated by lookback)
     │  paper/live           → algo.closed_trades
     ▼
Rebuilt Performance page: filter bar → strategy comparison table →
  (trade drill-down + per-ticker breakdown when one strategy is picked)
```

**Why an offline daily job instead of querying `algo.events` from the page
on demand:** avoids repeating the `algo.events` bloat/read-pressure
incident (`shared/debugging/algo-events-bloat-redesign` — 8.2 GB / 22k
files from exactly this kind of unthrottled access pattern) and keeps
market-hours load on Iceberg unchanged. The job is pure
read-Iceberg/write-Postgres, same shape as the existing
`algo_events_retention` / `risk_state_reset` scheduled jobs.

**Why re-derive trades from a 400-day window every run instead of
carrying forward state:** a position can open on day N and close on day
N+40 (CNC swing trade), so a same-day-only scan would miss it. Given
long-only Indian-equities trade volume is modest (tens, not thousands, of
trades per strategy per day), a bounded full re-derivation is simpler and
safer than maintaining carry-forward state, and the idempotent upsert key
makes it self-healing if a run is skipped or re-run.

## 5. Data model

```sql
CREATE TABLE algo.closed_trades (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  strategy_id uuid REFERENCES algo.strategies(id) ON DELETE SET NULL,
  mode varchar(16) NOT NULL,          -- 'paper' | 'live'
  ticker varchar(32) NOT NULL,
  qty integer NOT NULL,
  avg_price numeric(12,4) NOT NULL,   -- entry (buy fill)
  fill_price numeric(12,4) NOT NULL,  -- exit (sell fill)
  opened_at date NOT NULL,
  closed_at date NOT NULL,
  opened_at_ts_ns bigint,
  closed_at_ts_ns bigint,
  realised_pnl_inr numeric(14,2) NOT NULL,
  return_pct numeric(8,4) NOT NULL,
  exit_reason varchar(32) NOT NULL DEFAULT 'signal',
  buy_event_id varchar(64) NOT NULL,
  sell_event_id varchar(64) NOT NULL,
  computed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (buy_event_id, sell_event_id)
);
CREATE INDEX ix_closed_trades_lookup
  ON algo.closed_trades (user_id, strategy_id, mode, closed_at DESC);
```

Column names deliberately mirror the existing `TradeRow` Pydantic model
(`backend/algo/backtest/types.py`) — `avg_price` / `fill_price` /
`opened_at` / `realised_pnl_inr` / `return_pct` / `exit_reason` — so the
frontend can use one shared trade type and one shared table component
(generalised from `BacktestTradeTable.tsx`) across all 4 modes instead of
building a parallel one for paper/live.

This is a Postgres table, not an Iceberg table, despite being
append-only-in-spirit: volume is low (bounded by real trade counts, not
tick/event firehose volume) and the access pattern needs arbitrary
`WHERE`/`ORDER BY`/indexed lookups for a UI page — exactly what PG does
well and Iceberg/DuckDB does not. This does not need `_HOT_ICEBERG_TABLES`
/ `ALL_TABLES` enrollment (CLAUDE.md §4.3 rule 21) because it isn't an
Iceberg table.

## 6. Backfill

One-time backfill required before the recurring job takes over: same
FIFO-pairing code, run once over the *entire* available `algo.events`
history (not the 400-day trailing window) for every user/strategy,
upserting into `algo.closed_trades`. Implemented as an idempotent
management script (safe to re-run) rather than folded silently into the
scheduled job, so it's an explicit, observable ops step during rollout.

## 7. API contract

`GET /v1/algo/performance/summary?mode=live&strategy_id=&lookback=30d`

Mutually exclusive time-range params: `lookback=7d|30d|90d|all` **or**
`start=YYYY-MM-DD&end=YYYY-MM-DD`. If both are present, `lookback` wins
and the conflict is logged (not raised — avoid breaking the page over a
client bug).

```jsonc
{
  "mode": "live",
  "lookback": "30d",
  "strategies": [   // one row per strategy in scope — always returned
    {
      "strategy_id": "...", "strategy_name": "RSI(2) v5",
      "total_trades": 6, "wins": 4, "losses": 2,
      "win_rate_pct": 66.7,
      "total_pnl_inr": 4210.50,
      "biggest_win": {"ticker": "ITC", "pnl_inr": 2100.0, "closed_at": "2026-06-30"},
      "biggest_loss": {"ticker": "SHAILY", "pnl_inr": -890.0, "closed_at": "2026-06-25"},
      "avg_win_inr": 1350.2, "avg_loss_inr": -610.4,
      "profit_factor": 2.21,
      "max_drawdown_pct": null   // populated only for backtest/walkforward
    }
  ],
  "trades": [ /* TradeRow[], populated only when strategy_id is set */ ]
}
```

Cache key: `cache:algo:perf:{user_id}:{mode}:{strategy_id|all}:{lookback|start_end}`
(`TTL_STABLE`, 300s), invalidated by the daily job on non-zero insert
(same glob-invalidation pattern as `recommendation_cleanup`).

`strategies[]` powers the always-visible strategy-comparison table.
`trades[]` powers the drill-down table and is only fetched/returned when
`strategy_id` is set (avoids returning a huge unscoped trade list for
"All").

## 8. Frontend UX

- **Filter bar**: Mode pills (Backtest / Walk-forward / Paper / Live,
  default Live) · Strategy dropdown (default All, scoped per §2) ·
  Lookback pills (7d / 30d / 90d / All, default 30d) + a "Custom range"
  pill revealing From/To native date inputs (mutually exclusive with the
  presets).
- **Strategy comparison table** (always shown): Trades, Win rate, Total
  PnL, Biggest win, Biggest loss, Profit factor, Max DD% (backtest/WF
  only, em-dash otherwise).
- **Trade drill-down table** (shown only once a single strategy is
  picked): full closed-trade list. Generalise `BacktestTradeTable.tsx`
  into a shared `TradeLogTable` component; `ColumnSelector` +
  `DownloadCsvButton` per the tabular-pages rule (≥8 columns).
- **Per-ticker breakdown** (same strategy, same already-fetched trade
  list, grouped client-side by ticker — no extra query): win rate & PnL
  per ticker. Directly supports "tune the universe" — surfaces which
  tickers are dragging a strategy down.
- Loading/empty/error states follow existing conventions
  (`performance-empty`, `performance-error` style testids already in the
  current tab).

## 9. Testing

- Backend: FIFO pairing correctness (multi-day trade spanning the job
  boundary, partial fills, unmatched open position skipped — not
  surfaced as a "closed" trade), idempotent upsert (job run twice → no
  duplicate rows), mode/lookback/custom-range filter tests, backtest/
  walk-forward path reusing existing `algo.runs` fixtures, backfill
  script idempotency.
- Frontend: filter-driven refetch, strategy-dropdown scoping per mode,
  custom-range date validation, empty states per mode, CSV export column
  parity, per-ticker breakdown grouping.

## 10. Rollout sequence

1. Migration: `algo.closed_trades` table.
2. Extract shared FIFO-pairing helper from `routes/attribution.py`
   (behaviour-preserving refactor — `attribution.py`'s existing
   single-day endpoint keeps working unchanged).
3. Backfill script (run once, full history).
4. Daily job `algo_closed_trades_rollup`, registered in `scheduled_jobs`
   (CLAUDE.md rule 25/28) at 16:30 IST Mon–Fri.
5. `GET /v1/algo/performance/summary` endpoint.
6. Frontend: filter bar, strategy comparison table, trade drill-down,
   per-ticker breakdown.

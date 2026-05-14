# Backtest correctness suite — MIS / CNC, period_end_mtm, MIS
square-off, entry cutoff, intraday timestamps

Hardening landed in PR #221 (2026-05-14, squash `f140fd6`) after
the operator surfaced four classes of silent misreporting on
backtest results. Each fix has a unit test in
`backend/algo/tests/`.

## Fix 1 — Period-end MTM force-close

**Symptom**: Total PnL on the cards showed +₹1,269 but the trade
table had a single closed trade at −₹548. Discrepancy = MTM gain
on an open position the trade table couldn't see.

**Cause**: Runner computed `final_equity = MTM(realised + unrealised)`,
but `total_trades` and `trade_list` only counted CLOSED positions.

**Fix**: After the main bar loop, synthetically close every
still-open position at the last bar's close. Stamp
`exit_reason='period_end_mtm'` on the resulting `TradeRow`.
After this, Σ realised − fees ≡ total_pnl exactly. Trade table
matches the cards.

**Tests**: `test_position_tracker_force_close.py` — 5 tests
covering profit/loss force-close, missing-mark skip, opened-after
fill_date guard, empty book no-op, date preservation.

## Fix 2 — MIS daily square-off honors `square_off_time`

**Symptom 1**: Daily MIS run held positions overnight in the
simulation (Zerodha would have force-squared at 15:15 IST).

**Symptom 2** (after fix-1 landed): MIS square-off was always
firing at the last bar of the day (15:15 IST on 15m cadence),
regardless of `strategy.square_off_time = "15:08 IST"`.

**Cause**: `day_end_keys` was computed as "last bar per trading
day" — ignored `square_off_time` entirely.

**Fix**: For each trading day, pick the FIRST bar whose IST
open >= `square_off_time`. Fallback to last bar if no candidate
(square_off configured after market close). Granularity-limited
by data: square_off=15:05 on 15m bars rounds to 15:15 (next
boundary); on 5m bars resolves at 15:05 exactly.

## Fix 3 — MIS entry cutoff ("no new BUYs after T−1h")

**Operator request**: Real intraday desks stop opening positions
~1 hour before square-off so positions have time to develop /
hit stop-loss. Apply uniformly across backtest, paper, dry-run,
and live runtimes.

**Design**: New AST field `entry_cutoff_time: str | None`.
Model validator stamps `square_off_time − 60min` default for
MIS strategies when None. Shared helper
`backend/algo/runtime/intraday_window.py::is_entry_allowed()`
imported by all four runtimes; SELL / exit signals stay
unaffected (closing a position is always OK).

**Tests**: `test_intraday_window.py` — parse_ist_time variants,
default_entry_cutoff math, is_entry_allowed MIS-only, is_past_square_off,
ist_time_from_ns.

## Fix 4 — Intraday Opened / Closed timestamps

**Symptom**: Trade table showed `Opened: 2025-11-18` /
`Closed: 2025-11-18` for an MIS scalp — too coarse to tell
whether the trade lasted 5 minutes or 5 hours.

**Fix**: Added `opened_at_ts_ns` / `closed_at_ts_ns` to
`Position` and `TradeRow`. `apply_fill` and `force_close_all`
stamp the bar's `bar_open_ts_ns`. Daily-cadence trades leave
both None.

Frontend `BacktestTradeTable.renderCell` switches to
`"YYYY-MM-DD HH:mm IST"` when `ts_ns` is present; falls back to
bare date for daily strategies.

## Fix 5 — Date-inverted rows on MIS backtests

**Symptom**: After the above fixes, a few MIS rows still showed
`opened_at > closed_at` (closed_at = previous day's date).

**Cause**: `_action_to_intent` was missing
`intent_emitted_ts_ns` on the `sell(all=True)` and both
`set_target_weight` legs. SimBroker fell back to its daily
path: next CALENDAR day's first bar instead of next 15m bar.
EXIT signals fired same-day correctly (had ts_ns), but the BUY
filled a day late, creating the inversion.

**Fix**: pass `intent_emitted_ts_ns=bar_open_ts_ns` on all four
order legs. Side benefits:
- Date-inversion artefacts disappear.
- Fees drop (orders correctly classified intraday vs delivery —
  capped ₹20/leg vs full delivery schedule).

## Fix 6 — Number formatting on trade table

**Symptom**: Prices rendered with full Decimal precision
(`1.024034658292076258714...`); columns bled across viewport.

**Fix**: `Intl.NumberFormat("en-IN")` with 2 decimals + Indian
thousands separators for ₹ columns; `.toFixed(2)` + `%` for
Return %. CSV download keeps raw precision.

## Walk-forward inherits all of the above

Each fold's child backtest goes through the same `run_backtest`
function, so:
- period_end_mtm rows appear in each fold (CNC strategies hold
  over fold boundaries)
- MIS square-off fires per trading day inside each fold
- intraday timestamps stamped on fold trade lists
- Number formatting on the per-fold UI

Verified end-to-end on "Live Test ₹3000 RSI" (CNC daily): 4
folds, every fold reconciles diff = 0 (Σrealised − fees =
total_pnl), each fold has at least 1 `period_end_mtm` row.

## Walk-forward additionals

- `regime_stratified` default flipped `True → False`. Indian
  markets are 90% SIDEWAYS; with BULL/BEAR rare the "every
  regime in each train slice" gate wipes all folds. Frontend
  exposes a checkbox to opt in when explicitly testing
  bull/bear strategies.
- Live fold progress indicator: GET /walkforward/runs/{id} returns
  `progress: {done, running, total_estimated, started_at}` computed
  live from `algo.runs`. Frontend banner shows "fold X of Y done · ETA ~N min"
  with progress bar; ETA = (elapsed / done) × (total − done) after
  ≥2 folds completed.

## Files

- `backend/algo/backtest/types.py` — added `exit_reason`,
  `opened_at_ts_ns`, `closed_at_ts_ns` on Position + TradeRow.
- `backend/algo/backtest/positions.py` — `force_close_all`
  with `fill_ts_ns` + opened-after-fill_date guard.
- `backend/algo/backtest/runner.py` — period_end + MIS day-end
  keys honoring `square_off_time`, entry cutoff gate, ts_ns on
  TradeRow, fix to `_action_to_intent` ts_ns propagation.
- `backend/algo/runtime/intraday_window.py` — shared helper.
- `backend/algo/strategy/ast.py` — `entry_cutoff_time` field +
  validator.
- `backend/algo/paper/runtime.py`, `backend/algo/live/runtime.py`
  — entry cutoff gate (import from intraday_window).
- `backend/algo/backtest/walkforward.py` — `regime_stratified` default,
  `WalkForwardProgress` shape.
- `backend/algo/routes/walkforward.py` — live progress snapshot.
- `frontend/components/algo-trading/BacktestSummaryCards.tsx` —
  period_end + MIS hint chips.
- `frontend/components/algo-trading/BacktestTradeTable.tsx` —
  exit_reason badge, intraday timestamps, 2-dp formatting.
- `frontend/components/algo-trading/builder/CadenceProductPanel.tsx`
  — entry_cutoff_time input (MIS only).
- `frontend/components/algo-trading/WalkForwardSubTab.tsx` —
  regime-stratified toggle + live progress banner.
- `frontend/hooks/useBacktestRuns.ts` — TradeRow ts_ns + exit_reason
  types.

## Cross-refs

- Shipped via PR #221 (squash f140fd6)
- CLAUDE.md §5.16 mentions strategy promotion (related)
- Strategy promotion workflow memory: `strategy-promotion-workflow`
- ASETPLTFRM-400 (Intraday Backtest Support) — Slices 5-7 +
  correctness fixes

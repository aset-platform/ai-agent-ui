# Regime-Aware Multi-Factor System — Slice REGIME-7: Sector Rotation + PIT Universe + Slippage

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session is about to start.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Ship four hardening pieces in lockstep:
1. `stocks.universe_snapshot` Iceberg table with monthly rebuild job (top 200 by 60d ADTV from NIFTY 500; ₹500cr mcap min, ₹10cr ADTV min, 252-day listing-age min).
2. Backtest runner reads point-in-time universe (PIT resolver asserts ≥ 50 rows, hard error if missing).
3. Slippage model upgrade from fixed bps to `max(5, 50 × order_value / ADTV) bps` in backtest sim_broker.
4. AST parser enforces 2007-01-01 backtest start floor (parser-time guard, not runtime).
5. Sector rotation strategy template (reference) using the factor library with regime overlay per research §8 (BULL → cyclicals, BEAR → defensives).

**Architecture:** Monthly job (1st Sunday 03:00 IST, via existing scheduler) rebuilds `stocks.universe_snapshot` partitioned by `rebalance_date`. Backtest runner queries `WHERE rebalance_date = (last <= bar_date)` via PIT resolver; hard-fails if <50 rows. Sim_broker applies order-value-scaled ADTV slippage on every execution (live runtime uses actual Kite fills). AST parser validates strategy JSON at load time — period_start < 2007-01-01 raises Pydantic ValidationError. Sector rotation template references the monthly rebalance pattern with regime-conditional sector selection.

**Tech Stack:** Python 3.12, PyIceberg, pandas, Pydantic v2, SQLAlchemy 2.0 async.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §3.7 (universe snapshot), §3.8 (slippage), §4.1 (table definition), §5.1 REGIME-7 (row 7), §6.1 REGIME-7 (test row 7).

**Research:** `docs/superpowers/research/2026-05-10-regime-aware-multifactor-research.md` — §8 (sector rotation NSE patterns), §9 (rolling universe survivorship 4.94pp/yr inflation fix), §10 (anti-pattern: backtest start floor 2007; slippage `max(5, 50 × order_value/ADTV) bps`).

**Branch:** `feature/algo-regime-slice-7-rotation-pit-slippage` off `feature/algo-regime-multifactor-integration`. Can run in parallel with REGIME-5, REGIME-6.

**Depends on:** REGIME-1 (regime feed), REGIME-2a (factor library + daily_factors Iceberg). Independent of REGIME-3, REGIME-4, REGIME-5, REGIME-6.

---

## File Structure

**Backend (new):**
- `backend/algo/universe/__init__.py` — PIT resolver exports.
- `backend/algo/universe/snapshot_job.py` — `rebuild_universe_snapshot(rebalance_date: date) -> None` (monthly, 03:00 IST via @register_job).
- `backend/algo/universe/pit_resolver.py` — `resolve_pit_universe(bar_date: date) -> list[str]` (tickers in top 200 snapshot as of bar_date's rebalance_date).
- `backend/algo/strategy/templates/sector_rotation.py` — reference JSON strategy + documentation.
- `backend/algo/tests/test_universe_snapshot_job.py` — job unit tests (filters, ordering, deduplication).
- `backend/algo/tests/test_pit_resolver.py` — resolver with synthetic snapshots; test exact bar_date, missing rebalance_date, before first snapshot.
- `backend/algo/tests/test_slippage_model.py` — slippage formula unit tests (min 5bps, order-value scaling, ADTV edge cases).
- `backend/algo/tests/test_backtest_start_floor_parser.py` — AST parser validation (accepts 2007-01-01, rejects 2006-12-31, edge case 1970).
- `backend/algo/tests/test_sector_rotation_template.py` — template loads JSON, compiles AST, runs on synthetic data.

**Backend (modified):**
- `stocks/create_tables.py` — add `stocks.universe_snapshot` Iceberg table creation (Alembic-style PyIceberg).
- `backend/algo/backtest/runner.py` — `run_backtest(...)` reads universe via `pit_resolver.resolve_pit_universe(bar_date)` instead of current registry; asserts `len(universe) >= 50`, raises `DataValidationError` otherwise (NO fallback).
- `backend/algo/backtest/sim_broker.py` — replace fixed-bps slippage with `estimate_slippage_bps(order_value_inr: Decimal, ticker_adtv_inr: Decimal) -> Decimal` (returns `max(5, 50 * order_value / adtv)` bps).
- `backend/algo/backtest/sim_broker.py` — on every trade, fetch ADTV from `stocks.daily_factors` or pre-load during run setup.
- `backend/algo/strategy/ast.py` — parser validates `period_start >= date(2007, 1, 1)` at load time; raises ValidationError if breached.
- `backend/algo/jobs/__init__.py` — register `regime-universe-snapshot` job (1st Sunday 03:00 IST).
- `backend/db/migrations/versions/2026_05_10_stocks_universe_snapshot.py` — empty migration (PyIceberg table creation in code, not SQL).

**Frontend (no new UI):** Uses existing Backtest tab; walk-forward report (REGIME-5) surfaces the PIT-universe-backed equity curves. No frontend changes needed.

**E2E:**
- `e2e/tests/backend/algo-universe-snapshot.spec.ts` — seed universe snapshot rows; backtest reads correct top-200 cohort for bar_date; assert equity curve differs from current-registry backtest.
- `e2e/tests/backend/algo-slippage-model.spec.ts` — backtest with known order sizes + ADTV; verify slippage applied per formula.

---

## High-level task list (12–14 items)

1. **Universe snapshot table creation** — PyIceberg `stocks.universe_snapshot` with columns (rebalance_date, ticker, adtv_inr_60d, market_cap_inr, sector, included_in_top_200); partition year(rebalance_date); schema in `create_tables.py`.

2. **Snapshot rebuild job** — `rebuild_universe_snapshot(rebalance_date)` logic:
   - Query current `stocks.stock_master` + OHLCV for NIFTY 500 constituents on rebalance_date.
   - Filter: market_cap ≥ ₹500cr, adtv_60d ≥ ₹10cr, listing_age_days ≥ 252.
   - Sort by adtv_60d DESC, take top 200.
   - Upsert (NaN-replaceable per CLAUDE.md §5.1) into `universe_snapshot WHERE rebalance_date = ...`.
   - Emit `universe_snapshot_rebuilt` event with ticker count.

3. **Monthly scheduler registration** — `@register_job("regime-universe-snapshot", every="month", day_of_month=1, hour=3, min=0)`.

4. **PIT resolver** — `resolve_pit_universe(bar_date: date) -> list[str]`:
   - Query `stocks.universe_snapshot WHERE rebalance_date <= bar_date ORDER BY rebalance_date DESC LIMIT 1`.
   - Assert result is non-empty AND row.rebalance_date ≤ bar_date (no future lookups).
   - Return ticker list where included_in_top_200 = True.
   - Test: before first snapshot date → empty; on exact rebalance_date → correct cohort.

5. **Backtest runner PIT integration** — `run_backtest(strategy, config, user)`:
   - Before execution, call `pit_resolver.resolve_pit_universe(config.period_start)`.
   - Assert `len(universe) >= 50` and `universe is not None`; raise `DataValidationError("No PIT universe for {period_start}")` if not.
   - During bar loop, use PIT universe instead of current registry for entry/exit filtering (existing v2 logic).
   - No fallback to current registry.

6. **Slippage model** — `estimate_slippage_bps(order_value_inr, ticker_adtv_inr)`:
   - Formula: `max(5, 50 * (order_value_inr / ticker_adtv_inr)) bps`.
   - Pre-load ADTV from `stocks.daily_factors` or cache on run setup (minimize per-bar lookups).
   - Call from `sim_broker.py::execute_trade()` before returning filled_price; apply bps to both BUY and SELL.
   - Live runtime: keep actual Kite fills (slippage is backtest-only).

7. **AST parser date floor** — `backend/algo/strategy/ast.py::StrategyConfig`:
   - Add validator: `period_start` must be ≥ `date(2007, 1, 1)`.
   - Raise Pydantic ValidationError if breached (message: "Backtest start floor is 2007-01-01 (mandatory to include 2008 bear market)").
   - Test: 2006-12-31 → error; 2007-01-01 → OK; 2007-01-02 → OK.

8. **Sector rotation template** — `backend/algo/strategy/templates/sector_rotation.py` (reference JSON + documentation):
   - Monthly rebalance trigger.
   - Rank sectors by 6-month return (via `rs_vs_sector_3m` factor + backward cumsum logic OR fresh computation).
   - Regime filter: if BULL, favor cyclicals (AUTO, BANK, METAL, REALTY); if BEAR, favor defensives (FMCG, PHARMA, IT).
   - Top 3 sectors → pick top N stocks from each by momentum + quality.
   - Equal weight or vol-target per position.
   - Include `applicable_regimes: ["bull", "sideways"]` (BEAR only in sideways context; full BEAR requires more defensive bias).
   - JSON filename: `sector_rotation_monthly.json`.

9. **Universe snapshot tests (unit)** — `test_universe_snapshot_job.py`:
   - `test_rebuild_filters_by_adtv` — excludes tickers with adtv < ₹10cr.
   - `test_rebuild_filters_by_mcap` — excludes tickers with mcap < ₹500cr.
   - `test_rebuild_filters_by_listing_age` — excludes delisted or <252-day listings.
   - `test_rebuild_sorts_by_adtv_desc` — top-200 by ADTV.
   - `test_rebuild_idempotent` — re-run on same date overwrites (NaN-replaceable dedup).

10. **PIT resolver tests (unit)** — `test_pit_resolver.py`:
    - `test_resolve_pit_on_exact_rebalance_date` — correct cohort.
    - `test_resolve_pit_before_first_snapshot` — returns empty list (or raises, per design choice).
    - `test_resolve_pit_between_rebalance_dates` — picks last rebalance_date ≤ bar_date.
    - `test_resolve_pit_asserts_min_universe_size` — ≥ 50 tickers (or 0 tickers → error).

11. **Slippage model tests (unit)** — `test_slippage_model.py`:
    - `test_slippage_min_5bps` — `estimate_slippage_bps(1000, 1e8)` → 5 (5bps minimum).
    - `test_slippage_scales_with_order_value` — order_value 2× → slippage ≥ 2× (at high order/ADTV).
    - `test_slippage_edge_case_zero_adtv` — raises ValueError or clamps to max (per design).
    - `test_slippage_applied_symmetrically` — BUY and SELL both apply.

12. **AST parser floor tests (unit)** — `test_backtest_start_floor_parser.py`:
    - `test_parse_period_start_2007_accepted` — `StrategyConfig(period_start=date(2007, 1, 1))` → OK.
    - `test_parse_period_start_2006_rejected` — `StrategyConfig(period_start=date(2006, 12, 31))` → ValidationError.
    - `test_parse_period_start_1970_rejected` — `StrategyConfig(period_start=date(1970, 1, 1))` → ValidationError.
    - `test_error_message_clarity` — error message mentions "2007-01-01" and "2008 bear".

13. **Sector rotation template test (integration)** — `test_sector_rotation_template.py`:
    - Load JSON template, compile AST.
    - Synthetic data: 3 sectors, 5 stocks each, monthly bars.
    - BULL regime → cyclical sector selected.
    - BEAR regime → defensive sector selected.
    - Run end-to-end backtest on PIT universe; no errors.

14. **E2E test: PIT-backed backtest vs current-registry backtest** — `e2e/tests/backend/algo-universe-snapshot.spec.ts`:
    - Seed universe snapshot rows for 2024-01-01, 2024-02-01 (different top-200 cohorts).
    - Run backtest on strategy for period 2024-01-01 → 2024-12-31.
    - Assert equity curve differs from (hypothetically) running on current registry.
    - Assert resolver correctly transitions between snapshot dates mid-backtest.

---

## Acceptance

- [ ] `stocks.universe_snapshot` Iceberg table created and partition strategy in place.
- [ ] `rebuild_universe_snapshot()` runs on 1st Sunday 03:00 IST; filters correct; upserts 200 tickers per month.
- [ ] `resolve_pit_universe(date)` returns ≥ 50 tickers or raises DataValidationError.
- [ ] Backtest runner asserts PIT universe non-empty on period_start; hard fails if missing (NO fallback to current registry).
- [ ] Slippage model applies `max(5, 50 * order_value / adtv) bps` to all trades; backtest equity curves differ from pre-slippage by 50–150 bps annualized.
- [ ] AST parser rejects period_start < 2007-01-01 at load time with clear error message.
- [ ] Sector rotation template JSON loads, compiles, runs on synthetic data; regime overlay filters sectors correctly.
- [ ] Walk-forward backtest (REGIME-5) uses PIT universe for all windows.
- [ ] All unit tests pass; E2E test verifies PIT universe selects correct cohort mid-backtest.
- [ ] No regression on existing backtests (non-PIT-using test strategies still work via fallback or dual-path logic).

---

## Out of scope for REGIME-7

- Per-sector regime overlay (v4).
- Alternative universe definitions (NIFTY 500 unmodified, thematic indices — deferred).
- Cross-asset universe (F&O, forex, commodities — out of scope for equity system).
- Sector rotation template as user-editable strategy (reference only; template editing is v4 UX).
- ADTV backfill beyond 90 days (sufficient for slippage model calibration).
- Live-mode auto-pause on regime change (v4; manual pause/resume per spec).

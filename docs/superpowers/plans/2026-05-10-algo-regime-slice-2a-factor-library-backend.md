# Regime-Aware Multi-Factor System — Slice REGIME-2a: Factor Library Backend

> **STATUS:** SKELETON — expand into a full TDD task-by-task plan via `superpowers:writing-plans` when this session is about to start.

> **For agentic workers:** REQUIRED SUB-SKILL: After expansion, use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.

**Goal:** Ship 7 factor modules (momentum, quality, lowvol, trend, volume, relative_strength, breadth) + nightly compute job + `stocks.daily_factors` Iceberg table + runtime integration (backtest, paper, live read cached values instead of per-bar recompute). Backend-only, no UI.

**Architecture:** Pre-computed nightly factor store in Iceberg; runtimes read cached values instead of per-bar O(N) recompute. One compute function per factor family; `compute_job.py` orchestrator batches 90+ days, writes to Iceberg with NaN-replaceable upsert (CLAUDE.md §5.1). All three runtimes (`backend/algo/backtest/runner.py`, `backend/algo/paper/runtime.py`, `backend/algo/live/runtime.py`) read `daily_factors` cache on runtime init instead of computing features per signal.

**Tech Stack:** Python 3.12 / FastAPI / PyIceberg / pandas / Pydantic v2.

**Spec:** `docs/superpowers/specs/2026-05-10-algo-regime-aware-multifactor-design.md` — §3.3 (factor library 7 modules), §4.1 (`stocks.daily_factors` Iceberg), §5.1 REGIME-2a row, §6.1 REGIME-2a test row.

**Branch:** `feature/algo-regime-slice-2a-factor-library-backend` off `feature/regime-multifactor-integration`.

**Depends on:** REGIME-1 merged (light dep — breadth metrics use regime context). No hard blocker.

**Estimated SP:** 13

---

## File Structure

**Backend (new):**
- `backend/algo/factors/__init__.py` — module marker.
- `backend/algo/factors/momentum.py` — compute `mom_12_1` (skip-month: excludes last 21 trading days per spec), `mom_6_1`, `mom_3_1`, `prox_52w`.
- `backend/algo/factors/quality.py` — compute `roic`, `accruals` (reads existing `stocks.fscore_summary.f_score`).
- `backend/algo/factors/lowvol.py` — compute `realized_vol_60d`, `beta_to_nifty`.
- `backend/algo/factors/trend.py` — compute `adx_14`, `sma200_slope`, `distance_from_sma200`.
- `backend/algo/factors/volume.py` — compute `obv`, `volume_x_avg_20`, `up_down_vol_ratio_20`.
- `backend/algo/factors/relative_strength.py` — compute `rs_vs_nifty_3m`, `rs_vs_nifty_6m`, `rs_vs_sector_3m`.
- `backend/algo/factors/breadth.py` — compute `pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio` (reads breadth data from regime context, if available).
- `backend/algo/factors/compute_job.py` — nightly orchestrator: iterates all tickers, calls each factor function, batches 90+ days, writes to Iceberg via NaN-replaceable upsert, invalidates `cache:factors:*` post-commit.
- `backend/algo/tests/test_factors_momentum.py` — unit test `mom_12_1` skip-month, `mom_6_1`, `prox_52w`; happy path + NaN input path.
- `backend/algo/tests/test_factors_quality.py` — `roic`, `accruals`; happy + NaN.
- `backend/algo/tests/test_factors_lowvol.py` — `realized_vol_60d`, `beta_to_nifty`; happy + NaN + corr matrix singular case.
- `backend/algo/tests/test_factors_trend.py` — `adx_14`, `sma200_slope`, `distance_from_sma200`; happy + NaN + pre-SMA period.
- `backend/algo/tests/test_factors_volume.py` — `obv`, `volume_x_avg_20`, `up_down_vol_ratio_20`; happy + NaN.
- `backend/algo/tests/test_factors_relative_strength.py` — `rs_vs_nifty_3m`, `rs_vs_nifty_6m`, `rs_vs_sector_3m`; happy + NaN + sector boundary.
- `backend/algo/tests/test_factors_breadth.py` — `pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio`; happy + missing sector data.
- `backend/algo/tests/test_factor_compute_job.py` — orchestrator integration: seed OHLCV, run job, verify Iceberg rows written, cache invalidated.
- `backend/algo/tests/test_runtime_reads_factors_cached.py` — backtest runtime reads `daily_factors` on init (not per-bar compute); paper + live same.
- `backend/algo/tests/test_factor_backfill_idempotent.py` — backfill script (see below) runs 2× over 30d window, row count unchanged.
- `backend/algo/tests/test_factor_keys_registered.py` — all 28 factor keys present in `strategy/features.py` FACTOR_KEYS registry.

**Backend (modified):**
- `stocks/create_tables.py` — add Iceberg table `stocks.daily_factors` (spec §4.1: ticker STRING, bar_date DATE, 28 factor columns DOUBLE, sector STRING; PK ticker+bar_date; partition year(bar_date); 14-month TTL via cleanup job).
- `backend/algo/strategy/features.py` — register all 28 factor keys + `regime_label`, `stress_prob` (REGIME-1 deps), `pct_above_50sma`, `pct_above_200sma`, `midcap_largecap_ratio` (breadth from REGIME-1) + `vix_close`, `vix_sma_20`.
- `backend/algo/backtest/runner.py` — `run_backtest()` reads `daily_factors` for the period (pre-load into dict indexed by ticker+date) at session init; feature evaluator looks up from cache, not recompute.
- `backend/algo/paper/runtime.py` — same as backtest: pre-load `daily_factors` for the next N trading days on runtime init.
- `backend/algo/live/runtime.py` — same: pre-load daily factors for today on market open.
- `backend/paths.py` — optional: add `FACTOR_CACHE_TTL` constant if not already present (used in invalidation map).
- `backend/main.py` — (existing job registration) register `compute_daily_factors` job via `@register_job()` decorator (nightly 23:00 IST, mon-sun, no catchup).
- `backend/cache.py` or `backend/redis_utils.py` — update `_CACHE_INVALIDATION_MAP` (per CLAUDE.md §5.13): `stocks.daily_factors` → `cache:factors:*`.

**Scripts (new):**
- `scripts/backfill_factors.py` — CLI to backfill 90 days of factor history post-deploy (idempotent: filter to rows not yet in Iceberg, append).

**E2E (optional for this slice, but recommended):**
- `e2e/tests/backend/factors.spec.ts` — seed OHLCV for 1 ticker + 90 days, run backfill, verify Iceberg + cache, backtest reads from cache (not recomputes), compare cached result to a hand-computed reference factor.

---

## High-level task list (12–14 items)

1. **Create `stocks.daily_factors` Iceberg table** — via Alembic-style PyIceberg schema + partition + TTL in `stocks/create_tables.py`.
2. **Momentum factor** — `backend/algo/factors/momentum.py` with skip-month convention enforced (21-day exclusion window documented in docstring).
3. **Quality factor** — `backend/algo/factors/quality.py` reading `stocks.fscore_summary.f_score`.
4. **Low-vol factor** — `backend/algo/factors/lowvol.py` (realized_vol_60d, beta_to_nifty).
5. **Trend factor** — `backend/algo/factors/trend.py` (ADX, SMA200_slope, distance).
6. **Volume factor** — `backend/algo/factors/volume.py` (OBV, volume averages).
7. **Relative strength factor** — `backend/algo/factors/relative_strength.py` (vs NIFTY + sector).
8. **Breadth factor** — `backend/algo/factors/breadth.py` (pct above SMAs, midcap/largecap ratio).
9. **Compute job orchestrator** — `backend/algo/factors/compute_job.py` batching + Iceberg write + cache invalidation.
10. **Register factors in features module** — update `strategy/features.py` FACTOR_KEYS with all 28 keys.
11. **Runtime integration** — backtest + paper + live read from `daily_factors` cache on init (not per-bar recompute).
12. **Backfill script** — `scripts/backfill_factors.py` 90-day cold-start.
13. **Unit tests** — per-factor happy + NaN paths; orchestrator integration; runtime caching; keys registry.
14. **Documentation** — inline docstrings for each factor; README section in `docs/algo-trading/factor-library.md`.

---

## Acceptance Checklist

- [ ] All 28 factor keys (momentum 4, quality 2, lowvol 2, trend 3, volume 3, relative_strength 3, breadth 3) compute without errors over a 180-day OHLCV window.
- [ ] Momentum `mom_12_1` visibly excludes the last 21 trading days (unit test verifies).
- [ ] `stocks.daily_factors` Iceberg table has 14-month TTL via automatic cleanup job (or explicit Iceberg expire_snapshots).
- [ ] `compute_job.py` runs nightly 23:00 IST, writes 90 days of data, invalidates `cache:factors:*` on commit.
- [ ] Backtest runtime pre-loads `daily_factors` at session init; feature evaluator reads from cache (zero per-bar recompute cost).
- [ ] Paper runtime same: pre-load next N trading days of factors at market open.
- [ ] Live runtime same: pre-load daily factors for today on market open.
- [ ] Backfill script runs 2×, idempotent (row count unchanged on second run).
- [ ] All 28 factor keys appear in `strategy/features.py` FACTOR_KEYS; CI test `test_factor_keys_registered` passes.
- [ ] NaN-replaceable upsert strategy (scoped pre-delete NaN rows, then append) is implemented per spec §4.1.
- [ ] No regression on existing backtest, paper, or live runs (single run without factors still works).
- [ ] E2E: seed factors, backtest reads from cache, performance matches hand-computed reference.

---

## Out of Scope for REGIME-2a

- Frontend Factor Scores tab (REGIME-2b).
- CI sync test extension for factor keys (deferred to REGIME-2b).
- Strategy AST extensions (regime_eq predicate, string-compare) — handled in REGIME-3.
- Volatility-targeted sizing (REGIME-4).
- Walk-forward CV extension + DSR/PBO (REGIME-5).
- Attribution stamping on signal events (REGIME-6).
- Universe snapshot + slippage model (REGIME-7).

---

## Key Constraints

1. **Skip-month convention on momentum is non-negotiable.** `mom_12_1` MUST exclude the most recent 21 trading days per spec §3.3. Enforce in unit test.
2. **Iceberg writes via NaN-replaceable upsert** per CLAUDE.md §5.1: scoped pre-delete NaN rows for incoming keys before append. Dedup query filters to non-NaN.
3. **Cache invalidation map update** per CLAUDE.md §5.13: `stocks.daily_factors` → `cache:factors:*` entered in `_CACHE_INVALIDATION_MAP`.
4. **90-day backfill on first deploy** via `scripts/backfill_factors.py`; idempotent via filtered append (not existing rows).

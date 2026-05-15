---
name: centralized-feature-engine
description: ASETPLTFRM-402 — feature store + per-fill snapshot dataset + research endpoints. All 14 slices shipped 2026-05-14 to 2026-05-15.
metadata:
  type: project
---

# Centralized Feature Engine (ASETPLTFRM-402)

Shipped end-to-end across 3 PRs on 2026-05-14 + 2026-05-15. **52 SP delivered + 2 hotfixes + ~280 new tests.**

## PR / commit lineage

- **PR #223** `a0bbcc5` (2026-05-14) — Phase 1 (24 SP) + Phase 2 (16 SP) + maintenance smart-skip hotfix
- **PR #224** `60bb485` (2026-05-15) — Phase 3 research tooling (12 SP) + `shap==0.51.0` + FE-3 bar_date fix
- **PR #225** `008665b` (2026-05-15) — Scoped iceberg_maintenance (ASETPLTFRM-418, 3 SP)
- **PR #226 (open)** — FE-5.1 Redis-buffered + per-run batched snapshot writes (ASETPLTFRM-417, 5 SP)

## Architecture (3 layers)

```
Raw bars  →  Feature Engine  →  Feature Store (Iceberg)  →  Strategy Engine
   ↓             (pure fns)            ↓                          ↓
intraday_bars   features/*.py      intraday_features          backtest/paper/live
index_intraday_bars                                            FE-5 snapshot hook
                                                                    ↓
                                                            trade_feature_snapshots
                                                            (research dataset)
                                                                    ↓
                                                            Phase 3 research APIs
                                                            (importance / SHAP /
                                                             coverage / labeling)
```

## New Iceberg tables

- **`stocks.intraday_features`** — 9 cols, long format (ticker, bar_open_ts_ns, bar_date, year_month, interval_sec, feature_name, feature_value, feature_set_version, written_at). Partition `(ticker, year_month)`.
- **`stocks.trade_feature_snapshots`** — 15 cols including fill_id, run_id, strategy_id, mode, features_json, realised_pnl_inr (nullable), outcome_label (nullable). Partition `(year_month, mode)`.
- **`stocks.index_intraday_bars`** — mirrors intraday_bars schema. Holds all 10 NSE indices (broad NIFTY 50 + NIFTY BANK + 8 sectoral). ASETPLTFRM-409 (FE-7) merged into this single table.

## Pure-function engine — `backend/algo/features/`

- `engine.py` — `compute_intraday_features` / `_for_universe`. 26 Phase-1 features + 4 FE-8 cohort + 3 FE-9 regime-link = **30 features**.
- `primitives.py` — 18+ pure primitives (EMA TA-Lib seeding, Wilder ATR, BB width pop-std, gap_pct, ORB, time-of-day bucket, etc.)
- `sector_map.py` — `stocks.company_info.sector` → Kite index tradingsymbol (parallel to factor library's Yahoo-named SECTOR_INDEX_MAP)
- `loader.py` — FE-4 partition-chunk Redis cache (strategy-agnostic, on-demand backfill on miss)
- `backfill.py` — sync-wrapper for async backfill_features_window
- `snapshots.py` — FE-5 fill-time hook. After PR #226 lands: 3-mode dispatcher (backtest/paper → in-process buffer; live → Redis LIST)
- `snapshots_buffer.py` (PR #226) — `SnapshotsBuffer` with RLock + `(strategy_id, run_id)` keying
- `importance.py` — FE-11 sklearn GBClassifier
- `shap_analysis.py` — FE-12 TreeExplainer (handles list/ndarray/3D output shapes across shap versions)
- `version.py` — `FEATURE_SET_VERSION = "v1.0"` constant, intraday SMA windows, NO_CROSS_SENTINEL

## Pipeline (Mon-Fri 15:45 IST)

5-step Intraday Bars Daily Pipeline DAG:

1. Fetch Intraday Bars (Nifty 500) — `intraday_bars_daily_ingest`
2. Fetch Intraday Bars (Indices) — `index_intraday_bars_daily_ingest` (FE-6)
3. Compute Intraday Features — `intraday_features_daily_compute` (FE-3)
4. Trim to 4-Year Retention — `intraday_bars_retention` (monthly gate)
5. Compact + Backup Iceberg — `iceberg_maintenance` (scoped — see `scoped-iceberg-maintenance`)

Plus standalone: `trade_outcome_backfill` (FE-13, on-demand or scheduled) and `trade_feature_snapshots_eod_flush` (PR #226, Mon-Fri 15:30 IST).

## Critical invariants preserved

- **Promotion gate (PR #221) bit-for-bit untouched** — scans `algo.events` only; the new `stocks.trade_feature_snapshots` table is separate. Verified by `test_promotion_gate_unchanged_by_snapshots` regression test.
- **Daily-strategy path unchanged** — `compute_indicators_for_universe` (daily) stays in `backend/algo/backtest/indicators.py`; only intraday compute was deleted in FE-4 hard cutover.
- **PR #221 fixes intact** — period_end_mtm, MIS square-off, entry cutoff, opened_at_ts_ns / closed_at_ts_ns.

## Known limitations + follow-ups

- **String features dropped at write boundary** — `time_of_day_bucket` + `regime_label` are computed in-memory but filtered out by `_panel_to_arrow_rows` because `feature_value` is `DoubleType`. Schema-evolution to add `feature_value_str: StringType` is deferred.
- **FE-3 bug discovered + fixed** — was `In("year_month", year_months)` (wiped current month on daily keeper [yesterday, today] window); fixed to `In("bar_date", bar_dates)` per-day scope. Mirror of intraday_bars / index_intraday_bars upsert pattern.
- **FE-8/FE-9 cohort features only emit from daily compute** (not FE-10 live emitter) — single-bar per-ticker can't assemble universe-wide inputs.

## Cross-refs

- `iceberg-maintenance-smart-skip-and-scoped` — sister memory for the maintenance perf workstream
- `fe5-snapshot-batching-redis-eod-design` — the 3-mode dispatch pattern in PR #226
- `subagent-parallel-dispatch-pattern` — how Phase 3 R1 (3 parallel slices) + R2 (sequential FE-12) shipped in ~20 min
- `pg-nullpool-sync-async-bridge` / `disposable-pg-session-asyncio-loop-bug` — all new scheduler jobs use `disposable_pg_session()`
- `iceberg-maintenance-enrollment` — every new write-heavy table must be in BOTH `_HOT_ICEBERG_TABLES` + `ALL_TABLES`

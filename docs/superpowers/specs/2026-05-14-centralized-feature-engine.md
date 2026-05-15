# Centralized Feature Engine + Store for Intraday Strategies

**Date:** 2026-05-14
**Epic (Jira):** ASETPLTFRM-403 (to be created)
**Predecessor:** ASETPLTFRM-400 (intraday backtest support — Slices 1-4b merged on `dev`)
**Status:** Spec — ready for next-session pickup

## 1. Goal

Move intraday feature computation OUT of the backtest runner's per-call in-memory pre-compute INTO a persisted, pipeline-fed **feature store**. Same engine feeds backtest, paper, live. Persisting features unlocks the research tooling phase (feature importance, SHAP, meta-labeling) and the alpha-research dataset (trade-time feature snapshots).

```
┌─────────────────────────────────────────────────────────────────┐
│ Raw Market Data                                                 │
│  ├─ stocks.ohlcv                  (daily, 4 yr × 500+ tickers)  │
│  ├─ stocks.intraday_bars          (15m, 4 yr × Nifty 500)       │
│  └─ stocks.index_intraday_bars    (15m, 4 yr × all indices) [NEW] │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Feature Engine — backend/algo/features/                         │
│   Pure functions, deterministic, no I/O.                        │
│   One function per feature family.                              │
│   Shared by daily compute job, on-demand backfill, live tick.   │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Feature Store — stocks.intraday_features (Iceberg)              │
│   Long format: (ticker, bar_open_ts_ns, feature_name, value)    │
│   Partitioned (ticker, year_month) like intraday_bars           │
│   Populated by:                                                 │
│    - Daily 16:00 IST keeper (after intraday_bars ingest)        │
│    - On-demand backfill (when backtest asks for missing window) │
│    - Live runtime (incremental per new bar)                     │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Strategy Engine — backend/algo/backtest, paper, live            │
│   Reads from intraday_features (no in-process compute).         │
│   On every fill, write the current feature snapshot to          │
│   stocks.trade_feature_snapshots → alpha research dataset.      │
└─────────────────────────────────────────────────────────────────┘
```

## 2. Why now (vs continuing in-memory)

| Cost of staying in-memory | Cost of moving to feature store |
|---|---|
| Every backtest re-computes the full feature panel from scratch | One nightly compute, all runs read |
| Backtest + paper + live have 3 separate (drifting) feature paths | Single source of truth |
| No way to do feature importance / SHAP — features don't exist outside a single run | Persisted dataset enables ML research |
| Adding a new feature requires touching `indicators.py` AND rebuilding every cached run | Add a row to the feature catalog, backfill once |
| Trade-time alpha research is impossible (no feature history at fill time) | Trivial: snapshot on every fill |
| 15-min vs 5-min vs 1-min strategies each recompute their own panel | One panel, strategies pick the cadence-matched slice |

The slice 4b in-memory compute was the right MVP for shipping Slice 1-4 of ASETPLTFRM-400 quickly. Moving to a feature store now unlocks the "research" phase the operator is explicitly planning for.

## 3. Storage schema

### 3.1 `stocks.intraday_features` (NEW)

**Format choice: long.** Operator's explicit ask (`Stores: symbol, timestamp, feature_name, feature_value`). Trade-off: ~3× the storage of wide format but schema-stable across the research phase (new features = new rows, not new columns).

```python
NestedField(1,  "ticker",            StringType(),    required=True)
NestedField(2,  "bar_open_ts_ns",    LongType(),      required=True)
NestedField(3,  "bar_date",          StringType(),    required=True)  # YYYY-MM-DD IST
NestedField(4,  "year_month",        StringType(),    required=True)  # partition key
NestedField(5,  "interval_sec",      LongType(),      required=True)
NestedField(6,  "feature_name",      StringType(),    required=True)
NestedField(7,  "feature_value",     DoubleType(),    required=True)
NestedField(8,  "feature_set_version", StringType(),  required=True)  # e.g. "v1.0"
NestedField(9,  "written_at",        TimestampType(), required=True)
```

**Partition spec:** `(ticker, year_month)` — matches `stocks.intraday_bars` for join-locality.

**Storage estimate:** Nifty 500 × 4 yr × 25 bars/day × 30 features ≈ 330 M rows. At ~30 bytes/row compressed (dict encoding on ticker + feature_name) ≈ **~10 GB on disk**. Acceptable given 11 GB headroom on the SSD.

**Maintenance enrollment:** Add to `_HOT_ICEBERG_TABLES` + `ALL_TABLES` in the same PR as the DDL (per CLAUDE.md §4.3 #20 — the `algo.events` 11 GB incident).

### 3.2 `stocks.trade_feature_snapshots` (NEW)

One row per fill (backtest / paper / live). The full feature vector at the fill bar, for downstream alpha research.

```python
NestedField(1,  "fill_id",           StringType(),    required=True)
NestedField(2,  "run_id",            StringType(),    required=True)
NestedField(3,  "strategy_id",       StringType(),    required=True)
NestedField(4,  "ticker",            StringType(),    required=True)
NestedField(5,  "side",              StringType(),    required=True)  # BUY|SELL
NestedField(6,  "qty",               LongType(),      required=True)
NestedField(7,  "fill_price",        DoubleType(),    required=True)
NestedField(8,  "fill_ts_ns",        LongType(),      required=True)
NestedField(9,  "bar_date",          StringType(),    required=True)
NestedField(10, "year_month",        StringType(),    required=True)
NestedField(11, "mode",              StringType(),    required=True)  # backtest|paper|live
NestedField(12, "features_json",     StringType(),    required=True)  # serialised feature vector
NestedField(13, "realised_pnl_inr",  DoubleType(),    required=False) # backfilled on close
NestedField(14, "outcome_label",     StringType(),    required=False) # backfilled by meta-labeller
NestedField(15, "written_at",        TimestampType(), required=True)
```

**Partition:** `(year_month, mode)`. The `mode` partition lets us isolate live-trading snapshots from backtest snapshots for research.

### 3.3 `stocks.index_intraday_bars` (NEW)

Same 12-field schema as `stocks.intraday_bars` (ticker, bar_date, interval_sec, bar_open_ts_ns, OHLCV, written_at, source, year_month). Partition `(ticker, year_month)` matches the equity bars layout for join-locality. Sourced from Kite's `historical_data`. Daily-keeper enrollment as step 2 of `Intraday Bars Daily Pipeline` (FE-6).

**All 10 indices live in this one table** (broad + sectoral). Consolidated decision 2026-05-15 — ASETPLTFRM-409 closed as merged into ASETPLTFRM-408. Rationale: identical schema, FE-8 cross-sectional consumers need both broad + sectoral in one scan, `WHERE ticker IN (...)` partition-prunes equally cheaply.

| Kite tradingsymbol | Role |
|---|---|
| `NIFTY 50` | Broad market — RS-vs-NIFTY baseline |
| `NIFTY BANK` | Broad sector — bank stocks |
| `NIFTY AUTO`, `NIFTY FMCG`, `NIFTY IT`, `NIFTY FIN SERVICE`, `NIFTY PHARMA`, `NIFTY METAL`, `NIFTY ENERGY`, `NIFTY REALTY` | Sectoral — sector rotation + sector RS |

**Ticker storage**: Kite tradingsymbol verbatim (e.g. `"NIFTY 50"`, NOT Yahoo's `^NSEI`). The factor library at `backend/algo/factors/compute_job.py::SECTOR_INDEX_MAP` keeps its Yahoo names untouched — this rename isolates to the index intraday surface only.

Mapping equity ticker → sector index uses the existing `stocks.company_info.sector` column (consumed by FE-8).

## 4. Feature catalog — Phase 1 (must-ship)

| Feature | Family | Formula | Importance |
|---|---|---|---|
| `vwap` | trend | Σ(typical × vol) / Σ(vol), reset daily | VERY HIGH |
| `dist_from_vwap_pct` | trend | (close − vwap) / vwap × 100 | VERY HIGH |
| `ema_20` | trend | exp moving avg, span 20 | HIGH |
| `ema_50` | trend | exp moving avg, span 50 | HIGH |
| `ema_20_slope_5bar` | trend | ema_20[i] − ema_20[i-5] | HIGH |
| `rsi_5` | momentum | Wilder RSI, window 5 | HIGH |
| `rsi_14` | momentum | Wilder RSI, window 14 (already exists) | HIGH |
| `roc_5` | momentum | (close[i] / close[i-5]) − 1 | MEDIUM |
| `atr_14` | volatility | Wilder ATR, window 14 | VERY HIGH |
| `range_expansion` | volatility | (high − low) / atr_14 | MEDIUM |
| `bb_width` | volatility | 2 × std(close, 20) / sma_20 | HIGH |
| `relative_volume` | volume | volume / avg(volume_same_time_of_day, last_20_days) | VERY HIGH |
| `volume_spike` | volume | volume > 2× rolling_avg(20) | MEDIUM |
| `gap_pct` | structure | (today_open − prev_day_close) / prev_day_close × 100 | HIGH |
| `orb_high_15min` | structure | max(high) of 09:15-09:30 bars | HIGH |
| `orb_low_15min` | structure | min(low) of 09:15-09:30 bars | HIGH |
| `dist_from_prev_day_high_pct` | structure | (close − prev_day_high) / prev_day_high × 100 | MEDIUM |
| `dist_from_prev_day_low_pct` | structure | (close − prev_day_low) / prev_day_low × 100 | MEDIUM |
| `minutes_since_open` | time | (bar_open − 09:15 IST) in minutes | LOW |
| `time_of_day_bucket` | time | "opening" / "midday" / "closing" / "lunch" | LOW |
| `rs_vs_nifty_15m` | relative | stock_return_15m − nifty_return_15m | VERY HIGH |
| `rs_vs_sector_15m` | relative | stock_return_15m − sector_index_return_15m | HIGH |

**Already exist (slice 4b):** `sma_20`, `sma_50`, `sma_100`, `sma_200`, `rsi`, `rsi_14`, `vwap`, `golden_cross_bars_ago`. Migrated into the feature store as-is; backfill applies the new schema.

**Total Phase 1 feature count: ~28** (including existing).

## 5. Feature catalog — Phase 2 (advanced)

| Feature | Notes |
|---|---|
| `market_breadth_pct_above_sma200` | % of Nifty 500 above their own SMA200 |
| `advance_decline_ratio` | advancers / decliners on the day |
| `sector_rotation_score` | % return rank of stock's sector vs all sectors |
| `realized_vol_annualised` | std(returns, 60d) × √252 |
| `regime_label` | link to `stocks.regime_history.regime_label` |
| `stress_prob` | link to `stocks.regime_history.stress_prob` |
| `volatility_compression_pct` | bb_width / max(bb_width, 60d) |
| `bollinger_z_score` | (close − sma_20) / std(close, 20) |

## 6. Feature catalog — Phase 3 (research)

These produce metadata about features, not features themselves:

- `compute_feature_importance(strategy_id, period)` — per-feature importance via sklearn `GradientBoostingClassifier` on the closed-trade outcomes
- `compute_shap_values(strategy_id, fill_ids)` — SHAP attribution for each trade's entry signal
- `meta_label_trades(strategy_id)` — label closed trades by outcome (winner / loser / breakeven), persist into `stocks.trade_feature_snapshots.outcome_label`
- Feature-coverage admin dashboard — `% of (ticker, ts_ns) pairs where each feature is non-null`

## 7. Migration plan

### 7.1 Backtest runner

After Phase 1 ships, `run_backtest` swaps:

```python
# Before (slice 4b — in-memory)
intraday_indicators = compute_indicators_for_universe_intraday(bars)
# ...
bar_feats = intraday_indicators[ticker].get(ts_ns)

# After (this epic)
feature_panel = load_intraday_features_window(
    tickers=universe, interval_sec=request.interval_sec,
    period_start=request.period_start, period_end=request.period_end,
)
# ...
bar_feats = feature_panel[ticker].get(ts_ns)
```

If the feature panel is sparse for the requested window, the runner calls `ensure_features_present(...)` (mirrors slice 1c's `ensure_window_present` for intraday_bars).

### 7.2 Live runtime

Live tick-stream resampler emits one feature row per closed bar into `stocks.intraday_features` so the live strategy reads the same source as backtest. Latency budget: < 100 ms per ticker per bar close.

### 7.3 No backwards-compat path — hard cutover

(Decision locked 2026-05-14.) The 4-yr × Nifty 500 historical
intraday data is already backfilled in `stocks.intraday_bars`,
so FE-3's one-time historical feature compute completes
before FE-4 ships. FE-4 then **hard-cuts** the intraday
backtest path to read from `stocks.intraday_features`. There
is no in-memory fallback.

When the feature panel is missing for any (ticker, window)
the runner fails fast with a structured error
(`feature-panel-missing: ticker=X interval=Y window=Z`). On-
demand backfill (FE-3) covers gaps; the runner refuses to
silently degrade.

`compute_indicators_for_universe_intraday()` from slice 4b
is deleted in FE-4. The pure-function building blocks it used
(`_vwap_intraday`, `_wilder_rsi`, `_rolling_sma`) move into the
new `backend/algo/features/` engine module in FE-2 and become
the canonical implementations called by both the daily compute
job (FE-3) and the live runtime emission path (FE-10).

### 7.4 Feature-panel loader — partition-chunk cache (Redis)

Loader signature:

```python
def load_intraday_features_window(
    tickers: list[str],
    interval_sec: int,
    period_start: date,
    period_end: date,
) -> dict[str, dict[int, dict[str, Decimal]]]:
    ...
```

Returns `{ticker: {bar_open_ts_ns: {feature: Decimal}}}` — the
same shape as today's `compute_indicators_for_universe_intraday`
output, so the runner's per-bar lookup is unchanged.

**Cache strategy: partition-chunk, strategy-agnostic.** Cache
unit matches the Iceberg partition `(ticker, year_month,
interval_sec)`, NOT the per-strategy panel. Multiple strategies
sharing the same universe re-use the same chunks; overlapping
periods reuse the months in overlap.

Key schema: `cache:feature:chunk:{ticker}:{year_month}:{interval_sec}`

Algorithm:

1. Decompose `(tickers, period_start, period_end)` into
   `(ticker, year_month)` partition chunks.
2. `MGET` all chunks from Redis. Deserialize hits.
3. For misses, single Iceberg scan: `ticker IN (...) AND
   year_month IN (...) AND interval_sec = ?`.
4. Group rows by `(ticker, year_month)`; write each chunk back
   to Redis with TTL = `TTL_STABLE` (300s).
5. Slice the assembled panel to `[period_start, period_end]`
   by filtering on `bar_open_ts_ns`.

Per CLAUDE.md §5.13: invalidation hook in `_retry_commit()`
calls `cache.invalidate("cache:feature:chunk:*:{ym}:*")` for
months touched by the write. New `_CACHE_INVALIDATION_MAP`
entry for `stocks.intraday_features`.

Estimated cache footprint (Nifty 500 × 48 months × 3 cadences):
~72 000 keys max, ~80 KB/chunk after msgpack+zstd ≈ 5.7 GB
working set. Redis LRU eviction keeps the actually-used subset
hot; cold months age out naturally.

### 7.5 Trade-snapshot writes do NOT touch `algo.events`

(Decision locked 2026-05-14.) The strategy promotion gate
(PR #221, `strategy-promotion-workflow` memory) scans
`algo.events WHERE mode='paper' AND type='order_filled' AND
ts_ns >= updated_at_ns` to decide paper→live eligibility.

FE-5's per-fill snapshot writes go to a **separate** Iceberg
table (`stocks.trade_feature_snapshots`). The existing
`order_filled` / `order_filled_live` emissions on `algo.events`
are unchanged. The promotion gate is bit-for-bit untouched.

Hook ordering inside `_apply_fill`:

1. Apply fill to `PositionTracker` (existing).
2. Emit `order_filled` event to `algo.events` (existing —
   promotion gate observable).
3. **NEW**: Write `stocks.trade_feature_snapshots` row.
   Wrapped in try / `log.exception` — snapshot failure NEVER
   blocks the fill or the event emission. Logged with
   `exc_info=True` per CLAUDE.md §4.2 #10.

## 8. Slice breakdown → Jira tickets

| # | Title | SP | Phase |
|---|---|---|---|
| 1 | `stocks.intraday_features` table + maintenance enrollment | 3 | 1 |
| 2 | Feature engine module — Phase 1 features (28 features) | 8 | 1 |
| 3 | Daily feature compute pipeline step + on-demand backfill | 5 | 1 |
| 4 | Backtest runner reads from feature store | 5 | 1 |
| 5 | `stocks.trade_feature_snapshots` + fill-time write hook | 3 | 1 |
| 6 | `stocks.index_intraday_bars` table + Kite backfill (all 10 NSE indices, broad + sectoral) | 5 | 2 |
| ~~7~~ | ~~stocks.sector_intraday_bars + sectoral backfill~~ — **merged into slice 6** (ASETPLTFRM-409 closed 2026-05-15) | ~~5~~ → 0 | 2 |
| 8 | Relative-strength + market-breadth features | 3 | 2 |
| 9 | Sector rotation + regime-link features | 3 | 2 |
| 10 | Live runtime incremental feature emission | 5 | 2 |
| 11 | Feature importance API (sklearn GBClassifier) | 3 | 3 |
| 12 | SHAP analysis endpoint | 3 | 3 |
| 13 | Meta-labeling helper + outcome backfill | 3 | 3 |
| 14 | Feature-coverage admin dashboard | 3 | 3 |
| | **Total** | **57** | |

**Suggested sprint shape:** Phase 1 (24 SP) as one bundled PR or 2 PRs. Phase 2 (21 SP) over 2-3 PRs. Phase 3 (12 SP) one PR.

## 9. Risks + open questions

1. **EMA vs SMA semantics on warmup-truncated series** — EMA's first value is undefined for the standard formula until you have at least `span` bars. We'll use "ema starts with simple mean of first `span` bars then exponential thereafter" to match TA-Lib convention; document explicitly.
2. **Long-format storage cost (~10 GB)** — if it grows past 30 GB, switch hot features (RSI, SMA, VWAP) to a wide-format sibling table; long-format becomes the experimental staging. *Update 2026-05-14: 4-yr intraday bars already backfilled; FE-3 one-time historical compute estimated under 8 GB → well inside SSD headroom.*
3. **Index data licensing** — Kite Connect provides ^NSEI bars; sectoral indices may need verification on Kite's allowed symbols. Backfill quota: ~10 indices × 5 windows = 50 API calls (negligible).
4. **Feature set version pinning** — every row stamps `feature_set_version`. Strategies declare which version they need; backtest fails fast if the requested version isn't in the store. Prevents silent semantic drift.
5. **Cross-sectional features (breadth, sector rotation)** — can't be computed per-ticker in isolation. Compute job needs an all-Nifty-500 cohort pass before per-ticker rows are emitted.

## 10. Not in scope (call-out)

- Tick-level features (sub-minute) — out of scope for v1.
- Fundamentals features (P/E, sector growth, etc.) — daily factor library already covers these via `stocks.daily_factors`.
- Options-flow / order-flow features — separate epic.
- Cross-asset features (crypto / commodities) — separate epic.

## 12. Daily parity gaps — running log

Maintained while FE-2 builds the engine. Each Phase-1 feature
that ALSO has value on the CNC daily surface but isn't in
today's daily indicator path (`compute_indicators_for_universe`,
`stocks.daily_factors`) gets a row here. End-of-Phase-1
deliverable: convert this list into ASETPLTFRM-417 (backlog
follow-up).

**Architecturally additive**: `stocks.intraday_features` already
carries `interval_sec` as a column, so daily features land as
rows with `interval_sec=86400`. Same store, no schema fork.

| Feature | Daily formula | Comment |
|---|---|---|
| `ema_20`, `ema_50` | exp moving avg of close, span N | Today only SMA exists; EMA preferred by trend strategies. |
| `ema_20_slope_5bar` | `ema_20[i] − ema_20[i−5]` (daily bars) | Trend acceleration. |
| `rsi_5` | Wilder RSI window 5 (daily) | Short-term momentum; complements existing `rsi_14`. |
| `roc_5` | `(close[i]/close[i−5]) − 1` | Rate of change. |
| `atr_14` | Wilder ATR window 14 (daily) | True-range based; different from existing `realized_vol_60d`; useful for ATR-based stop sizing. |
| `range_expansion` | `(high − low) / atr_14` | Breakout intensity. |
| `bb_width` | `2 × std(close, 20) / sma_20` | Volatility regime; complements `realized_vol_60d`. |
| `gap_pct` | `(today_open − prev_close) / prev_close × 100` | Pre-market reaction signal. |
| `dist_from_prev_day_high_pct`, `dist_from_prev_day_low_pct` | from prev-day H/L | Breakout proximity. |
| `volume_spike` | `volume > 2 × rolling_avg(20)` (daily) | Existing `volume_x_avg_20` factor is the underlying; this is the binary form. |

**Skipped (intraday-only by definition)**: `vwap` (daily VWAP
≈ typical price — weak signal), `dist_from_vwap_pct`,
`orb_high_15min`, `orb_low_15min`, `minutes_since_open`,
`time_of_day_bucket`, `rs_vs_nifty_15m`, `rs_vs_sector_15m`
(daily counterparts already exist as `rs_vs_nifty_3m/6m`,
`rs_vs_sector_3m` in the factor library).

**Estimated SP for ASETPLTFRM-417**: ~5 SP. Feature engine
module reuses Phase-1 implementations; new daily compute step
in the existing `Stock Pipeline → analytics` chain; no new
table or backtest reader path.

**Implementation confirmations (FE-2, 2026-05-14):**

| Feature | Transfers cleanly to daily? | Notes |
|---|---|---|
| `ema_20` / `ema_50` | Yes | Use ``backend.algo.features.primitives.ema(closes, span=N)`` directly on daily close series. SMA-seeded TA-Lib convention applies identically. |
| `ema_20_slope_5bar` | Yes | ``series_slope_n_bar(ema_20, 5)`` on daily series — slope is just bar-count difference, semantically identical. |
| `rsi_5` | Yes | ``wilder_rsi(closes, 5)`` on daily close series — same Wilder smoothing. |
| `roc_5` | Yes | ``roc_n_bar(closes, 5)`` on daily series — fraction not percent (consistent with intraday convention). |
| `atr_14` | Yes | ``wilder_atr(daily_bars, 14)`` — true range uses prior close, no intraday assumption. |
| `range_expansion` | Yes | `(high − low) / atr_14` per daily bar — daily H/L are well-defined. |
| `bb_width` | Yes | ``bollinger_band_width(closes, 20)`` on daily series — population std + SMA(20). Complements existing ``realized_vol_60d``. |
| `gap_pct` | Yes (adaptation) | On daily bars: `(today_open − prev_close) / prev_close × 100`. Uses ``bar.open`` and previous bar's ``close`` directly — no IST/timestamp gymnastics needed. The intraday helper's IST-date bucketing collapses to per-row ``bar.date`` on the daily path. |
| `dist_from_prev_day_high_pct` | Yes | Daily: distance from previous bar's high. Trivial. |
| `dist_from_prev_day_low_pct` | Yes | Daily: distance from previous bar's low. Trivial. |
| `volume_spike` | Yes | ``volume_spike_flag(daily_bars, window=20, multiplier=2)`` — binary, same threshold semantics. |

**Skipped (intraday-only) — formal record:**

- `vwap`: daily VWAP degenerates to a single typical-price per row — emit at `interval_sec=86400` only if a strategy explicitly asks; cost is one extra Decimal arithmetic per row.
- `dist_from_vwap_pct`: derived from daily VWAP — same caveat; can be enabled but adds no signal beyond typical-price proximity.
- `orb_high_15min` / `orb_low_15min`: intraday-only by definition.
- `minutes_since_open`: intraday-only by definition.
- `time_of_day_bucket`: intraday-only by definition.
- `relative_volume`: today's daily factor library already ships `volume_x_avg_20` covering the daily case. Keep intraday-only.
- `rs_vs_nifty_15m` / `rs_vs_sector_15m`: daily counterparts (`rs_vs_nifty_3m` / `rs_vs_nifty_6m` / `rs_vs_sector_3m`) already exist as factor-library features.

**Open question parked for ASETPLTFRM-417 design:**

- `bb_width` daily counterpart overlaps semantically with `realized_vol_60d` (annualised) — the daily compute step in ASETPLTFRM-417 should decide whether to ship both or alias. Lean toward **both** because BB-width window (20) is much shorter than the 60d realised-vol — different regime sensitivity.

## 13. Locked decisions (2026-05-14, post-spec amendments)

These were left as "open questions" in the original spec and
have been resolved before kickoff:

| # | Question | Resolution |
|---|---|---|
| 1 | Should FE-4 keep an in-memory fallback for sparse windows? | **No.** 4-yr backfill is already complete; hard cutover, fail-fast on missing panel. See §7.3. |
| 2 | Where do per-fill snapshots write — same `algo.events` stream or separate table? | **Separate** `stocks.trade_feature_snapshots`. Promotion gate's `algo.events` query untouched. See §7.5. |
| 3 | Is string-valued `time_of_day_bucket` safe through AST `Compare`? | **Yes.** Existing precedent: `regime_label` is a string feature consumed by `Compare`. No AST changes needed. |
| 4 | Walk-forward panel reload cost across folds? | **Partition-chunk cache** in Redis (`cache:feature:chunk:{ticker}:{year_month}:{interval_sec}`, TTL 300s). Strategy-agnostic — multiple strategies sharing a universe reuse chunks; overlapping windows reuse months. See §7.4. |

## 14. Acceptance criteria (epic-level)

- All 28 Phase-1 features persisted in `stocks.intraday_features` for 4 yr × Nifty 500.
- `run_backtest(interval_sec=900, ...)` reads from the feature store (verified by mocking the store empty → backtest fails fast with a clear error).
- A strategy referencing every Phase-1 feature key runs end-to-end without `feature-key-error`.
- Every fill in backtest/paper/live writes a `stocks.trade_feature_snapshots` row. ✓ FE-5 (table DDL + `backend.algo.features.snapshots.write_trade_feature_snapshot` + hooks in backtest runner, paper runtime, live runtime). Promotion gate's `algo.events` paper-fill scan is verified unchanged (snapshot test on the gate query — `test_promotion_gate_unchanged_by_snapshots.py`). ✓ FE-5.
- Phase 2 index data backfilled; RS-vs-NIFTY feature emits non-null for all Nifty 500 tickers.
- Phase 3 feature-importance API returns top-10 features for any closed strategy.
- **Non-regression**: existing CNC daily strategies (e.g. "Live Test ₹3000 RSI") produce identical 4-fold walk-forward results on `dev` vs FE-4. Verified by checksum of trade list + summary cards.
- **Daily parity gaps log** in §12 turned into ASETPLTFRM-417 at Phase-1 close.

---

**References:**
- ASETPLTFRM-400 (predecessor — intraday backtest support)
- ASETPLTFRM-386 (MIS strategy support)
- ASETPLTFRM-380 (pipeline-quality-assertions framework — reused for feature-coverage gates)
- CLAUDE.md §4.3 #20 (maintenance enrollment)
- Memory: `iceberg-maintenance-enrollment`, `iceberg-nan-replaceable-dedup`

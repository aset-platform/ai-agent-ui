# FE-15 — Daily-cadence feature parity + cross-cadence overlay

**Status:** Spec · 2026-05-15
**Epic:** ASETPLTFRM-402 (Centralized Feature Engine)
**Stories:** ASETPLTFRM-419 (FE-15a) · ASETPLTFRM-420 (FE-15b)
**Sprint:** Sprint 11 (2026-05-15 → 2026-05-20)
**Supersedes:** §12 "Daily parity gaps — running log" in
`docs/superpowers/specs/2026-05-14-centralized-feature-engine.md`
(the original spec referenced ASETPLTFRM-417 as the follow-up
ticket; that ID was repurposed for FE-5.1 Redis-buffered
snapshots and is now closed).

---

## 1. Goal

Land 18 daily-cadence technical-indicator features in
`stocks.intraday_features` at `interval_sec=86400`, then make
them accessible to AST strategies of any primary cadence via a
shared per-bar feature-assembly helper used by all three
runtimes (backtest, paper, live + dry-run).

Outcome: a single strategy can mix cadences in its AST, e.g.

```json
{"and": [
  {"compare": ["ema_50_1d", ">", "ema_200_1d"]},
  {"compare": ["rsi_14", "<", 30]}
]}
```

— daily golden cross combined with 15m RSI oversold on the
same evaluation tick.

## 2. Non-goals (call-out)

- **Schema fork** — `stocks.intraday_features` already carries
  `interval_sec` as a column; daily features land as rows with
  `interval_sec=86400`. No new table.
- **Rename existing intraday feature names** — `rsi_14`,
  `ema_20` etc. at `interval_sec=900` stay unchanged. Migration
  to a fully cadence-suffixed naming scheme is deferred to a
  hypothetical `feature_set_version=v2` if/when needed.
- **Cross-cadence reads from a daily strategy** — daily
  strategies see daily features unsuffixed and existing
  `daily_factors` overlay. They do NOT get an `_15m` suffix
  overlay (no use case; avoids look-ahead concerns).
- **New cadences (1h, 5m, 1m)** — architecture supports them
  via the same suffix rule, but compute jobs for those
  cadences are out of scope for this sprint.

## 3. Feature inventory (18 daily features at `interval_sec=86400`)

Reuses existing `backend/algo/features/primitives.*` impls —
all transfer cleanly from 15m to 1d cadence per §12
"Implementation confirmations" in the parent spec.

| Family | Keys | Formula source |
|---|---|---|
| Trend (EMA) | `ema_20`, `ema_50`, `ema_20_slope_5bar` | `primitives.ema(closes, span=N)`; slope = `series_slope_n_bar(ema_20, 5)` |
| Trend (SMA) | `sma_20`, `sma_50`, `sma_100`, `sma_200` | `primitives.sma(closes, window=N)` |
| Trend (cross) | `golden_cross_bars_ago` | Bars-since `sma_50 > sma_200` |
| Momentum | `rsi_5`, `rsi_14`, `roc_5` | `wilder_rsi`, `roc_n_bar` |
| Volatility | `atr_14`, `range_expansion`, `bb_width` | `wilder_atr`, `(H−L)/atr_14`, `bollinger_band_width(closes, 20)` |
| Price-action | `gap_pct`, `dist_from_prev_day_high_pct`, `dist_from_prev_day_low_pct` | `(today_open − prev_close)/prev_close × 100`; trivial dist-from-prev-H/L |
| Volume | `volume_spike` (binary) | `volume > 2 × rolling_avg(20)` |

**Skipped (intraday-only by definition):** `vwap`,
`dist_from_vwap_pct`, `orb_high_15min`, `orb_low_15min`,
`minutes_since_open`, `time_of_day_bucket`, `relative_volume`,
`rs_vs_nifty_15m`, `rs_vs_sector_15m`. Daily counterparts of
the RS features already exist in `daily_factors`
(`rs_vs_nifty_3m`, `rs_vs_nifty_6m`, `rs_vs_sector_3m`).

## 4. Collision analysis

Three feature surfaces; cross-product collision count:

| | `daily_factors` (19) | `intraday_features`@900 (33) | FE-15 @ 86400 (18) |
|---|---|---|---|
| `daily_factors` | — | 0 | 0 |
| `intraday`@900 | 0 | — | **18** (every FE-15 key) |
| FE-15 @ 86400 | 0 | 18 | — |

All 18 FE-15 features collide with intraday counterparts by
name because they share formulas at different cadences. None
collide with the factor library.

## 5. Naming policy — suffix at overlay time, not store time

**Decision:** the Iceberg store stays cadence-agnostic
(`feature_name` column carries the bare name; `interval_sec`
column is the discriminator). At AST evaluation time the
shared per-bar helper injects cross-cadence features into the
features dict under suffixed keys.

### Rules

1. **Primary cadence stays unsuffixed.** If
   `strategy.schedule.interval = "15m"` (`interval_sec=900`),
   `bar_feats[rsi_14]` is the 15m value.
2. **Daily overlay** (when primary < 86400) is injected as
   `{name}_1d`. The 15m strategy sees `rsi_14` (15m) AND
   `rsi_14_1d` (daily).
3. **`daily_factors` overlay is never suffixed** — factor-library
   keys (`mom_12_1`, `f_score`, etc.) are unique by design.
4. **Daily strategies (`interval=1d`)** see all 18 FE-15
   features unsuffixed (primary cadence). No `_15m` overlay
   into daily strategies — out of scope.
5. **Future cadences (1h, 5m, 1m)** — same rule extends:
   overlay from a *higher* cadence (longer bars) onto a lower
   cadence (shorter bars) gets a cadence suffix. Within-cadence
   reads are always unsuffixed.

### Why store stays cadence-agnostic

- No migration on the existing 47.8M rows backfilled this
  sprint.
- No breaking change to existing strategies / tests.
- The `interval_sec` column is already the canonical
  discriminator — duplicating it into `feature_name` would be
  redundant.
- A future `feature_set_version=v2` can revisit this if
  cadence-in-name becomes desirable.

## 6. Store design

`stocks.intraday_features` is reused (no DDL change). Daily
features land as rows:

```
ticker             "RELIANCE.NS"
bar_open_ts_ns     <UTC midnight ns for bar_date>
bar_date           "2026-05-15"
year_month         "2026-05"
interval_sec       86400
feature_name       "ema_50"        ← unsuffixed in the store
feature_value      2872.31
feature_set_version "v1.0"
written_at         <UTC ts, tz-naive>
```

Partition spec stays `(ticker, year_month)` — same as the
intraday rows. Maintenance enrollment is already in place
(`stocks.intraday_features` is in both `_HOT_ICEBERG_TABLES`
and `ALL_TABLES` per CLAUDE.md §4.3 #20).

## 7. Compute job — FE-15a (ASETPLTFRM-419)

### New module
`backend/algo/jobs/daily_features_daily_compute.py` — modelled
on `intraday_features_daily_compute.py` (which we just
patched with batched ticker reads in this sprint).

### Key differences vs the intraday job
- **Bar source:** `stocks.ohlcv` (daily OHLCV, 4yr × 500+
  tickers) instead of `stocks.intraday_bars`.
- **Cadence:** `interval_sec=86400` hardcoded.
- **`bar_open_ts_ns`:** UTC midnight of `bar_date` (deterministic
  ts so primary-key dedup works).
- **Cross-sectional features:** none in this v1 (RS-vs-Nifty
  daily already in `daily_factors`; cohort pass not needed).
- **Engine call:** wraps existing
  `compute_intraday_features` with `interval_sec=86400` — the
  engine already routes by cadence per the parent spec.

### Bar window
Default `[period_end - 30 days, period_end]` (rolling) for
the daily scheduled job. Warmup of 30 trading days is the
minimum to compute `sma_200` and `golden_cross_bars_ago`
reliably. The backfill helper accepts an explicit window for
historical backfills.

### Scheduling
- Cron: `mon-fri 23:30 IST` (after `compute_daily_factors` at
  23:00). One run per IST trading day.
- Registered via `@register_job("daily_features_daily_compute")`
  in `backend/jobs/executor.py`.
- Pipeline position: independent (not chained into the Intraday
  Bars Daily Pipeline — that pipeline runs at 15:45 IST for
  intraday data).

### Idempotency
NaN-replaceable upsert with scoped pre-delete on
`(ticker, bar_date, interval_sec=86400)`. Re-running a window
overwrites cleanly. Same pattern as the intraday job's
`_write_features_batch`.

### Performance projection (based on FE-15 6-month probe data)
- ~497 tickers × 30-day window × 18 features ≈ **268K rows
  per run**.
- 10 batches × ~3 sec each (DuckDB metadata + per-batch compute,
  daily window is tiny vs intraday) ≈ **~30 sec wall clock**.
- Disk impact: ~2 MB / run; ~250 MB for the 180-day backfill
  catching up to the existing intraday coverage.

## 8. Per-bar helper + cross-cadence overlay — FE-15b (ASETPLTFRM-420)

### New module
`backend/algo/features/per_bar.py`

```python
def assemble_per_bar_features(
    *,
    ticker: str,
    bar_date: date,
    ts_ns: int | None,
    primary_features: dict[str, Decimal | str],
    daily_factors_row: dict[str, Decimal] | None,
    market_regime: Decimal,
    market_trend: Decimal,
    regime_row: dict[str, Any] | None,
    daily_overlay: dict[str, Decimal | str] | None = None,
    hourly_overlay: dict[str, Decimal | str] | None = None,
    fifteen_min_overlay: dict[str, Decimal | str] | None = None,
) -> dict[str, Decimal | str]:
    """Single source of truth for the AST's per-bar features
    dict. Used by backtest runner, paper runtime, and live
    runtime (incl. dry-run) so signal-generation behaviour is
    byte-identical across all three.
    """
```

### Overlay assembly order

1. Start with `primary_features` (unsuffixed — the strategy's
   primary cadence).
2. Overlay `daily_factors_row` (unsuffixed; factor-library
   keys are unique).
3. Inject `nifty_above_sma200 = market_regime`,
   `nifty_30d_return_pct = market_trend`.
4. Overlay `regime_row` (`regime_label`, `stress_prob`).
5. If `daily_overlay`: inject as `{name}_1d`.
6. If `hourly_overlay`: inject as `{name}_1h`.
7. If `fifteen_min_overlay`: inject as `{name}_15m`.

Collision policy: lower-step overlays do NOT clobber higher
ones — primary cadence wins. Cadence-suffixed keys cannot
collide with anything by construction.

### Loader changes
Each runtime, on run start, calls
`load_intraday_features_window(interval_sec=86400, ...)` for the
universe and run window. Result lookup keyed by
`(ticker, bar_date)`. The Redis partition-chunk cache from FE-4
serves the same role for the new 86400 cadence (key:
`cache:feature:chunk:{ticker}:{year_month}:86400`).

### Runtime wiring

#### Backtest (`backend/algo/backtest/runner.py:423-438`)
Replace inline dict-merge with `assemble_per_bar_features(...)`.
Daily overlay loaded once at run start (intraday strategies
only); cached per-bar lookup by `(ticker, bar_date)`.

#### Paper (`backend/algo/paper/runtime.py:377`)
Same replacement. Paper sessions are time-bounded by market
hours so the daily overlay loads the trailing 30-day window
once at session start.

#### Live + dry-run (`backend/algo/live/runtime.py:1013`)
Same replacement. The daily overlay loads once per trading
day at session start (or first signal); cached for the day. On
dry-run (`kite.dry_run=True`) the same code path runs — the
only difference is at order submission, not signal generation.

### Non-regression contract

For any strategy that **does not** reference cadence-suffixed
keys (i.e. every existing strategy on `dev`), the per-bar
features dict before/after FE-15b is **byte-identical**.
Verified by checksum test on at least one strategy per cadence
(daily + 15m).

## 9. AST strategy examples (post FE-15)

### Cross-cadence golden cross + RSI
```json
{"and": [
  {"compare": ["ema_50_1d", ">", "ema_200_1d"]},
  {"compare": ["rsi_14", "<", 30]}
]}
```

### Daily breakout strategy (interval=1d)
```json
{"and": [
  {"compare": ["dist_from_prev_day_high_pct", ">", -0.5]},
  {"compare": ["volume_spike", "==", 1]},
  {"compare": ["range_expansion", ">", 1.5]}
]}
```

### Regime-gated 15m mean reversion with daily quality filter
```json
{"and": [
  {"compare": ["regime_label", "==", "BULL"]},
  {"compare": ["f_score", ">=", 7]},
  {"compare": ["rsi_5", "<", 25]},
  {"compare": ["gap_pct_1d", ">", 0.3]}
]}
```

## 10. Test plan

### Unit (FE-15b)
- `assemble_per_bar_features` happy path
- Cadence-suffix overlay (daily → _1d)
- Collision: primary cadence wins over overlay for same key
- Empty overlays handled (no `_1d` keys appear)
- Multi-overlay (daily + future hourly) suffix matrix

### Integration (FE-15a)
- Compute job happy path (1 ticker, 30-day window)
- Batched read predicate (CLAUDE.md §4.1 #1)
- Scoped pre-delete on `(ticker, bar_date, interval_sec=86400)`
- NaN filtering before write
- Idempotent re-run (no row growth)

### Cross-runtime consistency
- One strategy + one synthetic bar produces byte-identical
  `ticker_features` dict from all three runtimes.
- Dry-run (`KiteClient(dry_run=True)`) matches live.

### End-to-end
- 15m strategy referencing `ema_50_1d` and `rsi_14`:
  backtest run produces non-zero trade count, no
  `feature-key-error` log entries.
- Daily strategy referencing the 18 FE-15 features: backtest
  run completes, every feature has at-least-one non-null bar.

## 11. Acceptance criteria (epic-level for both stories)

- 180-day daily-feature backfill at `interval_sec=86400`
  produces ~1.1M rows (~497 tickers × ~122 trading days × 18
  features); zero failures.
- All three runtimes resolve cross-cadence keys (e.g.
  `ema_50_1d`) for an intraday strategy.
- Non-regression: at least one daily strategy and one 15m
  strategy on `dev` produce identical results before/after the
  refactor (checksum on trade list + summary cards).
- Spec, this doc, and Jira tickets reconciled — no stale
  ASETPLTFRM-417 references remain in the parent spec.

## 12. Rollout

1. Merge PR (FE-15a + FE-15b together) to `dev`.
2. Run `daily_features_daily_compute` backfill for
   `2025-11-17 → 2026-05-15` (matches the existing intraday
   coverage).
3. Smoke test: an existing 15m strategy backtest produces
   identical results pre/post (non-regression).
4. Demo strategy: file a "golden-cross + 15m oversold" sample
   strategy in `templates.ts`, run a backtest, paper, and dry-run
   in sequence; confirm consistent signal generation.

## 13. Cross-refs

- Parent spec: `2026-05-14-centralized-feature-engine.md` §12
- CLAUDE.md §4.1 #1 (batch reads), §4.3 #20 (maintenance
  enrollment), §5.1 (Iceberg vs PG patterns)
- Memory: `centralized-feature-engine`,
  `iceberg-nan-replaceable-dedup`,
  `iceberg-maintenance-enrollment`,
  `fe5-snapshot-batching-redis-eod-design`
- Related session memory: today's batched ticker-read patch
  (32-min → 60-sec speedup on the intraday compute job —
  same pattern reused in FE-15a).

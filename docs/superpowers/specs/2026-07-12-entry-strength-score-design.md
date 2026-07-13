# Entry Strength Score (ESS) — Design

**Date:** 2026-07-12
**Scope:** Analysis → Watchlist Stocks page (embedded, not a new page)
**Status:** Draft — awaiting review

## 1. Problem

The Watchlist Stocks page already answers *"is this a stock worth owning?"* via the
existing 5-factor **Quality Score (QM Score)** — Sharpe(6M), Blended RS, Max
Drawdown(6M), ATR% closeness, SMA200-distance closeness. It does not answer
*"is today a good day to buy it?"*.

In practice this shows up as: RSI(2) drops below 5 on a stock that clears all the
existing filters (golden cross, SMA200/SMA50 distance, RSI(2)), a position is opened,
and the stock keeps falling for another 5-6 sessions until the stop-loss is hit — a
falling-knife entry. RSI(2) measures *how oversold* something is; it says nothing
about *whether the oversold condition is resolving*. None of the existing filters
distinguish "this is the same kind of dip this stock has always bounced from" from
"this decline has changed character."

## 2. First principle: Quality and Entry timing are orthogonal

- **Quality Score** — is this a stock worth owning, over months. **Unchanged by this
  work.**
- **Entry Strength Score (ESS)** — is *today's specific pullback* healthy or a
  breakdown-in-progress. New.

These must never be blended into one number. Mixing them makes it impossible to
later distinguish "we picked the wrong stock" from "we entered at the wrong time."

## 3. Scope for this iteration (explicit non-goals)

- **No new page/tab.** ESS is embedded as additional columns on the existing
  Watchlist Stocks table (`analytics/analysis/page.tsx`), following its established
  filter/column/tooltip conventions.
- **Not wired into the live/paper/backtest runtimes or the AST.** ESS is a screening
  aid only. It feeds the *existing* manual workflow: user filters by QM Score / ESS
  on this page, curates a ticker list, pushes it into a strategy's `allowed_tickers`
  via the existing `AddToStrategyModal`. The strategy engine is untouched.
  Formalizing QM Score / ESS as real AST features (usable in entry/exit/risk
  conditions across backtest/paper/live) is a **future phase**, gated on this
  screening phase actually validating that the score predicts outcomes.
- **Sector Strength is explicitly dropped.** Evaluated and rejected — needs a new
  sector-index data dependency or peer-aggregation logic, and the incremental value
  doesn't justify the engineering cost right now.
- **Hard gates don't block anything yet.** Since nothing here touches live capital,
  a "gated" row is *flagged*, not hidden or blocked — the user can still see why a
  stock was rejected and judge in hindsight whether the gate call was right. Rows
  failing a hard gate stay visible with a badge; the continuous score is still
  computed and shown for them.

## 4. Architecture

```
Nifty 500
   │
   ▼
Quality Score (QM Score, 0-100)          ← unchanged, existing 5-factor blend
   │
   ▼
Entry Strength Score (ESS)               ← NEW, this design
   ├── Hard Gates (reject/flag, no score)
   └── Continuous Score (0-100)
   │
   ▼
RSI(2) trigger                            ← unchanged
   │
   ▼
Manual curation → allowed_tickers → strategy (existing AddToStrategyModal flow)
```

Future (not this iteration): QM Score + ESS become AST-native features usable
directly in entry/exit/risk conditions, once the allowed_tickers validation loop
shows they predict outcomes.

## 5. Hard Gates

Evaluated per ticker; a failing gate flags the row (doesn't hide it):

1. **Price < SMA200 → reject.** Reuses the existing `dist_sma200` field already on
   the page.
2. **Distance below SMA50 > 10% → reject.** (Resolves a contradiction from an
   earlier working note where the gate was pencilled at -6% while the continuous
   curve's "worst" anchor was -10% — the gate is set to -10% so the continuous
   curve has the full range to discriminate; anything worse than -10% is simply
   too extended to call a "pullback." **Flag for review if -6% was actually
   intended.**)

Two gates are **market-wide, not per-ticker**, and are surfaced as **page-level
banners**, not per-row flags (repeating an identical value on every row would be
noise):

1. **Nifty < SMA200 → banner: "Market regime unfavorable for new longs."** Reuses
   the existing `compute_market_regime()` function
   (`backend/algo/backtest/indicators.py`). (Note: a mid-trade version of a regime
   check was already tried for this exact RSI(2) strategy and shown to hurt
   performance — see project memory on the v4 mid-trade-regime result. That
   finding is about a *mid-trade exit* check, not a *new-entry* gate, so it
   doesn't disqualify this, but this entry-side gate shouldn't be assumed to help
   without the same walk-forward scrutiny, once/if it ever becomes more than a
   display banner.)
2. **Nifty 5-day ROC worse than -6% → second banner: "Broad market falling
   fast — index itself down >6% over 5 sessions."** Mirrors the per-stock ROC5
   factor (§6.5) but applied to the index — a market-wide falling-knife check,
   distinct from the SMA200-regime banner above (a Nifty that's above its SMA200
   but dropping sharply over the last week is a different risk than a Nifty
   that's been below SMA200 for a while).

Both banners are *display flags only* — they don't filter or hide any row.

## 6. Continuous ESS factors

Six factors, blended into a single 0-100 score. Two ideas from the brainstorm were
explicitly merged/relocated rather than kept as independent lines (see §6.7):

### 6.1 Selling Absorption × Relative Volume (fused, weight 30%)

Answers: *did buyers defend today, and does the volume confirm it?*

- **Selling Absorption** (per-day): `60% × CLV + 40% × Lower-Wick Ratio`, where
  `CLV = (close − low) / (high − low)` and
  `Lower-Wick Ratio = (min(open, close) − low) / (high − low)`. Pure OHLC, no new
  data.
- **Relative Volume**: `today's volume / 20-day average volume`.
- These are combined via a 2-D control-point grid (same style as the existing
  ATR%/SMA200 closeness curves, just two-dimensional), not summed independently —
  a linear sum would score "high volume" the same regardless of whether the close
  showed absorption or not, which loses exactly the signal that makes volume
  useful here:
  - High volume + strong absorption → high score (capitulation that got bought)
  - High volume + weak absorption → low score (distribution, no buyers — the
    falling-knife signature)
  - Low volume (< ~0.8x) + any absorption → mid score (ordinary quiet pullback)
  - Exact control points are a starting hypothesis to be tuned once real outcome
    data exists — not fixed forever.

### 6.2 SMA50 Proximity (weight 20%)

Bell curve, not binary. Illustrative anchor points (linear interpolation between,
clamped [0,100], mirroring the existing ATR%/SMA200 closeness-curve pattern):
`(0%, 60) → (-2%, 95) → (-3%, 100) → (-5%, 90) → (-7%, 70) → (-10%, 40)`, extrapolated
toward 0 beyond -10% (still computed/shown for gated rows, per §3).

### 6.3 Trend Stability (weight 15%)

SMA50 slope over a ~10-20 day lookback (e.g. `SMA50 today − SMA50 10 days ago`, or
a short linear regression). Distinguishes "pulling back inside an intact uptrend"
from "the medium-term trend itself is rolling over" — a case RSI(2) alone can't
tell apart. This is the one factor closest to Quality Score's territory; it's kept
in ESS because it's evaluated on a much shorter window (10-20 days) than Quality's
6-month Sharpe/RS, and answers a narrower question (is *this* dip still inside a
trend) rather than "has this stock trended well historically."

### 6.4 Selling Deceleration (weight 15%)

Is the day-over-day rate of decline shrinking (e.g. `-4% → -3% → -1% → -0.3%`)
rather than constant/accelerating? Computed from the trailing sequence of daily
returns during the current down-move. Deliberately paired with, but not redundant
with, Selling Absorption — Absorption is a single-day read, Deceleration is the
multi-day trajectory (same relationship as RSI(2) vs RSI(5): same underlying idea,
different timescale).

### 6.5 ROC5 (weight 12%)

5-day rate of change: `(close_today − close_5d_ago) / close_5d_ago × 100`. Answers
"how much has already moved," distinguishing a contained dip (~-4%) from a knife
that's fallen too far too fast (~-18%) even when RSI(2) reads the same in both
cases.

### 6.6 ATR Expansion (weight 8%)

Ratio of today's ATR(14) to ATR(14) from N days ago (e.g. 5 or 10 days back).
Volatility expansion is often the earliest fingerprint that a decline has changed
character from an ordinary pullback to a breakdown.

### 6.7 Market Breadth — context, not a blended factor

Nifty's daily return (already fetched once per page-load for the existing `rs_6m`
calc — free). This is **market-wide**, so baking it into every ticker's ESS as a
weighted addend would shift every row by the same amount without changing relative
ranking on a given day — not useful for picking between tickers *today*. Instead:
surfaced as page-level context (e.g. a small "Nifty today: -2.1%" indicator near
the regime banner from §5) and **persisted as its own column** so it can be used
later to slice the validation analysis (e.g. "were low-ESS days mostly broad
market sell-offs, or stock-specific?").

**Weights above are an initial hypothesis based on the relative confidence
expressed for each factor, not a tuned result** — refining them is exactly what the
`allowed_tickers` validation loop (§9) is for.

## 7. Frontend: embedding into Watchlist Stocks

- New columns on the existing table: `ess_score`, plus a gate-status indicator
  (badge/icon when a hard gate failed, with the reason).
- Filter chips follow the same multi-select/bucket pattern already used for QM
  Score (e.g. Reject / Avoid / Tradable / Preferred / High priority / Elite
  buckets) and Dist-SMA200.
- **Every ESS-related column header gets a `ColumnTooltip`** (the existing
  portal-based hover component already used on Sharpe(6M), RS(6M), ATR%, Dist
  SMA200, and Score) that shows:
  - The plain-English question the factor answers (e.g. "Did buyers defend
    today?")
  - The exact sub-fields and formula used, e.g. for the fused factor: `Selling
    Absorption = 60% × CLV + 40% × Lower Wick`, `CLV = (close − low)/(high − low)`,
    combined with `Relative Volume = volume / 20d avg volume` via the 2-D grid
    described in §6.1.
  - The current weight in the final blend.
  - The final `ESS` tooltip shows the full weighted formula (all six factors with
    their weights) plus the hard-gate conditions, so the number is never a black
    box — same transparency bar as the existing `Score` tooltip.
- Page-level regime banner (§5) and market-breadth indicator (§6.7) rendered once,
  not per-row.

## 8. Persistence — `stocks.entry_quality_daily`

One row per `(ticker, trade_date)`, written once per trading day for the set of
tickers that were in `allowed_tickers` (union across live, non-archived strategies)
∪ QM Score ≥ 58 that day.

**Schema** (primitives only; `DateType`/`TimestampType` tz-naive):

| Field | Type | Notes |
|---|---|---|
| `trade_date` | Date | partition key input |
| `ticker`, `market` | String | |
| `qm_score` | Double | existing 5-factor blend |
| `qm_sharpe_pctile`, `qm_rs_pctile`, `qm_mdd_pctile`, `qm_atr_closeness`, `qm_sma200_closeness` | Double | QM sub-factors, for later effectiveness analysis |
| `ess_score` | Double | final continuous blend |
| `ess_gate_passed` | Boolean | |
| `ess_gate_reason` | String (nullable) | which hard gate fired, if any |
| `ess_absorption_volume_score`, `ess_sma50_proximity_score`, `ess_trend_stability_score`, `ess_selling_deceleration_score`, `ess_roc5_score`, `ess_atr_expansion_score` | Double | ESS sub-factors |
| `nifty_return_pct` | Double | market-breadth context, not blended |
| `nifty_roc5_pct` | Double | index 5-day ROC, backs the -6% banner (§5) |
| `nifty_below_sma200` | Boolean | regime context |
| `in_allowed_tickers` | Boolean | was this ticker whitelisted that day |
| `written_at` | Timestamp | |

Storing sub-factors (not just the two blended numbers) is what lets the
effectiveness analysis in §9 identify *which* signal is actually predictive, not
just whether the composite is.

**Partition spec:** `MonthTransform(trade_date)` only — no ticker bucketing needed.
Both the `nse_delivery` and `algo.events` incidents came from partitioning *by* a
high-cardinality column; this table is one batched commit/day for ~250 tickers, so
there's no per-ticker file-multiplication risk. `SortOrder(ticker, trade_date)`
gives fast per-ticker lookups without a ticker partition.

**Projected file count:** ~1 commit/day × 252 trading days ≈ **250 files/year**
worst case with zero compaction — 20x under the 5,000-file budget.

**Maintenance tier: low-write** (< 10 commits/day, < 1MB/day) — enroll in
`ALL_TABLES` (`backend/maintenance/iceberg_maintenance.py`) and the **weekly**
Long-Tail Iceberg Maintenance pipeline. Does **not** need the daily hot-table
compaction loop (`_HOT_ICEBERG_TABLES`) — lighter maintenance burden than the algo
tables, by design.

**Backup:** covered automatically by the existing whole-warehouse `run_backup()`
rsync step — no per-table wiring needed.

**Retention:** wire `trade_date` into `DATE_COLUMNS`, but set generously (18-24
months) — the point of this table is measuring effectiveness across multiple
market regimes, and there's no space pressure at this volume forcing an earlier
purge.

## 9. Write path & validation loop

- New scheduled job (`@register_job`) runs **post-market-close**, computes the
  settled EOD snapshot (not the live intraday number shown during market hours) for
  that day's qualifying universe, and writes it as a single batched commit.
- **Must be inserted into the `scheduled_jobs` PG table in the same PR as the
  `@register_job` registration** — registering alone does not schedule it
  (reference: the `algo_reconciliation` incident, where a registered-but-unscheduled
  job silently never ran for months).
- **Validation loop:** because `entry_quality_daily` is queryable Iceberg data and
  real fills live in `algo.events` (also Iceberg), the effectiveness question — "did
  high-ESS entries do better than low-ESS ones" — is a single-engine DuckDB join,
  no cross-store bridging required. This is the actual deliverable of the
  persistence work, not just a historical log.
- **Cohort caveat for the validation join:** the persisted `qm_score`/`ess_score`
  are a settled-EOD, full-universe-cohort snapshot (QM Score's percentile subfactors
  ranked across the full candidate universe, not the viewer's own watchlist) —
  distinct from the live page's watchlist-scoped values a user might see at a
  different moment; an analyst joining `entry_quality_daily` against outcomes should
  treat the persisted score as the ground truth for this table, not expect it to
  reproduce what any individual user saw on the page that day.

## 10. Open items for the implementation plan

- Exact control-point values for all six continuous factors and the 2-D
  absorption/volume grid — ship with the illustrative anchors above as the
  starting hypothesis, refine once outcome data accumulates.
- Whether `Trend Stability`'s lookback window is a simple delta or a linear
  regression slope (both cheap; regression is slightly more robust to a single
  noisy day).

**Confirmed during review (2026-07-12):** -10% SMA50 hard-gate threshold; Market
Breadth as page-level context + persisted column, not a blended factor; Nifty
5-day ROC < -6% added as a second market-wide banner (§5), mirroring the per-stock
ROC5 factor at the index level.

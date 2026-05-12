# Swing Setups — Three Regime-Ranked Watchlists from Advanced Analytics

**Date:** 2026-05-12
**Status:** Design — pending implementation plan
**Phase:** A (this spec) → C (algo-DSL port, separate later spec)

---

## 1. Goal

Generate three ranked, user-scoped watchlists every trading day —
**Bull-swing**, **Sideways-swing**, **Bearish-swing** — by combining
signals already computed for the seven Advanced Analytics reports plus
the LLM recommendation engine output. Ship as a new Advanced Analytics
tab; reuse the existing row pipeline, scoping helper, and caching
layer. No new Iceberg tables, no new pipeline step.

Phase A delivers ranked lists users can act on manually or feed into
the portfolio modal. Phase C (separate future spec) ports validated
rules into the algo-trading v3 DSL as strategy templates for
backtest / paper / live.

## 2. Non-goals

- Backtesting or P&L analytics — that's Phase C.
- Auto-execution of any kind — output is read-only ranked lists.
- New data sources / new Iceberg tables / new pipeline step.
- Fixing the unrelated three-day-scan filter bug
  (`advanced_analytics_routes.py:767-768` checks 2-day condition under
  a 3-day label) — flag separately on a Jira ticket; out of scope here.

## 3. Where this fits

- **Backend route:** `GET /v1/advanced-analytics/swing-setups`
  (sibling of the seven existing reports under
  `backend/advanced_analytics_routes.py`)
- **Frontend tab:** new `SwingSetupsTab` under
  `frontend/components/advanced-analytics/`, registered in the AA tab
  strip. Tab body has three inner sub-tabs: Bull / Sideways / Bearish.
- **Universe:** `_scoped_tickers(user, "discovery")` — Pro/superuser
  see full `stock` + `etf` registry; general users see watchlist ∪
  holdings. Same as existing AA scoping (§5.9 of CLAUDE.md).
- **Market filter:** same `detect_market` flow as existing AA tabs.
- **Cache:** two-layer mirror of existing AA — outer cache on
  `(user_id, as_of, scope, market)` for the full row set; inner cache
  on the full filter+regime tuple for the paginated response. TTLs:
  outer `TTL_STABLE=300`, inner `TTL_STABLE=300`.

## 4. Data model — what each row needs

Every regime ranks over the same `AdvancedRow` superset used by the
seven existing reports (`backend/advanced_analytics_routes.py:281-422`).
The following **new computed columns** are added to that row builder.
All derive from the 215-trading-day OHLCV history already loaded; no
new I/O per ticker.

| Column | Definition | Used by |
|---|---|---|
| `death_cross_days_ago` | Inverse of `golden_cross_days_ago` — trading days since SMA-50 last crossed BELOW SMA-200. `None` if no cross in window. | Bearish |
| `rolling_low_20d_prev` | min(low) over the 20 trading days *preceding* today | Sideways rank, Bearish gate |
| `rolling_high_20d_prev` | max(high) over the 20 trading days preceding today | Sideways rank |
| `rsi_3d_ago` | RSI-14 value 3 trading days ago | Bearish (rollover detection) |
| `rsi_max_10d` | max RSI-14 over last 10 trading days | Bearish (rollover detection) |

Plus a **new join** to the recommendation engine:

- For each row, attach the **latest active recommendation** from
  `stocks.recommendations` (joined via `stocks.recommendation_runs`)
  scoped to `user_id` and the same market, where `status = 'active'`.
- Fields surfaced onto the row:
  `rec_category` (str), `rec_severity` (str: high/medium/low),
  `rec_expected_return_pct` (float, nullable).
- Single PG → DuckDB materialized join per user request (not per
  ticker — batch via `WHERE ticker = ANY(:tickers)`).
- **Graceful degrade:** if the user has no rec run this IST month, all
  three fields are `None`. Bull regime then drops the rec gate and
  surfaces a UI chip "Recommendation gate not applied — no rec run
  this month" (transparency-chip pattern, §5.5 of CLAUDE.md).

## 5. The three regimes

### 5.1 Bull-swing list

**Hard gates (ALL must hold):**

1. `today_ltp > sma_50 > sma_200` OR
   `0 ≤ golden_cross_days_ago ≤ 30`
2. `2 ≤ today_x_vol ≤ 5` (sweet-spot; above 5× is usually
   news-spike / exhaustion, not clean breakout)
3. `current_dpc > avg_20d_dpc` (today's delivery % above 20-day avg)
4. `x_dv_20d > 1` (20-day delivery accumulation trend up)
5. `rsi < 70` (not exhausted)
6. `pscore ≥ 5` AND `pledged_pct < 10` (quality floor)
7. `today_ltp / week52_high < 0.95` (room to run, not at top)
8. **If rec available:** `rec_category ∈ {offensive, value, growth,
   hold_accumulate}`. No severity tightening in Phase A — severity
   is surfaced on the row for analysis but doesn't gate. If no rec
   for user this month, gate is skipped (with UI chip).

**Bullish category set** — pinned 2026-05-12 from DB inspection:
`{offensive, value, growth, hold_accumulate}`. The rec engine uses
a portfolio-action vocabulary (not stock-rating); these four are
the categories whose semantics map to "go long this name". Coverage
is sparse (~21 of 78 active recs match), so the rec-gate-degraded
path will be the common case for most users most days — the UI
transparency chip exists precisely for this.

**Rank:**

```
score = max(rec_expected_return_pct, 0) * x_dv_20d * today_x_vol
```

If `rec_expected_return_pct` is `None` (degrade), substitute `1.0` so
the rank reduces to `x_dv_20d * today_x_vol`. Sort DESC, cap 25.

### 5.2 Sideways-swing list

**Hard gates:**

1. `abs(sma_50 - sma_200) / sma_200 < 0.05` (MAs converged — no
   trend)
2. `abs(today_ltp - sma_50) / sma_50 < 0.03` (price near SMA-50,
   not on an edge)
3. `40 ≤ rsi ≤ 60`
4. `0.7 ≤ today_x_vol ≤ 1.3` (no surge, no drought — true
   consolidation)
5. `today_not > 50,000,000` (₹5 crore turnover floor; for US tickers
   the equivalent USD threshold is `today_not_usd > 600,000`)
6. `pscore ≥ 4` (basic quality)

**Rank:** Distance from band-edge for reversion entry —

```
dist_to_edge = min(today_ltp - rolling_low_20d_prev,
                   rolling_high_20d_prev - today_ltp) / today_ltp
```

Sort ASC (closer to band edge ranks higher), cap 25.

### 5.3 Bearish-swing list

**Hard gates:**

1. **Death-cross active**: `sma_50 < sma_200` currently
   AND `death_cross_days_ago ≤ 60` (cross within last 60 trading
   days — not stale)
2. **RSI rollover**: `rsi_max_10d ≥ 60` AND `today_rsi ≤ 50`
   AND `today_rsi < rsi_3d_ago` (was strong, now broken, still
   declining)
3. **Lower-low break**: `today_low < rolling_low_20d_prev` (decisive
   break of 20-day floor)
4. `today_ltp / week52_low > 1.05` (not already at floor — leaves
   swing room)
5. `today_not > 50,000,000` (₹5 crore turnover floor; US:
   `> 600,000 USD`)

No fundamentals / governance gate — we want to short overvalued
quality names too, not just deteriorating businesses.

**Rank:** Composite freshness × severity × decisiveness —

```
score = (1 / (death_cross_days_ago + 1))
      * max(0, 60 - today_rsi)
      * (rolling_low_20d_prev - today_low) / rolling_low_20d_prev
```

Sort DESC, cap 25. Rewards fresh death-cross + decisive RSI breakdown
+ clean break of 20-day low simultaneously.

## 6. API surface

### Request

```
GET /v1/advanced-analytics/swing-setups
    ?regime=bull|sideways|bearish
    &market=india|us|all   (default: all)
    &page=1&page_size=25   (default: 25, max: 100)
    &sort=<col>:asc|desc   (default: regime-specific rank above)
```

### Response

```json
{
  "rows": [ AdvancedRow + computed extras ],
  "total": 23,
  "regime": "bull",
  "as_of": "2026-05-12",
  "rec_gate_applied": true,
  "rec_run_id": "uuid-or-null",
  "rec_run_date": "2026-05-01",
  "notes": ["Recommendation gate not applied — no rec run this month"],
  "methodology": {
    "regime": "bull",
    "summary": "Trend-up stocks with fresh delivery-backed demand confirmed by the LLM recommendation engine.",
    "gates": [
      {
        "label": "Trend stack",
        "rule": "today_ltp > sma_50 > sma_200 OR golden_cross_days_ago ≤ 30",
        "why": "Establishes an uptrend or a fresh trend reversal."
      },
      {
        "label": "Volume sweet spot",
        "rule": "2 ≤ today_x_vol ≤ 5",
        "why": "Below 2× lacks conviction; above 5× is usually news-spike / exhaustion."
      },
      { "label": "Delivery confirmation", "rule": "current_dpc > avg_20d_dpc", "why": "Today's delivery % above 20-day average — real buying, not just churn." },
      { "label": "Accumulation trend", "rule": "x_dv_20d > 1", "why": "20-day delivery quantity trending up." },
      { "label": "Not exhausted", "rule": "rsi < 70", "why": "Leaves room before momentum reverses." },
      { "label": "Quality floor", "rule": "pscore ≥ 5 AND pledged_pct < 10", "why": "Filters out distressed names." },
      { "label": "Room to run", "rule": "today_ltp / week52_high < 0.95", "why": "Not already at the top of the 52-week range." },
      { "label": "Rec-engine bullish", "rule": "rec_category ∈ {bullish set} AND rec_severity ∈ {high, medium}", "why": "LLM engine independently confirms the thesis. Skipped if user has no rec run this month (chip surfaced)." }
    ],
    "rank": {
      "formula": "max(rec_expected_return_pct, 0) × x_dv_20d × today_x_vol",
      "direction": "DESC",
      "cap": 25,
      "degraded": "When no rec run for user this month, formula reduces to x_dv_20d × today_x_vol."
    },
    "as_of": "2026-05-12"
  }
}
```

- `rec_gate_applied`: `true` iff bull regime found a rec run; `false`
  in degraded mode. UI uses this to render the transparency chip.
- `notes`: human-readable strings shown as panel chips (§5.5).
- `methodology`: structured rules block — backend is the single source
  of truth for thresholds. Tuning in Phase A.5 updates one place
  (a `methodology.py` module) and the UI re-renders automatically.
  Returned for every request (small payload, ~1 KB); also available
  standalone at `GET /v1/advanced-analytics/swing-setups/methodology
  ?regime=...` for the page-load case.
- Row payload extends existing `AdvancedRow` schema (no breaking
  change to the seven existing tabs).

## 7. Frontend surface

### 7.1 Tab structure

New tab `swing-setups` in the AA tab strip, label "Swing Setups".
Tab body renders a three-pill segmented control (Bull / Sideways /
Bearish) above a single shared table. Selecting a pill issues a new
request with the appropriate `regime` query param.

### 7.2 Table

Follows the **tabular page pattern** (CLAUDE.md §5.4):

- `useColumnSelection("aa.swing.<regime>", DEFAULTS, VALID_KEYS)` —
  separate storage key per regime so users can curate each list
  independently.
- `<ColumnSelector lockedKeys={["ticker"]}>` with full AdvancedRow
  catalog plus regime-specific extras (`death_cross_days_ago`,
  `rec_category`, etc.).
- `<DownloadCsvButton rows={sortedRows} cols={visibleCols} />` next
  to pagination (NOT in header).
- Server-side pagination if `total > 200` else client-side. Default
  page size 25.
- Column-header sort + arrow; default = regime rank (server-side).
- Empty state: skeleton during load; "No setups match today" CTA when
  empty.
- Amber transparency chip in panel-title row when Bull regime
  degrades to no-rec mode.

### 7.3 Default visible columns per regime

| Regime | Default cols |
|---|---|
| Bull | ticker, sector, today_ltp, today_x_vol, current_dpc, x_dv_20d, rsi, sma_50, rec_category, rec_expected_return_pct, pscore |
| Sideways | ticker, sector, today_ltp, sma_50, rsi, today_x_vol, rolling_low_20d_prev, rolling_high_20d_prev, today_not, pscore |
| Bearish | ticker, sector, today_ltp, today_low, sma_50, sma_200, death_cross_days_ago, rsi, rsi_max_10d, rolling_low_20d_prev |

`ticker` is the locked identity column for all three.

### 7.4 Methodology panel ("How this list is built")

Each regime's filter rules are surfaced on the page so users can
**see exactly which gates produced the list** and self-diagnose why
a ticker did or didn't make the cut. Rendered from the backend
`methodology` block — no hard-coded rule copy in the frontend.

**Layout:** A collapsible info strip sits between the regime-pill
row and the table. Default state: **expanded on first visit per
regime per session** (localStorage flag `aa.swing.<regime>.methodology_seen`),
collapsed thereafter. A persistent `<InfoIcon>` button to the right
of the pill row toggles it any time.

**Structure inside the strip:**

```
┌─────────────────────────────────────────────────────────────────┐
│ How this Bull-swing list is built                  [collapse ▲] │
│                                                                 │
│ {methodology.summary}                                           │
│                                                                 │
│ Gates (all must hold):                                          │
│   ① Trend stack — today_ltp > sma_50 > sma_200                  │
│         OR golden_cross_days_ago ≤ 30                           │
│     ↳ Establishes an uptrend or a fresh trend reversal.         │
│   ② Volume sweet spot — 2 ≤ today_x_vol ≤ 5                     │
│     ↳ Below 2× lacks conviction; above 5× is news-spike noise.  │
│   ③ … (one row per gate, label / rule / why)                    │
│                                                                 │
│ Ranking: {methodology.rank.formula} (DESC, top 25)              │
│     ↳ Rewards confirmed picks with persistent delivery and      │
│       fresh volume thrust simultaneously.                       │
│                                                                 │
│ Last updated: 2026-05-12 · Source: stocks.ohlcv + nse_delivery  │
│ + stocks.recommendations                                        │
└─────────────────────────────────────────────────────────────────┘
```

**Implementation notes:**

- Renders from `response.methodology` — never hard-code rules in JSX.
- Each gate row uses monospace for the `rule` expression (clarity)
  and regular weight for the `why` sentence.
- Rule expressions use the same column names that appear in the
  table headers — click on a header chip and the matching gate row
  scrolls into view + highlights (lightweight scroll-target).
- On the degraded-rec-gate path, the Rec-engine row gets a strike-
  through and the `notes` chip explains.
- Tooltip on the `InfoIcon` button: "See the rules used to build
  this list".
- Mobile: strip collapses by default; tap header to expand.

**Why backend-sourced and not a static doc page:** rules will be
tuned in Phase A.5 based on observed hit-rates. Sourcing from the
backend means a single edit to `methodology.py` updates both the
filter behaviour and the on-page explanation in lockstep — no drift
between code and copy.

### 7.5 Row actions

Each row exposes the standard AA row-actions (view ticker detail,
add to watchlist, open in chart). No new modal in Phase A. Adding
"Send to algo paper run" lands in Phase C with the DSL port.

## 8. Caching

- **Invalidation:** every Iceberg write to `stocks.ohlcv` or
  `stocks.nse_delivery` (already wired in `_CACHE_INVALIDATION_MAP`,
  CLAUDE.md §5.13) — add `cache:aa:swing:*` glob to that map's
  entries. New rec-engine runs invalidate
  `cache:aa:swing:bull:{user_id}:*`.
- **Per-user key**: `cache:aa:swing:<regime>:<user_id>:<as_of>:<market>`.
- **Concurrency:** existing `_pg_session()` (NullPool sync→async
  bridge, CLAUDE.md §4.1) for the recommendations join. Don't use it
  inside the per-row hot loop — batch the lookup once per request.

## 9. Testing

**Backend (pytest):**

- Unit test: `methodology.py` returns a complete block for each
  regime with all gates labelled, all `rule` strings non-empty, and
  the rank formula matching what the route actually applies (snapshot
  test — if someone changes a threshold in the filter but forgets to
  update the methodology block, this fails loudly).
- Unit tests for the three new computed columns
  (`death_cross_days_ago`, `rolling_low_20d_prev`, RSI lookback) with
  synthetic OHLCV fixtures. Happy path + one boundary each
  (no cross in window, sub-20-day history, NaN handling).
- Unit tests for each regime filter — happy path + one excluded path
  (e.g., bull regime rejects when `today_x_vol = 5.5`).
- Integration test: full route with seeded `AdvancedRow` rows + mocked
  rec run; assert ordering matches the published rank formula.
- Degraded path: user with no rec run gets `rec_gate_applied: false`
  and the bull-regime rec-category gate is bypassed.
- NaN handling: `safe_float` (CLAUDE.md §6.1) used on every numeric
  gate; row with NaN in a hard-gate column is excluded silently
  (don't crash the response).

**Frontend (vitest + Playwright):**

- vitest: SwingSetupsTab renders three pills; switching pill triggers
  new SWR fetch; column selector persists per-regime localStorage key.
- vitest: MethodologyPanel renders all gates from the backend
  `methodology` block, collapses after first view per regime via
  localStorage flag, and renders the degraded-rec-gate strike-through
  when `rec_gate_applied: false`.
- Playwright (`e2e/utils/selectors.ts` registry update):
  `swing-setups-tab`, `swing-regime-pill-{bull|sideways|bearish}`,
  `swing-empty-state`. Page Object Model under
  `e2e/pages/frontend/`. Reuse general-user + superuser fixtures.

**Coverage target:** match existing AA route coverage (≥ 80% on the
new code paths).

## 10. Phased rollout

- **Phase A — this spec:** ship the tab + three lists as a read-only
  watchlist generator. Eyeball-validate for ~3-4 weeks across a few
  market regimes (trending / ranging / corrective).
- **Phase A.5 — tuning:** based on actual outcomes
  (`stocks.recommendation_outcomes` already tracks 7/30/60/90d
  returns; we can extend that machinery to score the swing-setups
  hit-rate), tune thresholds. Use the existing performance-cohort
  endpoint pattern (CLAUDE.md §5.8).
- **Phase C — algo-DSL port (separate spec, future):** lift each
  regime's rule set into a strategy AST template inside the algo
  trading v3 engine (`project_algo_v3_complete`). Strategies run in
  backtest / paper / live. Phase A's three lists then become "live
  candidates for the v3 strategy" rather than the strategy itself.

## 11. Open questions / runtime verifications

These are flagged for the implementation-plan phase, not blockers for
the spec:

- **Q1:** ~~Exact bullish category enum values~~ — **RESOLVED
  2026-05-12.** Pinned set: `{offensive, value, growth,
  hold_accumulate}`. Rec engine uses portfolio-action vocabulary,
  not stock-rating; coverage is sparse (~27% of active recs).
  Severity field surfaced on row but does not gate (Phase A.5 may
  revisit).
- **Q2:** Whether `today_not` is denominated in INR or native currency
  per ticker. If native, US tickers need a USD threshold variant in
  the sideways + bearish liquidity gates (placeholder used in §5.2 /
  §5.3; verify in plan).
- **Q3:** Whether `death_cross_days_ago` should track only the *most
  recent* cross (mirror of `golden_cross_days_ago`) or also expose a
  flag for "currently below" without requiring a recent cross.
  Current spec uses recent-cross-only (≤ 60 days).
- **Q4:** Does the AA inner cache pattern accommodate adding
  `regime` as a key dimension cleanly? Spot-check
  `advanced_analytics_routes.py:760-820` during plan-write.

## 12. Risks

- **Rec-engine coupling:** if rec runs are delayed/missing, the bull
  list silently broadens. Transparency chip is the mitigation but
  users may not notice. Consider a stricter mode (return empty list
  with explanatory error) as a follow-up.
- **Recommendation runs are monthly per scope.** Mid-month the rec
  data drifts vs. live signals. Acceptable in Phase A;
  re-evaluate when porting to Phase C.
- **No real-time intraday refresh** — the seven existing AA reports
  all rebuild only when the daily pipeline writes new OHLCV /
  delivery rows. Inheriting that cadence. Document on the tab that
  data is "as of yesterday's close" until intraday pipeline lands.
- **Bearish regime in long-only accounts:** UI must NOT suggest
  shorting actions for users without F&O / margin enabled. For
  Phase A, the bearish list is read-only and labelled
  "candidates to avoid / exit"; no buy/sell action button.

## 13. Out of scope

- Three-day-scan filter bug
  (`advanced_analytics_routes.py:767-768`) — file separately.
- Adding `current-day-downmove` and `range-bound-consolidation` as
  standalone AA reports — Approach B was not chosen.
- Algo-DSL strategy templates — Phase C, separate spec.
- Real-time / intraday refresh of the swing lists — daily cadence
  inherited from existing AA pipeline.
- Notification / alerting when a ticker enters or exits a list —
  follow-up after eyeball-validation.

## 14. Estimated size

Rough breakdown for the implementation plan to flesh out:

- Backend (route, computed cols, rec join, methodology module,
  tests): ~6 SP
- Frontend (tab, three pills, column selector wiring, methodology
  panel, tests): ~5 SP
- E2E (selectors, fixtures, two specs including methodology-visible
  assertion): ~2 SP
- Polish + docs (CLAUDE.md pattern entry, Serena memory, PROGRESS.md):
  ~1 SP

Total ~14 SP — single sprint slice.

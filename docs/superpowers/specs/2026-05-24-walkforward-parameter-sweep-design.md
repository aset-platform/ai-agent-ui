# Walk-Forward Parameter Sweep (1D) — Design

**Date:** 2026-05-24
**Author:** Abhay (with Claude pair-programming)
**Status:** Spec — pending implementation plan
**Builds on:** the cooldown sweep script shipped earlier
today (`scripts/sweep_rsi2_v3_cooldown.py`) and PR #240's
walk-forward tooltip work.

## Goal

Let users run a **1D parameter sweep** of a saved strategy
through the existing walk-forward UI: pick a strategy, pick
one tunable field, list the values to try; the engine runs
a full walk-forward CV per value and reports per-variant
metrics plus a **cross-variant Probability of Backtest
Overfitting (PBO)** — the multi-trial measurement the
single-strategy walk-forward today cannot compute.

## Background — what's missing today

1. **`WalkForwardConfig.strategy_id` is singular**
   (`backend/algo/routes/walkforward.py:55-58`). One
   strategy in, one walk-forward result out.
2. **`n_trials = 1` is hardcoded** in `run_walkforward_job`
   (`backend/algo/backtest/walkforward.py:776`). PBO is
   always `None` because the engine never sees multiple
   trials.
3. **No sweep configuration UI** — no field-picker, no
   value-list input.
4. **The cooldown sweep script** we shipped today
   (`scripts/sweep_rsi2_v3_cooldown.py`) does single-period
   backtests, not walk-forward. It's the right plumbing
   for PBO computation but the wrong rigour for promotion
   decisions.

## Out of scope (deferred to follow-ups)

Explicit non-goals to keep v1 shippable:

| Deferred | Why |
|---|---|
| Grid (multi-param combinatorial) sweep | 1D first per design decision. Grid is its own follow-up epic. |
| AST-path escape hatch for non-whitelist fields | Whitelist only in v1. If users push back, add hybrid UI in v2. |
| Parallel variant execution | Serial in v1. Re-evaluate if wall-clock is a real complaint. |
| Sweep cancellation mid-run | Cancel button marks sweep failed but the currently-running variant continues. Real cancellation requires a larger walk-forward-engine change. |
| Sweep templates / saved configs | "Save this sweep config as a template" — operational nice-to-have. |
| Cross-strategy sweeps (different strategies, not parameters within one) | Different feature entirely. |
| Auto-promotion to paper | v1 only ships "Save winner as new strategy"; manual paper-promotion via existing flow. |
| Bootstrap CI on PBO | Ship point estimate; add CI if users distrust it. |
| Returns-matrix heatmap | Useful debug visualisation but polish. |
| `n_blocks` UI control | Hardcoded 16 (fallback 8 for small T). Exposing it would be overfitting the overfitting test. |
| DSR across variants | Per-variant DSR is shown; cross-variant statistical question is PBO. |

## Architecture

A sweep is **one level higher than walk-forward**: it owns
N walk-forward children, one per variant. Each variant has
its own walk-forward parent, which in turn owns its window
children. Three-level row tree in `algo.runs`:

```
algo.runs (mode='sweep')              ← sweep parent
   ├─ algo.runs (mode='walkforward')  ← variant 1
   │     ├─ algo.runs (mode='backtest')  ← variant 1 window 1
   │     ├─ algo.runs (mode='backtest')  ← variant 1 window 2
   │     └─ ...                          ← variant 1 windows
   ├─ algo.runs (mode='walkforward')  ← variant 2
   │     └─ ...
   └─ ... N variants total
```

**The sweep parent row** stores in its `summary_json`:

- Swept field — short whitelist key (e.g.
  `cooldown_days`). The dotted AST path it maps to
  lives in the whitelist metadata, not in the row.
- Swept values (e.g. `[3, 7, 14, 21, 28]`)
- Cross-variant PBO via Bailey-de Prado CSCV
- Per-variant ranking by Sharpe / DSR / PnL
- Winner variant index + value
- `(T, N)` shape of the aligned-returns matrix

**Serial execution.** The sweep job iterates variants in
order, mutating the strategy AST **in memory only** (base
strategy in PG untouched), calling existing
`run_walkforward_job` for each. When all variants finish,
the sweep job pulls each variant's `summary_json`,
chains per-window equity curves, builds a `(T, N)` returns
matrix, computes cross-variant PBO, and writes the result
to its own `summary_json`.

**Failure handling**: same pattern as walk-forward windows
— if a variant fails, mark its walkforward row `failed`,
continue with the next. The sweep parent finishes
`completed` if ≥ 2 variants survive (PBO needs ≥ 2).
If < 2 survive, sweep parent marks `failed` with a clear
error.

## Backend changes

### Schema migration

Single Alembic migration:

```sql
ALTER TABLE algo.runs
    ADD COLUMN parent_sweep_id UUID NULL
        REFERENCES algo.runs(id) ON DELETE SET NULL;
CREATE INDEX idx_runs_parent_sweep_id
    ON algo.runs (parent_sweep_id)
    WHERE parent_sweep_id IS NOT NULL;
```

`mode` accepts arbitrary strings already — no migration
needed for `mode='sweep'`.

### New Pydantic types

`backend/algo/backtest/sweep_types.py` (new):

```python
class SweepConfig(BaseModel):
    """Body for POST /v1/algo/sweep/run"""
    base_strategy_id: UUID
    period_start: date
    period_end: date
    train_days: int = 60
    test_days: int = 30
    step_days: int = 30
    initial_capital_inr: Decimal = Decimal("100000.00")
    regime_stratified: bool = False
    swept_field: str  # short whitelist key
                     # e.g. "cooldown_days"
    swept_values: list[Any]  # validated per field metadata
    interval_sec: int = 86400


class SweepVariantSummary(BaseModel):
    variant_index: int
    swept_value: Any
    walkforward_run_id: UUID  # FK to child algo.runs row
    avg_pnl_pct: Decimal
    avg_win_rate_pct: Decimal
    avg_max_drawdown_pct: Decimal
    sharpe: Decimal
    dsr: Decimal
    n_trades: int
    status: Literal["completed", "failed", "skipped"]
    error_text: str | None = None


class SweepResult(BaseModel):
    """Sweep parent row's summary_json shape.

    ``swept_field`` is the short whitelist key (e.g.
    ``"cooldown_days"``) — NOT the dotted AST path. The
    path is derivable via SWEEPABLE_FIELDS at read time.
    Keeping the key (not the path) means a future rename
    of the underlying AST path doesn't orphan historical
    sweep rows.
    """
    run_id: UUID
    base_strategy_id: UUID
    swept_field: str
    swept_values: list[Any]
    variants: list[SweepVariantSummary]
    cross_variant_pbo: Decimal | None  # NaN-safe
    returns_matrix_shape: tuple[int, int]  # (T, N)
    winner_variant_index: int | None  # by Sharpe rank
    started_at: datetime
    completed_at: datetime | None
    status: Literal[
        "pending", "running", "completed", "failed",
    ]
```

### Whitelist + validation

`backend/algo/backtest/sweep_whitelist.py` (new):

```python
@dataclass(frozen=True)
class SweepableField:
    path: str         # dotted AST path
    label: str        # UI label
    field_type: Literal["int", "decimal"]
    min_value: Decimal
    max_value: Decimal


SWEEPABLE_FIELDS: dict[str, SweepableField] = {
    "cooldown_days": SweepableField(
        path="risk.per_trade.cooldown_after_failed_exit_days",
        label="Cooldown (days)",
        field_type="int",
        min_value=Decimal("0"),
        max_value=Decimal("60"),
    ),
    "stop_loss_pct": SweepableField(
        path="risk.per_trade.stop_loss_pct",
        label="Stop loss %",
        field_type="decimal",
        min_value=Decimal("0.5"),
        max_value=Decimal("20.0"),
    ),
    "max_holding_days": SweepableField(
        path="risk.per_trade.max_holding_days",
        label="Max holding days",
        field_type="int",
        min_value=Decimal("1"),
        max_value=Decimal("60"),
    ),
    "max_qty": SweepableField(
        path="risk.per_trade.max_qty",
        label="Max qty per fill",
        field_type="int",
        min_value=Decimal("1"),
        max_value=Decimal("100000"),
    ),
    "min_adtv_inr": SweepableField(
        path="universe.filter.min_adtv_inr",
        label="Min ADTV (₹)",
        field_type="decimal",
        min_value=Decimal("10000000"),       # ₹1 Cr
        max_value=Decimal("1000000000"),     # ₹100 Cr
    ),
    "daily_max_loss_pct": SweepableField(
        path="risk.daily.max_loss_pct",
        label="Daily max loss %",
        field_type="decimal",
        min_value=Decimal("0.5"),
        max_value=Decimal("10.0"),
    ),
    "max_concentration_pct": SweepableField(
        path="risk.portfolio.max_concentration_pct",
        label="Max position concentration %",
        field_type="decimal",
        min_value=Decimal("5"),
        max_value=Decimal("50"),
    ),
}


def validate_swept_values(
    field_key: str, values: list[Any],
) -> list[Decimal | int]:
    """Validate + coerce. Raises ValueError on bad input.
    Enforces:
      - field_key ∈ SWEEPABLE_FIELDS
      - len(values) ≥ 2
      - each value parses to field_type
      - each value within [min_value, max_value]
      - all values distinct (no duplicates)
    """
```

### Sweep runner

`backend/algo/backtest/sweep.py` (new):

```python
async def run_sweep_job(
    *,
    sweep_run_id: UUID,
    user_id: UUID,
    config: SweepConfig,
    base_strategy: Strategy,
    universe: list[str],
) -> None:
    """Serial sweep orchestrator.

      1. Mark sweep parent row 'running'.
      2. For each value V in config.swept_values:
         a. Deep-copy base_strategy AST.
         b. Mutate strategy[<swept_field.path>] = V.
         c. Create child walkforward row with
            parent_sweep_id=sweep_run_id.
         d. Call run_walkforward_job(...) and await.
         e. Record variant summary in a local list.
      3. After all variants:
         a. Pull each variant's equity curve.
         b. Chain per-window curves into one variant
            curve (see PBO computation below).
         c. Align dates → (T, N) returns matrix.
         d. Compute cross_variant_pbo via
            probability_of_backtest_overfitting().
         e. Rank by per-variant Sharpe.
         f. Write SweepResult to sweep parent's
            summary_json.
         g. Mark sweep parent 'completed'.

    Never raises — all errors written via mark_failed.
    """
```

Mutation helper:

```python
def _mutate_ast(
    strategy: Strategy, path: str, value: Any,
) -> Strategy:
    """Deep-copy + set nested attribute by dotted path.

    Path parts traverse Pydantic models via getattr +
    setattr. Raises ValueError if path doesn't resolve.
    """
```

### Routes

`backend/algo/routes/sweep.py` (new module):

```
POST   /v1/algo/sweep/run
       → 202 + sweep_run_id (kicks off background job)

GET    /v1/algo/sweep/runs/{id}
       → SweepResult + progress

GET    /v1/algo/sweep/runs
       → list, paginated, by user

GET    /v1/algo/sweep/fields
       → SWEEPABLE_FIELDS for the form dropdown
```

Reuses `pro_or_superuser` dependency. Caching:
`/sweep/fields` → `TTL_STABLE=300` (whitelist rarely
changes). `/sweep/runs/{id}` during run → no cache (polled
every 2-3 s); after completion → `TTL_STABLE`.

### Repo extension

`BacktestRunsRepo` adds:

```python
async def list_children_of_sweep(
    self, session, sweep_run_id: UUID,
) -> list[Row]:
    """Variant walkforward rows owned by a sweep parent."""

async def create_pending_sweep(
    self, session, *,
    user_id, base_strategy_id, period_start, period_end,
) -> Row:
    """Like create_pending but with mode='sweep'."""
```

## PBO computation details

### Data source

Each variant's walk-forward child row in `algo.runs` has
`summary_json` of shape `WalkForwardResult`, which already
includes `window_summaries: list[BacktestSummary]` →
`equity_curve: list[EquityPoint]` per window.

The sweep aggregator pulls N already-persisted rows from
PG and works in-process. **No re-running of backtests;
only post-processing.**

### Per-variant continuous equity curve

Each walk-forward window starts fresh at
`initial_capital_inr`. To turn N independent windows into
ONE continuous curve per variant, we chain the per-window
RETURNS:

```python
def variant_equity_curve(
    window_summaries: list[BacktestSummary],
    initial_capital: Decimal,
) -> list[tuple[date, Decimal]]:
    """Chain per-window returns into one continuous curve.

    Each window's curve goes from initial_capital to
    final_equity over its test_days. We compute the
    window's daily MULTIPLIER curve and apply it to a
    running capital that starts at initial_capital and
    compounds across windows.
    """
    points: list[tuple[date, Decimal]] = []
    running = initial_capital
    for w in sorted(
        window_summaries, key=lambda s: s.period_start,
    ):
        if not w.equity_curve:
            continue
        start_eq = Decimal(str(w.equity_curve[0].equity_inr))
        for pt in w.equity_curve:
            ratio = (
                Decimal(str(pt.equity_inr)) / start_eq
            )
            points.append((pt.bar_date, running * ratio))
        running = points[-1][1]
    return points
```

### Aligning variants → returns matrix

```python
def build_returns_matrix(
    variants_curves: list[list[tuple[date, Decimal]]],
) -> tuple[np.ndarray, list[date]]:
    """Returns (R, common_dates) where R is shape (T, N)."""
    date_sets = [
        {d for d, _ in curve} for curve in variants_curves
    ]
    common = sorted(set.intersection(*date_sets))
    if len(common) < 2:
        return (np.zeros((0, 0)), [])

    cols = []
    for curve in variants_curves:
        d2v = {d: float(v) for d, v in curve}
        seq = np.array(
            [d2v[d] for d in common], dtype=float,
        )
        rets = np.diff(seq) / seq[:-1]
        # Replace inf/nan (period-end MTM artifacts) with 0
        rets = np.where(
            np.isfinite(rets), rets, 0.0,
        )
        cols.append(rets)
    return np.column_stack(cols), common[1:]
```

### PBO call site

Reuse the existing
`probability_of_backtest_overfitting(R, n_blocks=16)`:

```python
def compute_sweep_pbo(R: np.ndarray) -> Decimal | None:
    T, N = R.shape
    if N < 2 or T < 8:
        return None  # PBO undefined → UI shows "N/A"
    n_blocks = 16 if T >= 16 else 8
    pbo = probability_of_backtest_overfitting(
        R, n_blocks=n_blocks,
    )
    if pbo != pbo:  # NaN
        return None
    return Decimal(str(round(pbo, 3)))
```

### Edge cases

| Scenario | Behaviour |
|---|---|
| < 2 variants complete | sweep `failed`, error_text = "Need ≥ 2 completed variants for PBO" |
| T < 8 common days | sweep `completed`; cross_variant_pbo = `None`; UI shows "N/A — too few common days" |
| Variant fails mid-run | row stays `failed`; sweep aggregator skips it; if survivors ≥ 2, PBO computed on survivors only |
| Variants have NO common dates | (shouldn't happen — same period, same universe) sweep fails with explicit error |
| Byte-identical variants (the cd=7/14/21 case) | PBO still works — ties don't break the CSCV algorithm; UI rank-ties them |
| Variant returns all-zero (no trades) | zero-variance column; `_block_sharpe` returns NaN; PBO function handles NaN columns |

### Why we don't reuse per-variant DSR

- **DSR** (per variant): "is THIS variant's Sharpe
  distinguishable from luck?"
- **PBO** (cross variant): "if I pick the best-looking
  variant in-sample, does it survive out-of-sample?"

Both surface in the UI: per-row DSR in the variant table,
single headline PBO for the sweep.

## Frontend changes

### Information architecture

Backtest tab gets a third sub-tab:

```
Backtest:
  ├── Single run
  ├── Walk-forward CV
  └── Parameter sweep     ← new
```

### New files

| Path | Responsibility |
|---|---|
| `frontend/components/algo-trading/SweepSubTab.tsx` | Top-level container |
| `frontend/components/algo-trading/SweepForm.tsx` | Input form |
| `frontend/components/algo-trading/SweepResultsTable.tsx` | Per-variant table (Sharpe-ranked) |
| `frontend/components/algo-trading/SweepEquityCurves.tsx` | Overlaid equity curves (reuses `WalkForwardEquityCurves` pattern) |
| `frontend/hooks/useSweepRuns.ts` | SWR hooks (mirrors `useWalkForwardRuns`) |
| `frontend/hooks/useSweepableFields.ts` | SWR hook for `GET /v1/algo/sweep/fields` |
| `frontend/lib/types/algoSweep.ts` | TS shapes |

### Form layout

```
┌─ Parameter sweep ───────────────────────────────────────────┐
│ Base strategy: [RSI(2) Connors Daily v3            ▼]       │
│ Period from: [2025-11-23]  Period to: [2026-05-23]          │
│ Train:[60]  Test:[30]  Step:[30]  Capital:[100000]          │
│ ☐ Regime-stratified                                         │
│                                                             │
│ ─ Sweep parameter ──────────────────────────────────        │
│ Field: [Cooldown (days)                       ▼]            │
│ Values: [3, 7, 14, 21, 28      ]  (comma-separated)         │
│                                                             │
│ [ Run sweep ]   Est. runtime: ~12-15 min for 5 variants     │
└─────────────────────────────────────────────────────────────┘
```

Validation:

- Empty / single value → block submit ("Sweep needs
  ≥ 2 values").
- Each value parsed via field metadata; out-of-range or
  wrong-type shows red helper.
- Duplicates rejected.
- Runtime estimate updates when N changes:
  `N × (period_days / step_days) × 30s`.

### Progress UI

After Run sweep, form collapses; progress panel shows:

```
┌─ Sweep in progress ─────────────────────────────────────────┐
│ Variant 3 of 5 — value=14                                   │
│ [━━━━━━━━━━━━━━━━░░░░░░░░░░░░░░] 47%                        │
│   ├─ Window 8 of 17                                         │
│                                                             │
│ Completed variants:                                         │
│   ✅ cd=3  →  +1.47% PnL, 7.91% DD                          │
│   ✅ cd=7  →  +3.74% PnL, 7.63% DD                          │
│   ⏳ cd=14 (running)                                        │
│   ⏸ cd=21 (queued)                                         │
│   ⏸ cd=28 (queued)                                         │
│                                                             │
│ [ Cancel ]                                                  │
└─────────────────────────────────────────────────────────────┘
```

Polling: `GET /v1/algo/sweep/runs/{id}` every 2-3 s
(matches walk-forward cadence). Drill-in: click a completed
variant row → opens its walk-forward detail page (existing
UI, no new screen).

### Results UI

Three blocks below the (collapsed) form.

**Block A — Per-variant table** (ranked by Sharpe):

| Rank | Value | Trades | Win Rate | Total PnL | Max DD | Sharpe | DSR | Action |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 🏆 | 7 | 83 | 63.9% | +3.74% | 7.63% | +0.648 | 0.62 | [View →] |
| 2 | 14 | 83 | 63.9% | +3.74% | 7.63% | +0.648 | 0.62 | [View →] |

View → opens the variant's walk-forward detail (Quality
Gates strip, equity curves, per-window table — already
exists).

**Block B — Cross-variant PBO badge**:

```
┌─ Cross-variant PBO ─────────────────────────────────────────┐
│ PBO = 0.328  (16 blocks, T=122 days × 5 variants)           │
│                                                             │
│   ✅ Robust      ⚠ At-risk      ❌ Likely overfit            │
│      0.30          0.50              1.00                   │
│           ●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                   │
│                                                             │
│ Verdict: AT-RISK. The rank-1 variant cd=7 is marginally     │
│ robust but partly luck-of-the-slice. Corroborate with a     │
│ longer period (12-24 months) or external priors before      │
│ promoting.                                                  │
└─────────────────────────────────────────────────────────────┘
```

Color band + verdict text matches the sweep script's
markdown output. `InfoTooltip` on PBO label reuses
PR #240's definition tooltip.

**Block C — Overlaid equity curves**:

ECharts line chart, X = bar_date, Y = equity, N lines (one
per variant), distinct colours. Same theming +
`notMerge={true}` + `key={isDark}` pattern as
`WalkForwardEquityCurves`. Legend toggles per variant.

### Promotion path

Below Block B:

```
[ Save winner cd=7 as new strategy ]
```

Clicking opens a confirmation modal:

> "Create new strategy `RSI(2) Connors Daily v3 [sweep-winner cd=7]` with cooldown_after_failed_exit_days = 7? You can edit the name before saving."

On confirm, POST to existing
`/v1/algo/strategies/clone` (template → strategy flow)
with the mutated AST. New strategy appears in the user's
strategies list; **no auto-promotion to paper/live**.

### Sweep history

New row type in the existing "Backtest history" sidebar.
Each sweep row shows date, base strategy, swept field,
N variants, winner, PBO. Click → restore the sweep
results view (read `summary_json` from PG).

### Hook contracts

```ts
// frontend/hooks/useSweepRuns.ts
export function useSweepRuns(): {
    runs: SweepRow[]; isLoading; mutate;
};
export function useSweepRun(id: string): {
    run: SweepResult; isLoading;
};
export async function startSweepRun(
    config: SweepConfig,
): Promise<{ sweep_run_id: string }>;

// frontend/hooks/useSweepableFields.ts
export function useSweepableFields(): {
    fields: SweepableField[]; isLoading;
};
```

All use `apiFetch` + SWR per CLAUDE.md §5.3. Default 2-min
dedup, no revalidate-on-focus, except the progress
endpoint during an active sweep — 3-second
`refreshInterval` until status === `'completed'` |
`'failed'`.

### E2E coverage

New testids:

```
sweep-base-strategy-select
sweep-period-from / sweep-period-to
sweep-train-days / sweep-test-days / sweep-step-days
sweep-field-select
sweep-values-input
sweep-regime-stratified
sweep-submit
sweep-progress-panel
sweep-variant-row-<index>
sweep-pbo-badge
sweep-promote-winner-button
sweep-cancel-button
```

POM: `e2e/pages/frontend/SweepPage.ts` extending
`BasePage`.

## Testing strategy

### Backend unit tests

| Module | Tests |
|---|---|
| `sweep_whitelist.py` | each field's path resolves on a fresh v3 strategy; `validate_swept_values` accepts valid types per field; rejects out-of-range / wrong-type / empty / single-value lists / duplicates |
| `_mutate_ast` | deep-copies (source untouched); each whitelist path resolves on v3 + intraday templates; unknown path raises ValueError |
| `build_returns_matrix` | overlapping dates → correct (T, N); missing dates → common-intersection trimming; zero-variance variant → zero column, no NaN poison; < 2 common dates → empty matrix |
| `variant_equity_curve` | single-window → matches input; two-window → chained values compound correctly; empty input → empty curve |
| `compute_sweep_pbo` | N < 2 returns None; T < 8 returns None; valid R returns Decimal in [0, 1]; NaN result returns None |

### Backend integration tests

In `backend/algo/backtest/tests/test_sweep_runner.py`:

- `test_sweep_runs_serial_and_aggregates` — mock
  `run_walkforward_job` to return canned summaries
  (3 variants × 4 windows). Assert sweep parent row
  has correct `summary_json`: 3 `SweepVariantSummary`
  entries, populated `cross_variant_pbo`,
  `winner_variant_index` points to highest-Sharpe row,
  status `completed`.
- `test_sweep_continues_when_one_variant_fails` —
  variant 2's mock raises RuntimeError. Sweep marks
  variant-2 walkforward row `failed`, continues to
  variants 3, 4. With ≥ 2 survivors, computes PBO on
  survivors. Sweep parent ends `completed`.

### Backend HTTP route tests

In `backend/algo/tests/test_sweep_routes.py`. Use the
lift-to-module-level pattern (handlers delegate to pure
`_impl` functions). Tests:

- `test_post_sweep_run_creates_pending_row`
- `test_post_sweep_run_validates_whitelist_field`
- `test_post_sweep_run_rejects_unknown_strategy`
- `test_post_sweep_run_rejects_single_value`
- `test_get_sweep_run_returns_in_progress_shape`
- `test_get_sweep_runs_lists_user_sweeps_only`
- `test_get_sweep_fields_returns_whitelist`

### Frontend Vitest tests

| Component | Test |
|---|---|
| `SweepForm` | field dropdown reads from mocked `useSweepableFields`; values input parses comma-separated; red helper on bad value; runtime estimate updates; submit disabled when < 2 valid values |
| `SweepResultsTable` | rows ranked by Sharpe descending; 🏆 on rank 1; tied Sharpe values share rank but row order deterministic by `variant_index` |
| `SweepEquityCurves` | N lines render; legend toggles work; `notMerge={true}` + dark-mode key respected |
| Cross-variant PBO badge | verdict text matches value (≤ 0.30 robust, etc.); tooltip content matches PR #240's pattern; `null` PBO renders "N/A — too few common days" |

### E2E (Playwright) smoke

One spec in `e2e/algo-trading/sweep.spec.ts`:

```
Pre-conditions:
  - superuser fixture (storage state)
  - small test universe (3 tickers, 60-day period,
    30/30/30 train/test/step)
  - v3 strategy seeded with cooldown_days=7

Steps:
  1. Navigate to /algo-trading/strategies?tab=backtest
  2. Click "Parameter sweep" sub-tab
  3. Form: select v3, pick "Cooldown (days)",
     enter "5, 10, 15"
  4. Submit
  5. Wait for sweep-progress-panel (≤ 30 s)
  6. Wait for sweep-results-table (≤ 90 s)
  7. Assert 3 rows
  8. Assert rank-1 row has 🏆 emoji
  9. Assert PBO badge visible
 10. Click sweep-promote-winner-button
 11. Confirm modal
 12. Assert new strategy appears in strategies list
```

1 worker local, 2 CI per CLAUDE.md §5.14.

## Process / git

- Branch off `dev`; squash merge per §4.4 #27.
- Co-Authored-By: Abhay (mandatory per §4.4 #24).
- One PR per slice (manifest mirroring the backup
  redesign's structure):
  1. PG migration + Pydantic types
  2. `sweep_whitelist` + `_mutate_ast` + validators
  3. `run_sweep_job` orchestrator + repo extensions
  4. PBO aggregation (`variant_equity_curve` +
     `build_returns_matrix` + `compute_sweep_pbo`)
  5. HTTP routes
  6. Frontend `SweepSubTab` + form + progress UI
  7. Frontend results table + PBO badge + equity-curve
     overlay
  8. Frontend promotion modal + history row
  9. E2E smoke + PROGRESS.md + push + PR

## Open questions answered

| Decision | Choice |
|---|---|
| 1D vs grid sweep | 1D in v1; grid is a follow-up epic |
| Persistence | Option B — extend `algo.runs` with `parent_sweep_id` |
| Concurrency | Serial; parallel as a v2 optimisation if needed |
| Field selection | Curated whitelist of 7 fields |
| Mutation site | In-memory only; PG base strategy untouched |
| Promotion | Manual "Save winner as new strategy" button — no auto-promotion |
| PBO `n_blocks` | Hardcoded 16, fallback 8 for small T |
| Failure tolerance | Continue on per-variant failure; sweep completes if ≥ 2 survivors |

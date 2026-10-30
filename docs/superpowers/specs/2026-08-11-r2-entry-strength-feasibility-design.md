# R2 Entry-Strength Feasibility + Switchable Composite Gate — Design

**Status:** Design approved (feasibility-analysis scope). The
switchable composite gate is designed here but built later, gated on
the feasibility result + a data-readiness criterion.

**Parent:** `docs/superpowers/specs/2026-08-09-algo-intraday-entry-window-design.md`
§8 (Release 2 sketch — the 4-dimension entry-strength composite).

---

## 1. Motivation

Release 1 shipped the timing fix + the one gate we trust (falling-knife
veto) + shadow logging. Release 2 is the **full 4-dimension
entry-strength composite gate**, and the parent spec makes it
**research-gated**: *turn the composite on only if it separates winners
from losers out-of-sample.*

We are not there yet. The labeled dataset is thin (44 settled live
trades, 27W/17L), and the July RSI2 post-mortem already found winners
and losers **largely indistinguishable at entry, with only
MDD-percentile separating**. So the first R2 item is not the gate — it
is an honest **feasibility analysis**: does any dimension separate
winners from losers on the real labeled data, and how strongly?

This spec covers (a) that feasibility analysis — **the immediate
build** — and (b) the full **switchable composite-gate** design it
feeds, built later.

## 2. Scope & sequencing

1. **Feasibility analysis (this build).** A re-runnable CLI over
   `algo.entry_labeled_outcomes` that ranks per-dimension winner/loser
   separation and reports one pre-registered composite headline.
   Output = a report + a plain-English go/no-go read. No gate, no
   thresholds, no simulation.
2. **Composite gate (later, gated).** Ship the gate as a three-state,
   env-toggleable component (`off` / `shadow` / `enforce`), built only
   once the feasibility analysis shows signal worth scoring. Its own
   plan.

The switch (§6) and activation criterion (§7) are designed now so the
path is explicit; **only §3–§5 (the analysis) are implemented in this
build.**

## 3. Data

Single self-contained PG table `algo.entry_labeled_outcomes` (populated
by the daily 16:45 rollup, PRE-1/6). **No joins** — every dimension
feature is already denormalized per trade.

**Cohort:** `mode='live' AND filled AND outcome_settled AND
label_win IS NOT NULL AND NOT dry_run` → 44 rows (27W/17L). Flags:
`--mode {live,paper,all}` (default `live`), `--min-n` guard (default 30,
warns below).

**Features → the 4 dimensions:**

- **Dim 1 — pullback depth / absorption:** `rsi2_at_entry`,
  `dist_sma50_pct`, `dist_sma200_pct`, `ret_1d_prior`, `ret_3d_prior`,
  `ess_absorption_volume_score`, `ess_selling_deceleration_score`,
  `ess_trend_stability_score`
- **Dim 2 — stock strength / resilience:** `qm_score`, `ess_score`,
  `ess_gate_passed`, `qm_mdd_pctile`, `qm_rs_pctile`, `qm_sharpe_pctile`
- **Dim 3 — market breadth / regime:** `breadth_oversold / breadth_total`
- **Dim 4 — falling-knife:** `ret_3d_prior`, `gap_pct`

**Outcome:** `label_win` (bool). Secondary continuous outcomes for
context only: `return_pct`, `mfe_pct`, `mae_pct`.

## 4. Analysis method (approved: A + B)

**A — Univariate separation ranking.** For each feature, split the
cohort by `label_win` and compute:

- **AUC** = Mann-Whitney `U / (n_win · n_loss)` — rank-based, robust at
  tiny n; 0.5 = no separation, →1 or →0 = separation (0<0.5 means the
  feature runs the *opposite* direction).
- **median-split win-rate** — win-rate among the feature's top half vs
  bottom half.
- **Mann-Whitney U p-value** (uncorrected; reported, not gated on).
- **direction** — does higher feature = more winning.

Features are grouped under the 4 dimensions and ranked by |AUC − 0.5|.

**B — Pre-registered composite headline.** ONE composite defined
**a-priori** (fixed here, before looking at results, so it is not
fished), as a sign-aware standardized (z-score over the cohort) sum of
one representative feature per dimension, with directions hypothesized
from the R1 thesis + the RSI2 post-mortem:

```
composite = z(ess_absorption_volume_score)      # Dim1: more absorption = better
          − z(qm_mdd_pctile)                     # Dim2: less-severe-drawdown pctile = better (KEY prior)
          + z(dir3 · breadth_oversold_pct)       # Dim3: weak prior — dir3 fixed here, see note
          + z(ret_3d_prior)                       # Dim4: less-negative prior 3d = not a knife = better
```

Report the composite's AUC + median-split win-rate as the single
headline number. A wrong hypothesized direction surfaces as the
component (or composite) AUC < 0.5 — informative, not fatal.

*Note on Dim3:* breadth's direction is genuinely uncertain
(mean-reversion tailwind vs broad-selloff risk). We fix `dir3 = −1`
(fewer names oversold ⇒ more idiosyncratic dip ⇒ hypothesized better)
a-priori and let the univariate AUC reveal the truth.

## 5. Output & honesty guardrails

`report.md` (+ `feature_ranking.csv`) → `~/.ai-agent-ui/research_runs/
<date>-entry-strength-feasibility/` (mirrors the `intraday_15m_mis_bakeoff`
convention). Sections:

1. **Dataset summary** — n, W/L balance, date span, mode.
2. **★ Caveat banner** (top of report) — "n=44, exploratory,
   IN-SAMPLE, NOT out-of-sample, multiple comparisons uncorrected;
   directional only — do NOT calibrate thresholds from this."
3. **Per-dimension ranking table** — features sorted by |AUC−0.5|.
4. **Composite headline** — AUC + win-rate split.
5. **Go/no-go read** — plain English: is there any dimension worth
   carrying into a properly-powered study, or is entry-strength
   indistinguishable at this n?

**Re-runnable:** the CLI re-runs unchanged as labeled n grows; the
report stamps run-date + n so the trend (and when the switch becomes
safe to flip) is visible over time.

## 6. Switchable composite gate (designed now, built later)

One env knob, `ALGO_ENTRY_STRENGTH_MODE`:

| State | Behavior |
|---|---|
| `off` (default) | No scoring, no effect — current behavior, zero overhead. |
| `shadow` | Score every RSI2≤5 candidate + log (extend `entry_strength_snapshot` with the score); **never blocks or mutates the order path.** Accumulates *scored* outcomes for validation. |
| `enforce` | Reject candidates below the calibrated threshold — `signal_rejected reason=entry_strength_gate`, metric values in payload. |

Fully reversible (flip back any time, no redeploy). `shadow` and
`enforce` MUST honor the R1 safety invariants (§5 of the parent spec):
never touch GTT two-path accounting, budget-reservation release,
STOP_HIT sacrosanct, caps freshness, no silent qty=0 drop. Scoring is
non-gating in `shadow`; the ONLY new rejection path is `enforce`.

## 7. Activation criterion ("enough data to train it")

Move `shadow → enforce` only when **both** hold, verified by re-running
the §4 analysis:

- **(a) Data readiness:** ≥ a documented target of settled labeled
  trades (initial target **300**, revisited as the trend report shows
  variance) with both classes represented.
- **(b) Out-of-sample separation:** the composite separates winners
  from losers OOS (time-split: train on earlier trades, test on later)
  by a set margin (initial target composite test-set AUC ≥ **0.60**).

Until both hold, the gate stays in `shadow` (or `off`). Both thresholds
live in the spec, not code, and are revisited from the trend report.

## 8. Testing (TDD — this build)

Happy + error paths minimum:

1. **AUC correctness** — perfect-separation fixture → AUC 1.0;
   identical distributions → 0.5; reversed → 0.0.
2. **median-split win-rate** — known fixture.
3. **Cohort filter** — selects only `filled AND outcome_settled AND
   label_win NOT NULL AND NOT dry_run`; excludes unsettled / rejected /
   dry-run rows.
4. **Composite** — sign-aware standardized aggregation; a NaN feature
   for one row degrades that row's component, never crashes the run.
5. **`--min-n` guard** — below threshold emits a loud caveat (still
   runs, never silently implies significance).

## 9. Non-goals (YAGNI)

No gate/threshold implementation, no backtest simulation (real-only per
decision), no scheduling, no UI, no model training. Pure analysis →
report. The `off/shadow/enforce` toggle and scoring function are a
**later** build, gated on §7.

## 10. Open questions

- Include the 27 paper trades (3-day burst) as a secondary cohort, or
  live-only? Default live-only; `--mode all` available for a look.
- Continuous-outcome variant (rank by `return_pct` / MFE-MAE) as a
  secondary lens once n grows — deferred.

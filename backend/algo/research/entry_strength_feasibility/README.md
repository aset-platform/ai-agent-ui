# Entry-Strength Feasibility (R2)

Spec: [`docs/superpowers/specs/2026-08-11-r2-entry-strength-feasibility-design.md`](../../../../docs/superpowers/specs/2026-08-11-r2-entry-strength-feasibility-design.md)

## Purpose

Release 1 shipped the timing fix + falling-knife veto + shadow
logging. Release 2's full 4-dimension entry-strength composite gate is
**research-gated**: turn it on only if it actually separates winners
from losers. The July RSI2 post-mortem already found winners/losers
largely indistinguishable at entry (only MDD-percentile separated).

This module is the honest feasibility check, not the gate itself: a
re-runnable CLI over `algo.entry_labeled_outcomes` that ranks
per-dimension winner/loser separation and reports one pre-registered
composite headline. No gate, no thresholds, no simulation.

## Cohort

Rows from `algo.entry_labeled_outcomes` filtered to `filled AND
outcome_settled AND label_win IS NOT NULL AND NOT dry_run`, scoped by
`--mode` (`live` | `paper` | `all`). As of this build the live cohort
is thin: **n=44 (27 win / 17 loss)** — below the `--min-n` default of
30 is *not* the failure mode here; 44 > 30 but is still a small,
in-sample, uncorrected-for-multiple-comparisons sample. Treat every
result as directional.

## Dimensions + pre-registered composite

Four dimensions (`backend/.../dimensions.py::DIMENSIONS`), each mapped
to raw/derived columns on the labeled table:

- `pullback_absorption` — RSI2, distance from SMA50/200, prior 1d/3d
  return, absorption-volume / selling-deceleration / trend-stability
  entry-strength scores.
- `strength_resilience` — quality-momentum score/entry-strength score,
  MDD/RS/Sharpe percentiles.
- `breadth_regime` — % of the universe oversold at entry time.
- `falling_knife` — prior 3d return, entry-day gap.

`COMPOSITE` is a **fixed, pre-registered** weighted z-score (direction
hypothesized *before* looking at results — from the R1 thesis + the
July post-mortem): more absorption-volume = better, less-severe MDD
percentile = better, fewer oversold peers = more idiosyncratic
(better), less-negative 3d return = not a falling knife (better). A
wrong hypothesized direction shows up as component/composite AUC <
0.5 — that is itself a finding, not a bug.

Per-feature stats (`stats.py`): ROC-AUC, median-split win rate
(top-half vs bottom-half), Mann-Whitney U p-value. Orchestration
(`analysis.py`) ranks features by `|AUC − 0.5|` and additionally
scores the composite the same way.

## How to run

```bash
# Tests
docker compose exec -T backend python -m pytest \
    backend/algo/research/entry_strength_feasibility/

# Real run against the live labeled cohort
docker compose exec -e PYTHONPATH=.:backend backend python -m \
    backend.algo.research.entry_strength_feasibility \
    [--mode live] [--min-n 30] [--out-dir DIR]
```

`--mode` selects the cohort (`live` default, or `paper`/`all`).
`--min-n` (default 30) only flips the report's "UNDERPOWERED" banner
— it never blocks the run. `--out-dir` overrides the default output
location.

## Output

Default location: `~/.ai-agent-ui/research_runs/` +
`entry-strength-feasibility/` (via `backend.paths.APP_HOME`; override
with `--out-dir`). Re-running overwrites in place — this is a
point-in-time report, not an appended history.

- `report.md` — primary deliverable: caveat banner, cohort summary,
  composite headline, per-feature ranking table, plain-English
  go/no-go read.
- `feature_ranking.csv` — same ranking, machine-readable.

## The "directional / underpowered" caveat

Every report opens with an explicit banner:
**exploratory, in-sample — not out-of-sample; multiple comparisons
uncorrected; small n; directional only — do NOT calibrate thresholds
from this report.** The go/no-go read at the bottom is a plain-English
recommendation (carry the strongest separator into a properly-powered
OOS study, or accumulate more labeled trades) — never a threshold to
wire into the live composite gate. That gate is designed in the spec
(§6-§7) but explicitly **out of scope for this build**; it ships later,
gated on this analysis showing real signal plus a data-readiness
criterion.

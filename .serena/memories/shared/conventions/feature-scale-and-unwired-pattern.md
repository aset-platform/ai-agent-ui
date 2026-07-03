---
name: feature-scale-and-unwired-pattern
description: Strategy feature catalog conventions — scale field (fraction/percent/ratio) and unwired flag
type: convention
---

# Strategy feature catalog: `scale` and `unwired` conventions

`backend/algo/strategy/features.py`'s `FEATURES` list (mirrored to
`frontend/components/algo-trading/strategyFeatureCatalog.ts`, synced
by `test_feature_registry_sync.py`) carries two conventions every
new feature entry must follow.

## `scale: "fraction" | "percent" | "ratio" | None`

Set explicitly for any feature whose value is percentage-like.
`None` (the default) means "not percentage-related, self-evident
from the label" (raw price, 0-100 oscillator, count, boolean, enum)
— no caption needed.

**Why this exists:** a strategy condition of the form
`distance_from_sma50 > -3` can be a silent no-op if the feature is
actually stored as a decimal fraction (`(close - sma) / sma`, so
`0.05` = 5%) while the threshold is typed as if it were a percentage
(`-3` meaning "greater than -300%" — a value nothing can ever be
below). An audit of the full feature catalog found both conventions
in use for near-identical concepts under similar names (some
`pct_`-prefixed features are fraction-scale despite the name; others
with no `pct` in the name are genuinely percentage-scale) — the name
alone is not a reliable signal of scale.

`scale` drives an inline caption next to the threshold input in the
condition builder (only for literal comparisons) and a hover tooltip
on the read-only feature chip in the AST tree view, so a user typing
a threshold or reviewing an existing strategy never has to guess
which convention a given feature uses.

## `unwired: bool = False`

Set `True` for any feature declared in the catalog (selectable in
the strategy builder) that is NOT YET populated by any of the
runtimes (live/paper/backtest) that assemble per-bar evaluation
context.

**Why this exists:** a feature merely added to the catalog but never
wired into a runtime's evaluation context always causes any
condition referencing it to fail evaluation silently — the strategy
still runs and evaluates its other conditions normally, so nothing
alerts the user that part of their strategy logic can never fire.
`unwired=True` drives a visible warning in the strategy builder
(both in the feature-selection dropdown and inline under the
condition row referencing it) instead of letting a user build a
permanently-dead condition without knowing it.

## Checklist for any new feature

1. Determine its actual computed scale by tracing the real
   evaluation-time resolver in each runtime, not a similarly-named
   computation used elsewhere for a different purpose (e.g. a
   display-only analytics page) — those can use a different
   convention for the same concept.
2. Set `scale` if percentage-like.
3. Confirm the feature is actually populated by at least one runtime
   before shipping it without `unwired=True` — or set the flag if it
   isn't wired yet.
4. Update BOTH `backend/algo/strategy/features.py` and
   `frontend/components/algo-trading/strategyFeatureCatalog.ts` in
   the same change. **`test_feature_registry_sync.py` only asserts
   key-set parity between the two catalogs — it does NOT check that
   `scale`/`unwired` values match.** A `scale`/`unwired` value set on
   only one side passes CI silently and produces a stale UI caption
   or warning. Until that test is extended to cover these fields,
   this step is manual — double-check both files by eye.

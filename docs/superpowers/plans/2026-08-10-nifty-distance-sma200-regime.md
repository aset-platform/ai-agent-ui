# Nifty distance-from-SMA200 regime feature — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Replace the binary `nifty_above_sma200` market-regime gate (currently mis-set to the no-op `>= -5`) with a continuous, percent-scaled `nifty_distance_from_sma200_pct` feature so the strategy trades QM RSI(2) dips as long as Nifty is within a tolerance band of its 200-DMA (default `> -5`), instead of being locked out whenever Nifty is below its 200-DMA.

**Architecture:** Mirror the two existing Nifty market-context features (`nifty_above_sma200` binary via `compute_market_regime`; `nifty_30d_return_pct` percent via `compute_market_trend_strength`). Add a third, computed once per run over `^NSEI` and injected per-bar into `EvalContext.features` across all three runtimes (live/paper via `per_bar.py`, backtest via `runner.py`). Percent scale, matching `nifty_30d_return_pct`.

**Tech Stack:** Python 3.12, pytest (Docker: `docker compose exec -e PYTHONPATH=.:backend backend python -m pytest <path> -v`, self-skips without pyarrow).

## Global Constraints

- Line ≤79; `X | None`; no bare `except`; black/isort unavailable (format by hand); runtime.py carries 14 pre-existing flake8 findings — add ZERO new.
- **Feature-scale rule (algo.md):** the new feature IS percentage-related → `scale="percent"` (value already ×100, e.g. −0.75 means 0.75% below). Threshold literals are therefore in percent (`> -5`), NOT fractions.
- **Wiring rule (algo.md):** a feature added to the catalog but not populated by a runtime hits `signal_rejected missing_feature` forever. This feature MUST be wired into live, paper, AND backtest `EvalContext.features` in the same change (not `unwired=True`).
- Compute uses `^NSEI`, `sma_window=200`, same `load_ohlcv_window` warmup pattern as `compute_market_regime`. Missing/short data → date absent from dict → caller falls back to `Decimal(0)` (same convention as the other two).
- Don't disturb the existing two Nifty features or any Release-1 entry-path code on this branch.

---

### Task 1: Feature — catalog + compute + wire all 3 runtimes

**Files:**
- Modify: `backend/algo/strategy/features.py` (catalog entry, near the other `nifty_*` at ~L155-166)
- Modify: `backend/algo/backtest/indicators.py` (new `compute_market_distance_from_sma200`, near `compute_market_regime`)
- Modify: `backend/algo/features/per_bar.py` (new `market_dist_sma200` param → `out["nifty_distance_from_sma200_pct"]`, mirroring the two existing keys ~L103-108)
- Modify: `backend/algo/live/runtime.py` (compute `self._market_dist_sma200` alongside `_market_regime`/`_market_trend` ~L495-498; pass its per-`bar_date` value into the `assemble_per_bar_features(...)` call)
- Modify: `backend/algo/paper/runtime.py` (same, ~L243-259 init + L752 assemble call)
- Modify: `backend/algo/backtest/runner.py` (compute the dict alongside `market_regime`/`market_trend`; inject at the entry call ~L1195-1198 AND the regime-exit block ~L1017-1021)
- Modify: `backend/algo/strategy/feature_warmup.py` (default `"nifty_distance_from_sma200_pct": 0`, ~L80-81)
- Test: `backend/algo/backtest/tests/test_market_distance_sma200.py` (new) + extend the existing per_bar / runtime wiring tests

**Interfaces produced:**
- `compute_market_distance_from_sma200(period_start, period_end, regime_ticker="^NSEI", sma_window=200, warmup_days=DEFAULT_WARMUP_BARS) -> dict[object, Decimal]` — `{bar_date: (close - sma200)/sma200 * 100}`.
- `assemble_per_bar_features(..., market_dist_sma200: Decimal | None = None)` → sets `out["nifty_distance_from_sma200_pct"]` (defaults `Decimal("0")` when None).
- Catalog key `nifty_distance_from_sma200_pct`, `scale="percent"`.

- [ ] **Step 1: Failing unit test for the compute function**

Mirror `compute_market_regime`'s test: a synthetic `^NSEI` series whose last close is a known % above/below its 200-SMA → assert the returned `{date: pct}` matches `(close-sma)/sma*100` (sign + magnitude), and that a series shorter than the SMA window yields an empty/absent entry.

- [ ] **Step 2: Run it (Docker) — verify it fails** (function undefined).

- [ ] **Step 3: Implement `compute_market_distance_from_sma200`** in `indicators.py`, copying `compute_market_regime`'s structure but emitting the percent distance instead of the 1/0 flag.

- [ ] **Step 4: Add the catalog entry** in `features.py` (percent scale) and the `feature_warmup.py` default `0`.

- [ ] **Step 5: Wire `per_bar.py`** — add `market_dist_sma200` param, set `out["nifty_distance_from_sma200_pct"]` (default `Decimal("0")`).

- [ ] **Step 6: Wire live + paper + backtest callers** — compute the dict once per run (`_cmd`/equivalent) and pass the per-`bar_date` value into each `assemble_per_bar_features(...)` call (live runtime, paper runtime, backtest runner entry) and the backtest regime-exit `market_feats` block.

- [ ] **Step 7: Wiring tests (Docker)** — for each runtime path, assert `nifty_distance_from_sma200_pct` is present in the assembled `EvalContext.features` (mirror how existing tests assert `nifty_above_sma200` is wired), so a strategy referencing it never hits `missing_feature`.

- [ ] **Step 8: Run full suite (Docker), flake8 zero-new, commit** (`feat(algo): nifty_distance_from_sma200_pct market-regime feature`; co-author trailer; no push).

---

### Task 2: Point the v5 template at the new gate + hand off the live-AST change

**Files:**
- Modify: `backend/algo/strategy/templates/rsi2_connors_daily_v5.json` — replace the `nifty_above_sma200 >= 1` compare node with `nifty_distance_from_sma200_pct > -5`.
- Test: extend `backend/algo/strategy/tests/test_template_rsi2_connors_daily_v5.py` to assert the template now gates on `nifty_distance_from_sma200_pct` with op `>` and literal `-5` (and no longer on `nifty_above_sma200`).

- [ ] **Step 1: Update the template JSON** (single compare node swap).
- [ ] **Step 2: Update/repoint the template test** to the new condition; run it (Docker).
- [ ] **Step 3: Commit** (`feat(algo): v5 template uses nifty_distance_from_sma200_pct > -5 regime band`).
- [ ] **Step 4 (hand-off, not code):** produce the exact corrected compare node the user pastes into the builder's "Paste JSON" for their LIVE strategy (`5c4aa66f`), replacing `nifty_above_sma200 >= -5` with `nifty_distance_from_sma200_pct > -5`. Note the live change requires save → re-promote (bypass ok, been live) → restart runtime (drops Kite WS).

## Self-Review
- Coverage: feature exists everywhere it's read (catalog + 3 runtimes + warmup) — no `missing_feature`. Percent scale set. Template + live-AST both addressed. No R1 code touched.
- Deferred: nothing.

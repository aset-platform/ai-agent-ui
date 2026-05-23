# Intraday 15m MIS Bake-Off — Research Design

| | |
|---|---|
| Date | 2026-05-21 |
| Author | Abhay Kumar Singh |
| Status | Draft (design only — no code) |
| Scope | Research spec for a one-shot feature-importance bake-off on 15-min MIS long+short signals over the F&O 200 universe |
| Non-goals | The strategy itself, backtest fees/slippage modeling, walk-forward, promotion, UI, feature backfill |
| Follow-up spec(s) | Strategy v1 backtest (gated on bake-off outcome §6.6) |

## 1. Motivation

The current strategy library has one 15-min template (`bull_momentum_15m_swing.json`) and it is CNC, long-only. We have no MIS long-or-short strategy, despite having all the infrastructure: 15-min intraday bars (`stocks.intraday_bars`, 4 yr × 497 tickers), per-bar materialized features (`stocks.intraday_features`, 49M rows from 2025-11-17 onward), a backtest runner that already supports `15m` cadence + MIS + intraday quality gates + walk-forward + square-off, and a draft→paper→live promotion workflow.

The bottleneck is hypothesis quality. Rather than guess which of the 26 emitted intraday features matter for short-horizon long+short decisions, we run a single XGBoost + SHAP bake-off against vol-normalized 4-bar (~1 hour) forward returns. The deliverable is a ranked list of stable features plus a draft AST candidate that a follow-up spec backtests through the existing runner.

## 2. Scope decisions (locked during brainstorm)

| Question | Decision | Why |
|---|---|---|
| Win condition | Run a feature-importance research bake-off first | Data should pick the strategy shape; we have an existing v4 baseline at 53.6 % so any new strategy must beat that net of fees |
| Universe | F&O 200 only | SEBI bans MIS short outside F&O — research must reflect what we can actually trade |
| Forward-return horizon | Single horizon: 4 bars (~1 hr) | Matches the natural holding period of MIS intraday momentum / mean-reversion in Indian markets; one model, clean SHAP |
| Approach | 3-class XGBoost + SHAP, vol-normalized labels | Smallest experiment that yields an actionable artifact (top-K features + draft AST); B and C archetypes from the brainstorm queued as natural follow-ups |
| Feature backfill | Run bake-off on the existing 6-month coverage as-is, flag regime imbalance in the report | Fastest path to evidence; long-side findings will be honestly tagged as weakly supported because BULL ≈ 2 % of the window |

The 6-month window means "this strategy will work in BULL regimes" is **unsupportable** from this bake-off — and the report will say so. If a follow-up decides we need that answer, the feature-backfill spec is queued.

## 3. Architecture

A read-only research subtree under `backend/algo/research/`. No Iceberg writes, no scheduler job, no admin UI:

```
backend/algo/research/
├── intraday_15m_mis_bakeoff/
│   ├── README.md             — run instructions, last-run pointer
│   ├── universe.py           — F&O 200 ticker loader
│   ├── fno_200.csv           — static snapshot of NSE F&O list (one-time pull)
│   ├── dataset.py            — DuckDB query against stocks.intraday_features
│   │                           + stocks.intraday_bars; pivot EAV→wide; join
│   │                           regime_label from stocks.regime_history
│   ├── labeler.py            — vol-normalized 3-class label function (pure)
│   ├── train.py              — XGBoost 3-class, time-series split, CLI entrypoint
│   ├── shap_eval.py          — SHAP per-class aggregation, asymmetry detection
│   ├── report.py             — Markdown + 3 PNGs → ~/.ai-agent-ui/research_runs/<date>/
│   └── tests/                — smoke + unit + integration tests
└── _shared/
    └── time_split.py         — strict no-shuffle time-series CV helper
```

**CLI**:

```bash
python -m backend.algo.research.intraday_15m_mis_bakeoff.train \
    --train-end 2026-02-28 \
    --threshold 0.5 \
    --seeds 42,43,44,45,46 \
    --out ~/.ai-agent-ui/research_runs/2026-05-21-intraday-15m-bakeoff/
```

**Output**: `~/.ai-agent-ui/research_runs/2026-05-21-intraday-15m-bakeoff/` containing `report.md`, `feature_ranking.csv`, `shap_long.png`, `shap_short.png`, `feature_ranking.png`, `model.json`, `class_balance.csv`, `run_metadata.json`.

The dataset loader reads materialized features straight from `stocks.intraday_features` — not via `backend/algo/features/loader.py` (the in-memory shape used by the runtime). Reading the Iceberg table guarantees we see exactly what the live runner sees and is faster than recomputing.

## 4. Dataset & labels

### 4.1 Source tables

| Table | What we use | Join key |
|---|---|---|
| `stocks.intraday_features` | feature values (EAV — pivot to wide) | `(ticker, bar_open_ts_ns)` |
| `stocks.intraday_bars` | open / close / high / low / volume for label + slippage modeling | `(ticker, bar_open_ts_ns)` |
| `stocks.regime_history` | `regime_label` daily overlay | `bar_date IST` |
| F&O universe | filter to ~200 shortable tickers | static `fno_200.csv` checked into the package |

Single DuckDB session, three views, one join. EAV pivot via `PIVOT (SELECT … FROM intraday_features WHERE ticker IN <FNO>) ON feature_name USING FIRST(feature_value)` → wide pandas frame, ~500 K rows × ~32 cols after filters. Stays in pandas; never touches PG.

### 4.2 Filters applied at load time

1. `interval_sec = 900` (15-min only).
2. `ticker ∈ FNO 200`.
3. `bar_date ∈ [2025-11-17, 2026-05-21]` (current feature coverage).
4. Session bars only: `bar_open_ts_ns` between 09:15 IST and 15:00 IST. Exclude pre-open and the last 30 min — no forward-label room before MIS square-off at 15:15.
5. Warmup: drop the first 8 bars of each `(ticker, day)` — VWAP resets at session open and is noisy for the first ~5 bars, ORB features (`orb_high_15min`, `orb_low_15min`) need at least 1 bar past the opening-range window. 8 bars is a generous buffer; tunable per the report's NaN-rate appendix. Cross-day rolling features (`sma_200`, `ema_50`) are unaffected because their lookback already spans previous sessions.
6. Drop rows where the label window `[t+1, t+4]` crosses a date boundary — no overnight leakage.

### 4.3 Label construction (`labeler.py`, pure function)

```
entry_px  = bar[t+1].open                  # realistic MIS fill at next-bar open
exit_px   = bar[t+4].close                 # exit at close of 4th forward bar (~1 hr hold)
r_fwd     = (exit_px - entry_px) / entry_px

atr_ret   = atr_14[t] / close[t]           # ATR as fraction of price (vol normalizer)
r_norm    = r_fwd / atr_ret                # vol-normalized return

label = LONG  if r_norm >= +0.5
        SHORT if r_norm <= -0.5
        else FLAT
```

Thresholds `±0.5σ` are first-pass and re-tune if class balance is degenerate (each class must be ∈ [15 %, 60 %], see Gate 2 in §6). No look-ahead: every term uses bars strictly later than the signal bar `t`, and `atr_14[t]` is computed up to and including bar `t`.

### 4.4 Feature set (~32 columns)

All 26 intraday features emitted by `backend/algo/features/engine.py` + 3 daily-overlay features (`regime_label`, `sector_rotation_score`, `rs_vs_nifty_15m`). One-hot `regime_label` (BULL/SIDEWAYS/BEAR) and `time_of_day_bucket` (OPEN/MID/CLOSE). No engineered features beyond what FE-3 emits — that is the point of the bake-off.

XGBoost handles NaN natively (skip-emission produces NaN after pivot — feature absent for a given bar). NaN-rate per feature is tracked in the report; any feature with > 40 % NaN in either class is flagged "low-coverage, treat ranking with caution".

### 4.5 Train / test split

| Split | Window | ~Days | Why |
|---|---|---|---|
| Train (fit) | 2025-11-17 → 2026-02-08 | ~60 | Heaviest SIDEWAYS, some BEAR |
| Train (val) | 2026-02-09 → 2026-02-28 | ~15 | Early-stopping watcher |
| Test  | 2026-03-01 → 2026-05-21 | ~57 | Hold-out — touched once at the end |

Strict chronological; no shuffling, no group-K-fold across tickers (we want time generalization, not cross-sectional). Single split, not k-fold — the 6-month window forbids non-overlapping k-fold for time-series data. Split helper lives in `_shared/time_split.py` for later reuse.

### 4.6 Caching / materialization

Nothing materializes to Iceberg from this research. Pandas frames live in-memory for one run; only the report artifacts persist to `~/.ai-agent-ui/research_runs/<date>/`. Re-runs re-pull from Iceberg — cheap at 500 K rows.

## 5. Model

### 5.1 Why XGBoost

- **NaN-native splits**: feature skip-emission produces real NaNs after the EAV pivot; XGBoost picks an optimal default direction per split — no imputation that would smear signal.
- **SHAP TreeExplainer**: `O(TLD²)` exact SHAP, deterministic, runs in seconds on 500 K rows.
- **Class-weighted multiclass**: `sample_weight` handles FLAT-dominance cleanly without resampling.
- **Single-process fit**: 500 K × 32 fits in 4-6 GB RAM, no cluster needed.

LightGBM would also work; XGBoost picked for parity with how we would serve this if a strategy graduates (`xgboost.Booster.save_model` → JSON → load anywhere).

### 5.2 Configuration

```python
xgb.XGBClassifier(
    objective="multi:softprob",
    num_class=3,                     # SHORT=0, FLAT=1, LONG=2 (alphabetical)
    n_estimators=400,
    max_depth=5,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.7,
    min_child_weight=10,             # ≥ 10 bars in a leaf — guards against single-bar overfit
    reg_alpha=0.1,
    reg_lambda=1.0,
    tree_method="hist",
    eval_metric=["mlogloss", "merror"],
    early_stopping_rounds=30,
    random_state=42,
    n_jobs=-1,
)
```

`max_depth=5` keeps SHAP per-feature attributions interpretable (deep trees produce interaction-heavy explanations). `n_estimators=400` with early stopping is the standard "let it run, stop on plateau" pattern; the best iteration goes in the report. `subsample=0.8` + `colsample_bytree=0.7` provide implicit ensemble diversity.

These are starting values, **not** a tuned grid. The bake-off goal is feature ranking, not model performance. If validation logloss flatlines high we report it honestly rather than chase hyperparameters.

### 5.3 Class weights

```python
w = compute_class_weight("balanced", classes=[0, 1, 2], y=y_train)
sample_weight = w[y_train]    # ≈ 2.5× SHORT/LONG, 0.4× FLAT
```

Computed on the train split only. The unweighted confusion matrix is also reported on test — class weights are a training-time correction; the test set tells us the deployment-time class distribution.

### 5.4 Determinism

`random_state=42`, `tree_method="hist"`, `OMP_NUM_THREADS=8` pinned in the CLI entrypoint and stamped in `run_metadata.json`. Two runs on the same machine should produce byte-identical `model.json`.

### 5.5 Out of scope for this section

No hyperparameter tuning (Optuna/Hyperopt), no ensembling, no probability calibration, no serving hooks. Model JSON sits in the run directory as reproducibility documentation, not a runtime artifact.

## 6. SHAP analysis & report

### 6.1 SHAP computation

```python
explainer = shap.TreeExplainer(booster)         # exact tree SHAP
sv = explainer.shap_values(X_test)              # list of 3 arrays, one per class,
                                                 # each shaped (n_rows, n_features)
```

`sv[0]` = SHORT-class contributions, `sv[1]` = FLAT, `sv[2]` = LONG. Computed on the **test split only** (~280 K rows × 32 features × 3 classes). Tree SHAP on hist trees with depth 5 runs in 30-60 s.

### 6.2 Per-feature aggregations

For each feature `f`:

| Metric | Formula | Tells us |
|---|---|---|
| `mean_abs_long` | `mean(|sv[LONG][:, f]|)` | How much the feature matters for long decisions |
| `mean_abs_short` | `mean(|sv[SHORT][:, f]|)` | Same, short side |
| `directional_long` | `mean(sv[LONG][:, f])` | Net push toward LONG (signed) |
| `directional_short` | `mean(sv[SHORT][:, f])` | Net push toward SHORT (signed) |

A feature can have high `mean_abs` and near-zero `directional` — that means it is an *interaction* feature (useful only in combination), not a standalone signal. Standalone candidates have both high magnitude and high `|directional|`.

### 6.3 Asymmetry detection

`asymmetry = mean_abs_long - mean_abs_short`. Three buckets per feature:

- `asymmetry > +0.5 × σ_asym`: **long-side feature** (expect `relative_volume`, `dist_from_vwap_pct` here)
- `asymmetry < -0.5 × σ_asym`: **short-side feature** (expect `gap_pct` negative, `bb_width` expansion here)
- `|asymmetry| ≤ 0.5 × σ_asym`: **symmetric feature** — works both sides (expect `regime_label`, `rsi_5` here)

`σ_asym` is the std of `mean_abs_long - mean_abs_short` across all features in the run. The long/short AST built from this drops symmetric features into both legs and asymmetric ones into their natural leg only.

### 6.4 Visualizations

Three PNGs land in the run directory:

1. **`shap_long.png`** — beeswarm of `sv[LONG]`, top 15 features by `mean_abs_long`. Points colored by feature value (red = high feature value, blue = low) so direction is visible.
2. **`shap_short.png`** — same for `sv[SHORT]`.
3. **`feature_ranking.png`** — two-sided horizontal bar chart. Left = `mean_abs_short`, right = `mean_abs_long`, sorted by total importance. One glance shows asymmetry.

Standard `shap.summary_plot` calls; saved with `matplotlib` to PNG (no notebook dependency).

### 6.5 Report contents (`report.md`)

```
# Intraday 15m MIS Bake-Off — 2026-05-21

## 1. Run metadata
   Date, git commit, data window, row counts (train/val/test),
   class balance per split, threshold used, feature_set_version,
   F&O 200 universe checksum.

## 2. Training summary
   Best iteration, train/val/test mlogloss + merror, 3×3 confusion
   matrices per split (weighted + unweighted), per-class
   precision / recall / F1 on the test split.

## 3. Caveats — READ FIRST
   - Regime imbalance: 94% SIDEWAYS, ~4% BEAR, ~2% BULL.
     Long-side findings are weakly supported; short-side findings
     are well-supported. Quantified by per-regime SHAP means with
     the BULL-bar count reported alongside.
   - NaN coverage per feature; any > 40% flagged ⚠.
   - F&O 200 list is a static snapshot — names that left F&O
     during the window may be present.

## 4. Feature ranking table (~32 rows)
   feature, mean_abs_long, mean_abs_short, asymmetry, bucket,
   NaN-rate (test), directional_long, directional_short.
   Sorted by max(mean_abs_long, mean_abs_short).

## 5. SHAP plots
   Embed the 3 PNGs with one-line captions.

## 6. Draft AST candidates
   For each of the top-K=8 stable features (see Validation §7.2),
   a one-line description of "what value pushes which side"
   derived from the directional signs. Then a draft AST JSON
   matching the bull_momentum_15m_swing.json shape, but:
   product=MIS, long and short legs, conditions populated from
   the top symmetric + asymmetric features. NOT a backtested
   strategy — a hypothesis ready to feed into a later spec.

## 7. Next actions
   Decision matrix (see §7.6): ship to backtest /
   iterate threshold / backfill features / abandon.
```

The "draft AST candidates" section is the artifact that bridges this research spec to the strategy-implementation spec that comes after.

### 6.6 Honesty conventions in the report

- SHAP magnitudes shown to 4 sig figs; never rounded for narrative emphasis.
- The phrase "feature X predicts LONG" is reserved for `(X ∈ stable_features) ∧ (directional_long > 0) ∧ (asymmetry > 0)`. Anything weaker uses hedged language ("X is associated with LONG predictions").
- Test-set numbers headline the report; train-set numbers go in an appendix. The temptation to report train accuracy is killed at the spec level.

### 6.7 Artifacts written

```
~/.ai-agent-ui/research_runs/2026-05-21-intraday-15m-bakeoff/
├── report.md
├── feature_ranking.csv
├── shap_long.png
├── shap_short.png
├── feature_ranking.png
├── model.json
├── class_balance.csv
└── run_metadata.json
```

Nothing lands in Iceberg, PG, or Redis.

## 7. Validation — sanity gates

The bake-off output is worthless if it reflects a quirk of the train/val draw, a leak, or a broken join. Seven gates run in `train.py` before the report is allowed to print "feature ranking" — any failure of a hard gate (1, 3, 6) raises before training; any failure of a soft gate (2, 4, 5, 7) prints a red banner as the **first line of `report.md` §3 (Caveats — READ FIRST)** and demotes the ranking to "exploratory only" for that section.

### 7.1 Gate matrix

| # | Gate | Pass criterion | Action on fail |
|---|---|---|---|
| 1 | Chronology assert | `max(train.bar_ts) < min(val.bar_ts) < min(test.bar_ts)` strictly | Raise — bug, not a soft fail |
| 2 | Label distribution | Each class ∈ [15 %, 60 %] on train | Adjust σ-threshold in 0.1 steps within [0.25, 1.0]; re-run from labeler; abort + report if no setting works |
| 3 | Leak audit | `max(|Pearson(feature, y_int)|) < 0.5` on train | Hard fail; list offending features, halt before training |
| 4 | Random-baseline floor | Test `mlogloss < stratified_random.mlogloss − 0.05` | Mark report "model did not beat random — feature ranking suppressed" |
| 5 | Ranking stability | Across 5 seeds, top-8 features by `mean_abs_long + mean_abs_short` overlap ≥ 6/8 | Top-K reduced to the stable subset; unstable features go to an "unstable, do not act on" appendix |
| 6 | Harness self-test | Synthetic 5K-row dataset with one feature linearly driving the label — harness must rank that feature #1 with `mean_abs ≥ 3×` any other | Hard fail; bug in the pipeline, not the data |
| 7 | Per-regime power | For each regime bucket on the test split, report mlogloss + count; any bucket with < 500 rows tagged "underpowered" | Soft — stamps caveats, does not block |

Gates 1, 3, 6 are hard fails — code or data bugs. Gates 2 and 5 self-heal by re-running with adjusted params. Gates 4 and 7 are soft — they degrade the report's claims rather than block it.

### 7.2 Ranking-stability detail (Gate 5)

Gate 5 is the most consequential — it is what separates "we found a signal" from "we found noise that happened to be top-ranked".

```python
rankings = []
for seed in (42, 43, 44, 45, 46):
    booster = xgb.train(params | {"random_state": seed}, dtrain, ...)
    sv = TreeExplainer(booster).shap_values(X_test)
    importance = np.abs(sv[0]).mean(0) + np.abs(sv[2]).mean(0)   # symmetric
    rankings.append(set(importance.argsort()[-8:]))

stable_features = set.intersection(*rankings)
mostly_stable   = {f for f in set.union(*rankings)
                  if sum(f in r for r in rankings) >= 4}
```

The report's "top-K candidates for AST" section uses **`stable_features`** only. `mostly_stable - stable_features` goes into the "promising but watch" appendix. Anything outside `mostly_stable` does not appear in actionable sections — only in the full ranking table with a 🟡 / 🔴 marker.

5× training cost (~20 min vs ~4 min) is the price for honesty. Cheap.

### 7.3 Pre-training data assertions (`dataset.py`)

```python
assert df["entry_px"].notna().all(),   "t+1 open missing"
assert df["exit_px"].notna().all(),    "t+4 close missing"
assert (df["atr_14"] > 0).all(),        "ATR_14 zero — divide-by-zero risk"
assert df.duplicated(["ticker", "bar_open_ts_ns"]).sum() == 0, "pivot duplicates"
```

Each failure prints a row sample to stderr before raising — fast iteration when the data is wrong.

### 7.4 Unit tests

| File | What it tests |
|---|---|
| `tests/test_labeler.py` | Pure-function tests. Hand-built bar sequences hitting LONG / FLAT / SHORT / boundary at exactly `±0.5σ` / NaN ATR / zero ATR / negative price. 100 % line coverage of `labeler.py`. |
| `tests/test_time_split.py` | Helper never overlaps; raises on shuffled input. |
| `tests/test_dataset_shape.py` | Integration test with a 50-row in-memory pyarrow fixture (no Iceberg). EAV-pivot produces expected wide columns; join aligns on `(ticker, bar_open_ts_ns)`. |
| `tests/test_gate6_harness.py` | Synthetic labelled data with one feature driving y; assert ranking puts it #1. Runs in < 5 s. |

No test for SHAP itself — that is library code. We test our aggregation logic on a frozen SHAP-output fixture instead.

### 7.5 Reproducibility ledger (`run_metadata.json`)

Every run stamps:

```json
{
  "git_commit": "<sha>",
  "dirty_tree": false,
  "started_at_ist": "2026-05-21T...",
  "data_hashes": {
    "intraday_features": "<MD5 of (count, MIN(bar_ts), MAX(bar_ts), feature_set_version)>",
    "intraday_bars":     "<same>",
    "regime_history":    "<same>",
    "fno_200_csv":       "<sha256 of CSV>"
  },
  "hyperparams": { "...full xgb config..." },
  "threshold_used": 0.5,
  "threshold_adjusted_from_default": false,
  "gates": { "1_chronology": "pass", "...": "..." },
  "stable_features": ["...", "..."],
  "test_mlogloss": 0.987,
  "random_baseline_mlogloss": 1.098
}
```

If a re-run produces a different ranking, the `data_hashes` diff explains why — same commit + same data hashes + same hyperparams ⇒ identical output, or there is a non-determinism bug worth fixing.

### 7.6 Decision matrix in `report.md` §7

The "Next actions" section produces one of four outcomes:

1. **Ship to backtest** — top stable features + draft AST look actionable. Next spec: `2026-XX-XX-intraday-15m-mis-strategy-v1-design.md` — backtests the draft AST through the existing runner with realistic MIS fees + slippage, walk-forward, promotion gates.
2. **Re-tune threshold and re-run** — class balance suggested σ-threshold wrong. No new spec; just re-run.
3. **Backfill features and re-run** — long-side findings unsupported, BULL coverage is the blocker. New spec: feature backfill window expansion.
4. **Abandon** — Gate 4 failed, no signal. Document the negative result and log a project memory so we do not repeat the same hypothesis.

Outcome 1 is the goal; outcomes 2-3 are common and cheap; outcome 4 is honest.

## 8. End-to-end run procedure

### 8.1 Three modes

| Mode | Purpose | Runtime |
|---|---|---|
| `--smoke` | Synthetic 5K-row fixture from Gate 6. Exercises full pipeline without Iceberg. CI-friendly. | < 30 s |
| `--dry-run` | Real Iceberg data, 3 tickers (`RELIANCE.NS`, `HDFCBANK.NS`, `INFY.NS`), 2 weeks. Validates the EAV pivot, joins, label distribution. | ~1 min |
| (default) | F&O 200, full 6-month window, 5 seeds, full report. | ~25 min |

`--smoke` runs as part of `pytest backend/algo/research/intraday_15m_mis_bakeoff/tests/`. Dry-run is a manual command before any full run when iterating on the labeler or dataset code.

### 8.2 Happy path

```bash
# 1. Sanity — Iceberg current, no stale-cache regression (ASETPLTFRM-429)
docker compose exec backend python -c \
  "from backend.db.duckdb_engine import query_iceberg_df; \
   print(query_iceberg_df('stocks.intraday_features', \
       'SELECT MAX(bar_date) FROM intraday_features'))"

# 2. Smoke
docker compose exec backend python -m pytest \
    backend/algo/research/intraday_15m_mis_bakeoff/

# 3. Dry-run on 3 tickers
docker compose exec backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train --dry-run

# 4. Full run
docker compose exec backend python -m \
    backend.algo.research.intraday_15m_mis_bakeoff.train \
    --train-end 2026-02-28 \
    --threshold 0.5 \
    --seeds 42,43,44,45,46 \
    --out ~/.ai-agent-ui/research_runs/2026-05-21-intraday-15m-bakeoff/

# 5. Read report.md, decide next step per §7.6
```

Steps 2 and 3 are mandatory before step 4.

### 8.3 Failure-mode playbook

| Symptom | Likely cause | Fix |
|---|---|---|
| Gate 1 hard fails | Bug in `_shared/time_split.py` or unsorted input | Re-sort, re-test |
| Gate 2 keeps re-tuning σ but never settles | Window too narrow (one low-vol week dominates) — usually only on `--dry-run` | Expand `--dry-run` window before debugging |
| Gate 3 leak audit flags `time_of_day_bucket` | One-hot label-aware column accidentally retained | Audit feature whitelist; remove offender |
| Gate 4 loses to random baseline | Labels are noise (threshold too tight) or features genuinely do not predict 1 hr returns at 15 m | Report "no signal found" — valid outcome |
| Gate 5 stability < 6/8 | High-variance ranking; too few samples per class or too many irrelevant features | Reduce `n_estimators` to force harder selection; if still unstable, report + stop |
| OOM at `xgb.train` | F&O 200 × 6 months overran 8 GB container | `--tickers-cap 100`, or `tree_method="hist"` with `max_bin=128` |
| Dataset pivot creates duplicate `(ticker, bar_ts)` rows | Multiple `feature_set_version` rows in Iceberg | `WHERE feature_set_version = (SELECT MAX(feature_set_version) FROM intraday_features)` |

Each fix is reproducible from `run_metadata.json` — loop is read metadata → diagnose → patch code → re-dry → re-full.

### 8.4 CI vs local

| | CI | Local-only |
|---|---|---|
| `--smoke` unit tests | ✅ | |
| Dry-run on real Iceberg | ❌ (no Iceberg in CI) | ✅ |
| Full run | ❌ | ✅ |
| Linting + type-checks | ✅ | ✅ |

Research code, not prod. We do not gate merges on a 25-min run.

### 8.5 Artifact lifecycle

Run directories accumulate at `~/.ai-agent-ui/research_runs/`. No automatic cleanup job in scope of this spec — manual `rm -rf` is acceptable for now. Each run dir is self-contained; if a run feeds a strategy that ships, `report.md` is copied into the strategy's PR description and the run dir becomes archive.

## 9. Out of scope (explicit non-goals)

- The actual strategy AST (this spec produces a draft, not a backtested strategy).
- Backtest fees / slippage modeling for MIS specifically.
- Walk-forward on the eventual strategy.
- Promotion (draft → paper → live).
- Any UI surfacing of the report.
- Feature backfill for older bars (queued as a follow-up spec if Outcome 3 fires).
- SHAP interaction values (`shap_interaction_values`) — flag as v2 if interaction-only bucket is interesting.
- Hyperparameter tuning.

## 10. References

- `backend/algo/features/engine.py` — canonical 26-feature emitter
- `backend/algo/features/backfill.py` — on-demand feature backfill (used by Outcome 3 follow-up)
- `backend/algo/strategy/templates/bull_momentum_15m_swing.json` — shape reference for draft AST
- `backend/algo/backtest/runner.py` — destination for the draft AST in the follow-up strategy spec
- ASETPLTFRM-402 — feature-engine epic (FE-1 .. FE-14)
- ASETPLTFRM-429 — DuckDB stale-cache fix (sanity check in §8.2 step 1)
- CLAUDE.md §5.16, §5.10 — strategy promotion workflow + feature skip-emission contract

# Daily Feature Backfill — ASETPLTFRM-432

| | |
|---|---|
| Date | 2026-05-23 |
| Ticket | ASETPLTFRM-432 |
| Branch | `feature/persist-daily-features` |

## Summary

The daily feature engine (`backend/algo/features/daily_engine.py`)
emits 21 features including `rsi_2`, `sma_5`, `distance_from_sma5`
referenced by RSI(2) Connors v3 strategy (shipped via PR #231).
The persistence job (`daily_features_daily_compute.py`) auto-writes
ALL emitted features to `stocks.intraday_features` at
`interval_sec=86400` — no whitelist in the writer.

The gap: the Iceberg table only had a ~6-month window
(2025-11-17 → 2026-05-21) — historical bars before that date
weren't covered. This made UI feature explorer + historical
research blind to the v3 strategy's input features.

Resolution is **operational**, not code. The job is correct; just
needed to run it across historical windows.

## What this PR adds

- Regression test `test_daily_engine_v3_features.py` (4 tests) that
  asserts the engine continues emitting `rsi_2`, `sma_5`,
  `distance_from_sma5` after warmup. Guards against accidental
  removal of the emit lines.
- This research doc capturing the backfill procedure.

## Backfill procedure (operational)

Run the `daily_features_daily_compute` job with explicit period
bounds:

```python
import asyncio
from backend.algo.jobs.daily_features_daily_compute import (
    run_daily_features_daily_compute_job,
)

# Backfill any historical window
result = asyncio.run(run_daily_features_daily_compute_job(payload={
    "period_start": "2024-01-01",
    "period_end": "2025-11-16",
    "warmup_days": 320,
    "batch_size": 50,
}))
print(result["status"], result["rows_written"])
```

Batches of 50 tickers; the job is restartable (NaN-replaceable
upsert), so re-running is safe.

## Backfill results (operational)

### 2024-01-01 → 2025-11-16 (completed today)

```
universe_size: 499
tickers_processed: 493
tickers_failed: 0
rows_written: 4,278,560
status: ok
```

Post-backfill verification:
```
distance_from_sma5  n=212,642  2024-01-01..2025-11-14
rsi_2               n=212,750  2024-01-01..2025-11-14
sma_5               n=212,642  2024-01-01..2025-11-14
```

All 3 v3 features now queryable for that window.

### 2018-01-01 → 2023-12-31 (in flight at PR open)

Historical extension running in background. Same job invocation
with the earlier window. Completion logged to
`/tmp/daily_features_backfill_2018_2023.log`. Expected ~8-12
minutes for ~500 tickers × 6 years of bars.

### Going forward

The nightly scheduler-registered job continues to write incremental
rows on its default trailing-window cadence. The 2018-2025 backfill
unifies historical + ongoing coverage.

## Feature count

| Snapshot | distinct features |
|---|---:|
| Before today's backfill | 18 |
| **After 2024-2025 backfill** | **21** |
| (Will stay 21 after 2018-2023 backfill — same engine emits same keys) |

The 3 new features are: `rsi_2`, `sma_5`, `distance_from_sma5`.

## What's NOT in this PR

- **No code change to the engine** — it already emits these
  features (added in PR #231).
- **No code change to the persistence job** — it auto-writes all
  emitted features.
- **No new AST features** — the v3 strategy already references
  them; the engine + persistence just now have the data foundation
  to support querying historical values.

The work was operational: kick off the backfill, verify the rows
landed, ship a small regression test as a guard.

# Intraday bars retention — monthly cadence + partition-aligned cutoff

## Problem

The `intraday_bars_retention` step of the Intraday Bars Daily
Pipeline takes ~16 min wall-clock every day. Breakdown observed
on 2026-05-14:

| Phase | Wall time |
|---|---:|
| `backup_table()` (rsync 536 MB intraday_bars tree) | 957 s |
| Iceberg `delete(LessThan("bar_date", cutoff))` | 17 s |
| Total | 974 s |

The delete is fast. The backup-before-delete is the bottleneck.
Worse: on most days the cutoff hasn't even advanced past a new
whole month (the cutoff was anchored to the SAME day-of-month
4 years ago), so we're paying 16 min/day for an effective no-op.

## Fix (PR #222, open at session-save time)

### 1. Cutoff = first-of-month

`_retention_cutoff(today, years)` returns
`date(today.year - years, today.month, 1)`. Partition-aligned
with the `(ticker, year_month)` layout, so the Iceberg
`delete(LessThan("bar_date", cutoff))` drops WHOLE-MONTH
partitions as a metadata-only operation, not a mid-partition
row-level rewrite.

Side benefit: leap-year edge case disappears (cutoff is always
day=1, so 29 Feb → 1 Feb has no `ValueError`).

### 2. Monthly-cadence gate

`_already_ran_this_month(today)` queries `scheduler_runs` for
the latest successful `intraday_bars_retention` and returns
True iff its `started_at` falls in the current IST calendar
month. The job short-circuits with
`status='skipped_already_ran_this_month'` BEFORE the expensive
`backup_table()` fires.

Detection is by QUERY rather than `today.day == 1` so the
first-of-month fire still happens when the 1st falls on
Sat/Sun (daily pipeline only runs mon-fri):
- 1st of month is a weekday → fires that day
- 1st is Sat/Sun → next Mon (2nd/3rd) becomes the "first
  successful run of the new month" and retention fires
- Re-runs within the same month → no-op

### 3. Force payload bypass

`payload={"force": True}` bypasses the gate for ad-hoc operator
runs from the CLI / Strategies tab manual trigger.

## Operational impact

| Metric | Before | After |
|---|---:|---:|
| Daily pipeline retention step wall clock | ~16 min × 22 days | ~16 min × 1 day |
| Retention runs / year | 264 | 12 |
| Iceberg snapshot churn | 264 delete commits | 12 delete commits |
| Storage retention horizon | 48 months | 48–49 months |

≈ **70 hr/yr** of pipeline wall clock + I/O reclaimed.

## Cron is unchanged

The pipeline still fires mon-fri 15:45 IST. The retention step
is still its #2 step. The behaviour change is internal:
the step short-circuits when it's already run in the current
calendar month.

## Tests

`backend/algo/jobs/tests/test_intraday_bars_retention.py` — 16
tests pass:
- `_retention_cutoff` returns first-of-month
- Robust to run-day-within-month
- Leap-year edge no longer needed (day-1 always valid)
- Monthly gate short-circuits BEFORE `backup_table()` (asserts
  zero backup_calls)
- `payload.force` bypasses the gate
- Iceberg roundtrip seeds a 2022-04-30 row to confirm the
  whole April-2022 partition is dropped at the 2022-05-01
  cutoff while the 2022-05-01 row survives
- Idempotent re-run is no-op
- All cutoff tests updated to expect `2022-05-01` rather than
  `2022-05-13`

Autouse fixture `_bypass_monthly_gate` defaults the gate to OFF
so existing delete-path tests don't need to know about it.

## Files

- `backend/algo/jobs/intraday_bars_retention.py`
- `backend/algo/jobs/tests/test_intraday_bars_retention.py`

## Cross-refs

- PR #222 (https://github.com/aset-platform/ai-agent-ui/pull/222)
- ASETPLTFRM-400 slice 1g (original daily-retention seed)
- `iceberg-orphan-sweep-design` memory (orphan reclamation
  by maintenance which compacts the now-smaller table)

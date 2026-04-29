# Recommendation outcomes job — self-healing window-match

The daily `recommendation_outcomes` job in `backend/jobs/executor.py` had a strict `created_at = today − N ± 2 days` window-match that silently dropped outcomes when:
- The job didn't run on the right day (pipeline failure, container restart, etc.)
- The horizon was added later (the recs were already past the window when the job started looking)
- The rec aged past `target_date + 2d` between job runs

## Original (broken) logic

```python
for days in (30, 60, 90):
    target = today - timedelta(days=days)
    w_start = target - timedelta(days=2)
    w_end = target + timedelta(days=2)
    q = sa_select(RecModel).where(
        ...
        func.date(RecModel.created_at).between(w_start, w_end),
        RecModel.id.notin_(existing),  # not yet checked at this horizon
    )
```

A rec issued on April 18 had its 30-day window of (May 16, May 20). If the job didn't pick it up exactly during those 5 days, the outcome was never computed — the rec aged past the window forever.

Worse: when 7d horizon was added on April 29, `today - 7 = April 22 ± 2 = April 20-24`. Apr 18 was already outside. The new horizon could **never** retroactively cover existing recs.

## Fixed (self-healing) logic

```python
for days in (7, 30, 60, 90):
    cutoff = today - timedelta(days=days)
    q = sa_select(RecModel).where(
        ...
        func.date(RecModel.created_at) <= cutoff,  # at least N days old
        RecModel.id.notin_(existing),  # not yet checked at this horizon
    )
```

Now: any rec ≥ N days old that lacks an outcome at horizon N is picked up on the next run. Idempotency comes from the `id.notin_(existing)` subselect, not from a tight date window.

## Pricing must use target-date close, not latest

With the strict window, the prior code computed return using *today's latest close* — close enough because the rec was always ≈ N days old. With self-healing, a 100-day-old rec being checked at the 30d horizon would have its return computed at *today's* close, not at day-30. Wrong by orders of magnitude.

Fix: compute `target_check_date = created_at + N days` per rec; fetch OHLCV close on that date (or first trading day after, ±6d forward scan for weekends/holidays).

```python
async def _get_due():
    for days in _HORIZONS:
        ...
        for r in q.scalars().all():
            results.append({
                "id": r.id,
                "ticker": r.ticker,
                "created_date": r.created_at.date(),
                "days_due": days,
                ...
            })

# Batched OHLCV fetch covering all (ticker, target) pairs:
targets = [
    min(r["created_date"] + timedelta(days=r["days_due"]), today)
    for r in due_recs
]
df = query_iceberg_df("stocks.ohlcv",
    f"SELECT ticker, date, close FROM ohlcv "
    f"WHERE ticker IN ({tickers}) "
    f"  AND date BETWEEN DATE '{d_min}' AND DATE '{d_max}'")
close_map = {(row.ticker, row.date.isoformat()): row.close for ...}

def _resolve_close(ticker, target):
    for offset in range(7):  # ±6d forward scan
        d = target + timedelta(days=offset)
        if d > today: break
        v = close_map.get((ticker, d.isoformat()))
        if v is not None: return v, d
    return None, None
```

## price_at_rec NULL — separate engine bug

The 41 existing recs from April 18 had `price_at_rec = NULL` (recommendation engine wasn't populating it). The outcomes job correctly skipped them, hence the symptom "tooltips empty even though recs exist". Backfilled out-of-band via:

```python
# For each rec with NULL price_at_rec, look up OHLCV close on
# created_at::date (or next trading day) and UPDATE.
```

Permanent fix should be in the engine itself — when generating a rec, fetch + persist `price_at_rec` from the latest OHLCV close at issue time. Tracked as Sprint 9 follow-up.

## benchmark_return_pct hardcoded 0

Pre-existing TODO in the executor — `bench_return = 0.0`, so `excess_return_pct = return_pct`. This means hit-rate ("excess > 0") is currently equivalent to "return > 0" — no real benchmark comparison. Tracked separately. The outcomes overhaul didn't fix this because it's a different concern (which index to compare to: Nifty for India recs, S&P for US recs?).

## Real incident

2026-04-29: User reported the new Performance tab showing empty charts despite 11-day-old cohort. Trace:
1. Outcomes job had no 7d horizon → no 7d outcomes for any age cohort.
2. Strict window-match meant adding 7d to the executor wouldn't backfill the 11-day-old recs.
3. Self-healing fix + manual job trigger → 41 outcomes inserted at 7d horizon.
4. Performance tab now shows W16: hit_rate_7d=60%, avg_return_7d=−0.89%.

## See also

- `shared/architecture/recommendation-performance-tab` — the consumer.
- `shared/architecture/recommendation-engine` — pre-existing recommendation flow doc.

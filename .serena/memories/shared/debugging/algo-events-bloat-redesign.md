# algo.events Iceberg Bloat — Root Causes & Redesign

## Symptom
Live trading page unresponsive ("stuck loading" — positions / postbacks /
budget never refreshed). `algo.events` had grown to **8.2 GB across ~22k
files for ~50 MB of actual data** (~99% Iceberg snapshot-chain metadata).

See `mem:shared/debugging/iceberg-deep-manifest-compaction-freeze` for the
compaction-freeze mechanics. This memory covers the write-path redesign that
prevents recurrence.

## Root Causes (verified)

1. **High-frequency WS observability events written to Iceberg.**
   `ws_backpressure_drop` fired ~50/s under the ~800-token tick firehose
   (~260k rows). WS lifecycle events (connect/disconnect/auth/gap/backpressure)
   are 7-day noise and do not belong in a durable OLAP commit log.

2. **Per-signal immediate flush.** `LiveRuntime._flush_events_now()` called
   after every `signal_generated` → one Iceberg commit per signal.

3. **`write.metadata.delete-after-commit.enabled` unset (default false)** →
   old `metadata.json` files never pruned; snapshot chain grew unbounded
   (the literal 7.7 GB of metadata).

4. **`algo_events_retention` job registered but never scheduled** — the
   weekly pipeline seed was never applied to PG, so retention never ran.

## Redesign (four parts)

### 1. WS events → Redis (not Iceberg)
WS lifecycle + backpressure events leave Iceberg entirely:
- Per-user Redis sorted set `algo:ws-events:{user_id}` (7-day TTL, 1k cap)
- `backend/algo/broker/ws_event_store.py` — write helper, no-op if Redis absent
- `/events?mode=live-ws` reads Redis sorted set
- `ws_backpressure_drop` aggregated per (user, strategy) → one summary event
  per 60s window; first drop emits immediately

### 2. Periodic flush (no more per-event commits)
`LiveRuntime`: removed all 5 per-event `_flush_events_now()` calls.
Added `_periodic_event_flush` (5s cadence, env `ALGO_EVENT_FLUSH_INTERVAL_S`)
started/cancelled in `run()`; terminal flush on shutdown unchanged.
Real Kite fills unaffected (separate webhook path).

### 3. Metadata pruning enabled at table init
`iceberg_init.create_algo_tables()` sets on `algo.events`:
- `write.metadata.delete-after-commit.enabled = true`
- `previous-versions-max = 20`
(Idempotent — safe to re-run.)

### 4. Retention enrolled in scheduled pipeline
Weekly Long-Tail pipeline (Sun 03:00 IST): retention → maintenance
including `algo.events`. Enrollment regression test added:
`algo.events ∈ ALL_TABLES` + `_HOT_ICEBERG_TABLES`.

## Operational notes

- **Never run heavy `algo.events` maintenance during a live session** — commit
  contention with the order-fill writer can delay real Kite postbacks.
- `file:////` (quad-slash) "Failed to delete metadata file" warnings from
  `cleanup_orphans_v2` are benign — the sweep still unlinks via clean `Path`s.
- `cleanup_orphans_v2` stalls on macOS Docker bind-mount under concurrent
  writes; run at off-hours / idle backend.
- Prevention rule: never write high-frequency or short-retention observability
  data to Iceberg — use Redis (CLAUDE.md §5.13). Every write-heavy table needs
  `delete-after-commit=true` + retention enrolled (not just `@register_job`).

## Related
- `mem:shared/debugging/iceberg-deep-manifest-compaction-freeze`
- `mem:shared/conventions/iceberg-maintenance-enrollment`
- CLAUDE.md §4.3 rules 21 + 22

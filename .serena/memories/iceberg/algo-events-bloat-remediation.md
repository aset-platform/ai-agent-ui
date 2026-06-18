# algo.events Iceberg Bloat — Incident & Remediation (2026-06-18)

## Symptom
Live trading page unresponsive ("stuck loading" — positions / postbacks /
budget never refreshed). Root: every read scans `algo.events`, which had
grown to **8.2 GB across ~22k files for ~50 MB of actual data** — i.e.
~99% Iceberg snapshot-chain metadata. A delete attempt on the table
**delayed a real Kite order postback** (commit contention with the
order-fill writer) — never run heavy `algo.events` maintenance during a
live session.

## Root causes (all verified)
1. **High-frequency observability events written to Iceberg.** The WS
   multiplexer wrote one `algo.events` row per `ws_backpressure_drop`
   (~50/s under the ~800-token firehose; 260k rows). WS lifecycle events
   (connect/disconnect/auth/gap/backpressure) are 7-day noise and do not
   belong in a durable OLAP commit log.
2. **Per-signal immediate flush** in `LiveRuntime` (`_flush_events_now()`
   after every `signal_generated`) → one commit per signal.
3. **`write.metadata.delete-after-commit.enabled` unset (default false)**
   → old `metadata.json` files never pruned; the chain grew unbounded
   (this is the literal 7.7 GB of metadata).
4. **`algo_events_retention` registered but never scheduled** — its
   `Weekly Long-Tail Iceberg Maintenance` pipeline seed
   (`scripts/seed_weekly_longtail_maintenance.py`) was never applied to
   PG (`pipelines`/`scheduled_jobs`), so retention never ran.

## Diagnosis technique (reusable)
- Split **data vs metadata**: count `*.parquet` vs `*.metadata.json` vs
  `*.avro` under the table dir + `len(tbl.metadata.snapshots)`. 99%
  metadata ⇒ snapshot-chain / metadata-retention problem, not data.
- Reads degrade (not hang) once writes stop — the flood's constant
  `invalidate_metadata` forced every read cold. Stopping the writer is
  the first fix, before any reclaim.

## Remediation shipped (PR1–PR4, branch chore/serena-memory-2026-06-18)
- **PR1** `ws_multiplexer`: aggregate `ws_backpressure_drop` per
  (user, strategy) → one summary event / 60s window (first drop emits
  immediately). Set `algo.events` `write.metadata.delete-after-commit.
  enabled=true` + `previous-versions-max=20` (idempotent in
  `iceberg_init.create_algo_tables`).
- **PR2** WS lifecycle events leave Iceberg → per-user Redis sorted set
  `algo:ws-events:{user_id}` (7-day TTL, 1k cap, no-op if Redis absent);
  `backend/algo/broker/ws_event_store.py`; `/events?mode=live-ws` reads
  Redis. Runtime-verified: 0 new `mode=live-ws` Iceberg rows.
- **PR3** `LiveRuntime`: removed all 5 per-event `_flush_events_now()`
  calls; added `_periodic_event_flush` (5s cadence, env
  `ALGO_EVENT_FLUSH_INTERVAL_S`) started/cancelled in `run()`; terminal
  flush unchanged. Real Kite fills unaffected (separate webhook path).
- **PR4** seeded the Weekly Long-Tail pipeline (Sun 03:00 IST: retention
  → maintenance incl. `algo.events`); added enrollment regression test
  (`algo.events ∈ ALL_TABLES` + `_HOT_ICEBERG_TABLES`); one-time
  `cleanup_orphans_v2` reclaim (snapshots → 5, verified).

## Gotchas / notes
- `file:////` (quad-slash) "Failed to delete metadata file" warnings are
  **benign**: `_normalize_uri` collapses them and the orphan sweep
  unlinks via clean `Path`s; only PyIceberg's *internal* expire/commit
  delete hits the malformed SQLite-catalog `file://` path. Reclaim still
  works (sweep deleted 217 files, read-verified).
- `cleanup_orphans_v2` stalls on macOS Docker bind-mount when the table
  is huge + a live writer contends; it runs in ~7s once idle and the
  snapshot count is small. Defer reclaim to off-hours / idle backend.
- **Prevention rule:** never write high-frequency or short-retention
  observability events to Iceberg — use Redis (per §5.13). Every
  write-heavy table needs `delete-after-commit=true` +
  `previous-versions-max` + retention enrolled in a *scheduled*
  pipeline (not just `@register_job`). See CLAUDE.md §4.3 #21/#22.

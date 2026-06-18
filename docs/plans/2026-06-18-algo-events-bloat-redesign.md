# algo.events Bloat — Redesign & Implementation Plan

**Status:** proposed · **Owner:** Abhay · **Created:** 2026-06-18
**Trigger incident:** live page unresponsive — `algo.events` grew to
8.2 GB across ~22k files (99% Iceberg snapshot-chain metadata, ~50 MB
actual data). WS-multiplexer backpressure-drop events flooded the table
at ~50 rows/s, starving every read (positions / postbacks / budget all
scan `algo.events`).

## Root causes (verified 2026-06-18)
1. **`live-ws` events don't belong in Iceberg.** WS lifecycle events
   (`ws_backpressure_drop`, `ws_gap_filled`, connect/disconnect/auth)
   are 7-day observability noise. They were the dominant writer
   (~67% of files; 260k `ws_backpressure_drop` rows alone).
2. **Per-signal immediate flush in live mode.** `_flush_events_now()`
   after every `signal_generated` (and the qty=0 rejection flush added
   2026-06-18) → one Iceberg commit per signal → ~30% of files.
3. **Iceberg metadata never pruned.** Table properties are *all unset*:
   `write.metadata.delete-after-commit.enabled` defaults FALSE, so old
   `metadata.json` files accumulate forever (4,921 files, 7.7 GB).
4. **`algo_events_retention` registered but never scheduled** — not
   enrolled in any pipeline; 7-day short-retention modes never purged.

## One-time remediation (DONE 2026-06-18, separate from this plan)
- Backend restarted to kill the orphaned WS firehose.
- `cleanup_orphans_v2("algo.events", retain_snapshots=5,
  retain_snapshot_min_age_hours=0)` on the idle backend — expired the
  snapshot chain + swept orphan parquet/avro/metadata.json, reclaiming
  the metadata bloat. (Backup taken pre-sweep.)

This plan prevents recurrence.

---

## PR 1 — Stop the metadata bleed (Tier 0 + backpressure aggregation)
**Smallest, highest-leverage. Ship first.**

1. **Set `algo.events` table properties** (in `iceberg_init.py` table
   creation *and* a one-shot migration for the existing table):
   - `write.metadata.delete-after-commit.enabled = true`
   - `write.metadata.previous-versions-max = 20`
   - Consider the same for every write-heavy algo Iceberg table.
2. **Aggregate `ws_backpressure_drop`** in `ws_multiplexer.py`: replace
   one-event-per-drop with a rolling counter flushed as a *single*
   summary event per 60s per (user, strategy) — `{dropped: N,
   window_s: 60}`. Same treatment for `ws_gap_filled`.
3. Backend restart required (Iceberg property change → §6.2).

**Acceptance:** new `metadata.json` files self-prune on commit;
`ws_backpressure_drop` volume drops ~50/s → ~0.017/s (1/min). Unit test
asserts the aggregator emits one row per window regardless of drop count.

## PR 2 — Move `live-ws` events out of Iceberg (Tier 1)
1. Route all `mode=live-ws` lifecycle events to a Redis sorted set
   `algo:ws-events:{user_id}` (score = `ts_ns`), 7-day TTL. Follow
   §5.13 key schema; no-op when `REDIS_URL` empty (silent drop — these
   are not compliance records).
2. Repoint the events-panel read path for `live-ws` mode
   (`routes/paper.py` / `routes/live.py` events query) to Redis.
3. Keep `ws_disconnected` / `ws_auth_failed` *also* as structured WARN
   logs (connection-loss forensics during live trading).

**Acceptance:** zero `mode=live-ws` rows written to `algo.events`;
events panel still renders WS lifecycle from Redis; E2E covers panel.

## PR 3 — Batch live-mode flushes (Tier 2)
1. Remove per-`signal_generated` `_flush_events_now()`; flush on
   `min(30s timer, N buffered)` whichever first. **Fold in the
   2026-06-18 qty=0 rejection flush** (`_maybe_emit_qty_zero_rejection`
   / `_on_bar_close`) — it must batch, not flush per-event.
2. Real-time panel latency: serve recent signals from the in-process
   buffer over the existing chat/live WebSocket, or a short-TTL Redis
   recent-signals cache (reuse PR 2 infra). Avoid a bespoke WS push if
   the Redis cache suffices.

**Acceptance:** live-mode commits/session drop from ~1,470 → single
digits; signals still appear in the panel within a few seconds.

## PR 4 — Schedule retention + maintenance enrollment (Tier 3)
1. Enroll `algo_events_retention` in the weekly long-tail maintenance
   pipeline (Sunday ~02:00 IST) — purges 7-day short-retention modes.
2. Ensure `algo.events` is in `ALL_TABLES`
   (`maintenance/iceberg_maintenance.py`) and the maintenance run does
   `expire_snapshots` + `cleanup_orphans_v2` (row-delete alone does NOT
   reclaim disk — §6.4). Verify `_HOT_ICEBERG_TABLES` enrollment too
   (§4.3 #21).
3. Add a scheduler-history assertion / alert if retention hasn't run in
   N days (this is why it silently never ran).

**Acceptance:** retention appears in scheduler history; a synthetic
old-`live-ws` row is purged on the next run; snapshot count stays
bounded week-over-week.

---

## Sequencing & dependencies
`PR1` (independent, ship now) → `PR2` (removes the main writer) →
`PR3` (depends on PR2 Redis infra for panel) → `PR4` (safety net;
independent but lower urgency once PR1/PR2 land).

## Steady-state target
- `live-ws`: 0 Iceberg files (Redis-only).
- `live`: single-digit commits/session.
- `metadata.json` chain: ~20 versions, self-pruning.
- `algo.events` compaction → effectively a no-op.

## CLAUDE.md compliance
§4.3 #21/#22 (maintenance enrollment), §5.13 (Redis key schema/TTL),
§5.7 (audit event vocabulary unchanged — `live-ws` types move stores,
not meaning), §4.4 #26 (happy + error tests each PR), §6.2 (restart on
Iceberg property change).

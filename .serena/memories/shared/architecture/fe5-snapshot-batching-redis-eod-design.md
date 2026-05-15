---
name: fe5-snapshot-batching-redis-eod-design
description: FE-5.1 (ASETPLTFRM-417, PR #226 open) 3-mode dispatcher pattern for trade_feature_snapshots — backtest/paper buffer + live Redis + EOD flush. Solves the per-fill Iceberg-commit fragmentation discovered in PR #224 validation.
metadata:
  type: architecture
---

# FE-5.1 — 3-mode snapshot batching

ASETPLTFRM-417 / PR #226 (open as of 2026-05-15). Eliminates per-fill Iceberg commits on `stocks.trade_feature_snapshots` (FE-5).

## Problem observed

FE-5's hook (`backend/algo/features/snapshots.py::write_trade_feature_snapshot`) calls `tbl.append(arrow_tbl)` per fill, creating one Iceberg snapshot + manifest list + 1+ manifest avros each time. Yesterday's PR #224 validation found `stocks.trade_feature_snapshots`:

- 7,334 parquet files / 9.4 GB on disk / **14,672 manifest avros** for only **5,096 logical rows**
- Per-table backup: 9.65 GB in 1m 37s
- `cleanup_orphans_v2`: **17+ min** churning the manifest tree (CPU 100%, no disk activity)

The pattern works for live trading (~20-30 fills/session, low rate, real-time visibility matters), but a single 7k-fill backtest produces 7k snapshots → permanent snapshot lineage bloat.

## Architecture

**3 modes via `write_trade_feature_snapshot(mode=..., ...)` dispatcher**:

| Mode | Pattern | Iceberg commits |
|---|---|---:|
| `backtest` | In-process `SnapshotsBuffer` keyed by `(strategy_id, run_id)`. Flushed in `run_backtest()` `finally:` block. Walk-forward folds get per-fold flush via the same finally path automatically. | 1 per run (was 7,000+) |
| `paper` | Same buffer pattern, keyed by `(strategy_id, session_id)`. Flushed in `PaperRuntime.run()` `finally:`. | 1 per session |
| `live` | Push to Redis LIST `algo:live:snapshots:{user_id}:{trading_date_ist}` with 48h TTL. NEW scheduled job `trade_feature_snapshots_eod_flush` at **Mon-Fri 15:30 IST** drains all keys → 1 Iceberg commit per `(user, date)`, then `DEL`s the keys. | 1 per (user, date) |

## Key files (PR #226)

- `backend/algo/features/snapshots_buffer.py` (NEW) — `SnapshotsBuffer` with `threading.RLock` (chose RLock over Lock because `caplog` interaction can re-enter the buffer during logging); `FillSnapshotRow` dataclass; `get_buffer()` singleton + `reset_buffer()` test affordance
- `backend/algo/features/snapshots.py` — `write_trade_feature_snapshot()` becomes a mode-dispatcher; `write_trade_feature_snapshots_batch()` is the bulk writer; `force_immediate=True` kwarg preserved for fixtures / admin tools / FE-5's original 6 hook tests
- `backend/algo/jobs/trade_feature_snapshots_eod_flush.py` (NEW) — async drain job. Uses **FE-13's scoped pre-delete pattern** (`In("fill_id", batch_fill_ids) AND EqualTo("mode", "live")`) before append → partial-replay safe
- `backend/cache.py` — extended `CacheService` + `_NoOpCache` with `rpush`, `lrange`, `delete`, `expire`, `scan_keys`. Graceful no-op when `REDIS_URL` empty
- `backend/algo/backtest/runner.py` + `paper/runtime.py` — `finally:` flushes
- `backend/algo/live/runtime.py` — Redis push only (no in-process buffer for long-running sessions)
- `scripts/seed_trade_feature_snapshots_eod_flush.py` (NEW) — `scheduled_jobs` row, Mon-Fri 15:30 IST. STANDALONE job, NOT part of the Intraday Bars Daily Pipeline (different time)

## Failure semantics — NEVER raises

- **Buffer flush failure** (backtest/paper): caught + logged `exc_info=True`; rows NOT re-buffered. Snapshot loss bounded to one strategy-run; fill itself is durable in `algo.events`.
- **Redis push failure** (live): swallowed + logged; live trading uninterrupted. If Redis goes down mid-session, that session's snapshots are lost for that ticker — but the fill row in `algo.events` survives, so FE-5 dataset gap is recoverable manually.
- **EOD flush per-user failure**: continue batch; failed users' Redis keys stay (next-day flush retries within 48h TTL).

## Idempotency

EOD flush partial-replay safe via FE-13-pattern scoped delete:
```python
tbl.delete(And(
    In("fill_id", batch_fill_ids),
    EqualTo("mode", "live"),
))
tbl.append(arrow_tbl)
```
Successful per-user runs `DEL` the Redis key → next-day's LRANGE returns empty → no replay.

## Performance projection

| Scenario | Before | After |
|---|---:|---:|
| 7k-fill backtest | 7,000 Iceberg commits | **1 per strategy** |
| Live user, 200 trading days/yr × 20 fills/day | 4,000 commits/yr | **200 commits/yr** |
| `cleanup_orphans_v2(stocks.trade_feature_snapshots)` | 17 min | **<30 sec** |

## Why Redis (not in-process buffer) for live

Live sessions run for hours/days. In-process buffer would:
- Grow unbounded (memory pressure)
- Lose state on backend restart
- Be invisible to multi-worker uvicorn deployments

Redis is:
- Already in the stack (used by FE-4 cache, dashboard cache, etc.)
- Persistent across backend restarts (AOF/RDB configured)
- Multi-process safe
- Real-time queryable for live dashboard (LRANGE) — sub-millisecond

## Crash recovery

- Redis AOF/RDB persists in-flight live snapshots across backend restarts
- 48h TTL is safety net if EOD flush ever misses (e.g. scheduler outage on Friday → Monday 15:30 picks up Friday's keys)
- Worst case (Redis wipe): one trading day's enrichment lost, but fills in `algo.events` remain source of truth

## Tests (60/60 green)

- 9 buffer (add/flush/clear/concurrent-add/singleton)
- 9 dispatcher (mode routing + Redis failure swallowed + key format)
- 9 EOD job (drain + DEL + dry-run + per-user failure isolation + idempotency)
- 3 backtest integration (1000-fill backtest → 1 commit, walk-forward folds → N commits)
- 4 live Redis (rpush called, iceberg NOT called, outage swallowed)
- 26 non-regression (existing FE-5, promotion gate verified untouched)

## Cross-refs

- `centralized-feature-engine` — parent epic
- `iceberg-maintenance-smart-skip-and-scoped` — sister workstream; this fix dramatically reduces what smart-skip has to skip on this table
- ASETPLTFRM-407 (FE-5 original, PR #223) — parent ticket
- ASETPLTFRM-417 (this — PR #226)

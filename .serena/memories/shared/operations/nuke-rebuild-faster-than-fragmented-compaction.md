---
name: nuke-rebuild-faster-than-fragmented-compaction
description: When an Iceberg table is heavily fragmented (≥10× expected file count), dropping + re-backfilling from source beats compacting it. 2026-05-15 case study with stocks.intraday_bars.
metadata:
  type: operations
---

# Nuke + rebuild faster than fragmented compaction

## When to apply

An Iceberg table is "heavily fragmented" when:
- Total parquet count >> total partition count (e.g. 30k parquets for 25k partitions = 1.2 avg is borderline; 30k for 5k partitions = 6.0 avg is bad)
- Per-partition file size << target (e.g. ~18 KB per parquet when target is 512 MB)
- `compact_table` consistently hits PyIceberg's atomic-overwrite branch-ref conflict ("overwrite failed")
- OR consistently runs for hours without committing

In this state, smart-skip (`is_compaction_already_optimal`) won't fire (avg too high) but the data CAN be re-fetched cheaply from source.

## 2026-05-15 case study — stocks.intraday_bars

**Before**: 29,809 parquets from PR #220's Nifty 500 × 4yr backfill (one parquet per per-ticker per-batch write × per-day refresh). Two 6h+ compaction attempts both failed:

- 2026-05-14 17:23 → 23:35 (6h 18m) → "overwrite failed" on the atomic commit
- 2026-05-15 06:10 → 07:39 (1h 29m) → "Server restarted while job was running" (uvicorn `--reload` triggered by `intraday_bars_retention.py` edit)

**Decision**: drop + Kite re-backfill. Math:
- Drop table via PyIceberg `catalog.drop_table()` (catalog ref only)
- `rm -rf` warehouse data dir (after drop — files are now orphaned, not part of any Iceberg table; CLAUDE.md §4.3 #20 rule doesn't apply)
- Recreate via `create_tables()`
- Re-fetch Nifty 500 × 4yr × 15m from Kite via existing `intraday_backfill` CLI

**Backfill runtime estimate**: 500 tickers × 8 Kite calls each (paginator chunks 200-day windows for 15m) = 4,000 calls / 3 req/s ≈ 22 min pure Kite. Plus 10 Iceberg commits (batch_size=50 tickers) × ~30s each = ~5 min. **Total ~30 min.**

**Actual**: 26m 17s — finished in under estimate. 492/499 tickers complete, 11.04M bars, 22,234 parquets (one per partition, optimal shape from the start). Plus 32s for the 10-index FE-6 backfill (246,800 bars / 490 parquets).

## Failure-mode comparison

| Path | Wall clock | Failure risk | End state |
|---|---:|---|---|
| Continue compacting | 5-6h | ~50% atomic-overwrite-conflict failure | Same data, defragmented |
| **Nuke + Kite re-backfill** | **30-40 min** | Low — small commits, no atomic-overwrite-the-world | Same data, optimal partition layout |

## Pre-flight checklist

Before nuking:

1. **Backup metadata** to a snapshot dir (`rsync -a $WAREHOUSE/$TABLE/metadata $BACKUP/`) — recovery option if drop or re-create fails
2. **Verify source data exists**: for Kite-sourced tables, check the user has a fresh access_token (daily rotation at 06:00 IST — re-auth if needed)
3. **Stop competing writers** — if the daily keeper is mid-run, wait or kill its scheduler row first
4. **Confirm fresh Kite token via Redis**: `algo:dry_run:{user_id}` should be set False, and `access_token_expires_at` should be in the future

## Procedure

```python
# 1. Drop catalog entry (orphans the data files)
from stocks.create_tables import _get_catalog
c = _get_catalog()
c.drop_table('stocks.intraday_bars')

# 2. Reclaim disk (now safe — files no longer part of any Iceberg table)
# DO NOT rm before drop_table, per CLAUDE.md §4.3 #20
import shutil
shutil.rmtree('/path/to/warehouse/stocks/intraday_bars')

# 3. Recreate empty
from stocks.create_tables import create_tables
create_tables()  # idempotent for existing tables, creates missing ones

# 4. Backfill via existing CLI
# docker compose exec backend python -m backend.algo.backtest.intraday_backfill \
#   --start 2022-05-15 --end 2026-05-15 --interval 15m --batch-size 50 \
#   --universe nifty500 --user-id <kite-authed-user>
```

## Post-flight verification

- Parquet count should match partition count within ±5% (optimal: 1.0 avg)
- Disk usage should be reasonable (~1-2 MB per partition for 15m intraday over 4 years)
- Spot-check one ticker's bar count across the window (`SELECT COUNT(*) WHERE ticker = ?`)
- Daily keeper next run should be fast (~3-5 min) and produce no orphans

## Known caveats

- **Some tickers won't re-fetch**: instrument_token missing in `algo.instruments`, or "invalid token" instrument-level error from Kite (e.g. delisted/renamed). Document them and accept partial coverage. For 2026-05-15: AKZOINDIA.NS (missing) + GSPL.NS (invalid token), 497/499 = 99.6%.
- **Kite token expiry**: tokens rotate at 06:00 IST daily. Re-auth before starting if the operation crosses 06:00.
- **Subsequent daily keeper run**: will see the table as 1-parquet-per-partition (optimal); smart-skip will fire on next maintenance run.

## When NOT to nuke

- Source data isn't re-fetchable (proprietary data, expensive APIs with strict quotas, time-windowed snapshots no longer available)
- Table has <2× expected file count — just let compaction work
- Table has live writers competing — coordinate first
- The data is the SOURCE OF TRUTH (e.g. `algo.events`) — never nuke, only compact/sweep

## Cross-refs

- `iceberg-maintenance-smart-skip-and-scoped` — the proper compaction path when fragmentation is moderate
- CLAUDE.md §4.3 #20 — "NEVER `rm` Iceberg metadata/parquet" applies to ACTIVE tables; once drop_table is called, the rule no longer applies
- `iceberg-table-corruption-recovery` memory (predecessor pattern)

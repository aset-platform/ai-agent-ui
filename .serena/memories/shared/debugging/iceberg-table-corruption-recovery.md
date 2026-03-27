# Iceberg Table Corruption Recovery

## Symptoms
`FileNotFoundError: Failed to open local file '.../<table>/data/<uuid>.parquet'`

The Iceberg metadata references parquet data files that no longer exist on disk.

## Root Cause
The Sunday 04:00 UTC `purge_old_data()` in `gap_filler.py` calls `tbl.maintenance.expire_snapshots().older_than(cutoff).commit()`. If data files were already deleted (manual cleanup, disk issue), the metadata still points to them.

## Diagnosis
```bash
source ~/.ai-agent-ui/venv/bin/activate
PYTHONPATH=backend python scripts/check_tables.py
```
This prints all Iceberg tables with row counts and identifies corrupted ones.

## Recovery Procedure
1. Drop the corrupted table: `cat.drop_table('namespace.table_name')`
2. Recreate via `python stocks/create_tables.py` or `python auth/create_tables.py`
3. Re-seed if needed: `scripts/seed_demo_data.py` or `scripts/fix_seed_users.py`

## Important: `user_writes.create()` Schema
After recreating `auth.users`, the `create()` function in `auth/repo/user_writes.py` must include ALL schema fields including subscription columns:
- `subscription_tier`, `subscription_status`
- `razorpay_customer_id`, `razorpay_subscription_id`
- `stripe_customer_id`, `stripe_subscription_id`
- `monthly_usage_count`, `usage_month`
- `subscription_start_at`, `subscription_end_at`

Missing fields cause `KeyError` in PyArrow table construction.

## Prevention
- Never manually delete files under `~/.ai-agent-ui/data/iceberg/warehouse/`
- The snapshot expiry is safe when data files exist — corruption only happens when they're already gone

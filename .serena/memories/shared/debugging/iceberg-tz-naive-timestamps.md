# Iceberg TimestampType — tz-naive Gotcha

## Problem
Iceberg `TimestampType` maps to pandas `datetime64[us]` which is **tz-naive**.
Passing tz-aware datetimes (e.g., `datetime.now(timezone.utc)`) causes:
- `TypeError: Cannot compare tz-naive and tz-aware datetime-like objects`
- `TypeError: Invalid value '...' for dtype 'datetime64[us]'`

These errors are typically caught by `except Exception` blocks and fail **silently**.

## Fix
Always strip tzinfo before writing to Iceberg via pandas:
```python
if hasattr(v, "tzinfo") and v.tzinfo:
    v = v.replace(tzinfo=None)
```

## Also: NaN Serialization
When reading Iceberg tables with NULL integer/float columns, pandas stores them as `NaN`.
JSON serialization fails with: `ValueError: Out of range float values are not JSON compliant: nan`

Fix: sanitize before JSON response:
```python
for k, v in list(r.items()):
    if isinstance(v, float) and (v != v):  # NaN check
        r[k] = None
```

## Discovered
Mar 27, 2026 — scheduler_runs table writes failed silently for hours.
Root cause: `datetime.now(timezone.utc)` passed to tz-naive Iceberg column.
Jira: ASETPLTFRM-206

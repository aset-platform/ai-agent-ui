# Iceberg TimestampType — tz-naive Gotcha

## Problem (write side)

Iceberg `TimestampType` maps to pandas `datetime64[us]` which is
**tz-naive**. Passing tz-aware datetimes (e.g.,
`datetime.now(timezone.utc)`) causes:
- `TypeError: Cannot compare tz-naive and tz-aware datetime-like objects`
- `TypeError: Invalid value '...' for dtype 'datetime64[us]'`

These errors are typically caught by `except Exception` blocks
and fail **silently**.

## Fix (write side)

Always strip tzinfo before writing to Iceberg via pandas:
```python
if hasattr(v, "tzinfo") and v.tzinfo:
    v = v.replace(tzinfo=None)
```

## Also: NaN Serialization

When reading Iceberg tables with NULL integer/float columns,
pandas stores them as `NaN`. JSON serialization fails with:
`ValueError: Out of range float values are not JSON compliant: nan`

Fix: sanitize before JSON response:
```python
for k, v in list(r.items()):
    if isinstance(v, float) and (v != v):  # NaN check
        r[k] = None
```

## Problem (read side) — frontend timezone drift

When surfacing Iceberg timestamps to the browser, a naïve
datetime round-trips as an ISO-like string without a timezone
designator:

```python
ts = df["timestamp"].max()   # tz-naive pandas Timestamp
str(ts)                      # "2026-04-19 00:15:33.123"
```

Frontend `new Date("2026-04-19 00:15:33")` treats that as
**local time**. For a user in IST (UTC+5:30) the resulting
Date object is ~5.5 hours earlier than intended, so relative
displays (`fmtRelative`) show "5h ago" for rows that were
just written.

## Fix (read side)

Coerce to UTC and emit ISO 8601 with a trailing `Z` before
returning in any API response:

```python
def _iso_utc(ts) -> str | None:
    if ts is None: return None
    if hasattr(ts, "tzinfo"):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(ts, str):
        return ts if "Z" in ts or "+" in ts else f"{ts}Z"
    return str(ts)
```

For pandas Timestamps read off a tz-naive Iceberg column,
`ts.tz_localize("UTC").isoformat().replace("+00:00", "Z")`
works too.

## Where this applies

- Any endpoint that returns Iceberg-sourced timestamps to
  the UI: `last_used_at`, `score_date`, `created_at` on
  admin dashboards.
- `backend/routes.py::_iso_utc()` is the shared helper —
  reuse rather than re-implement.
- `stocks/repository.py::get_dashboard_llm_usage` per-model
  rollup explicitly coerces with
  `grp[ts_col].max().tz_localize("UTC")`.

## Discovery notes

- Mar 27, 2026 — scheduler_runs table writes failed silently
  for hours. Root cause: `datetime.now(timezone.utc)` passed
  to tz-naive Iceberg column. Jira: ASETPLTFRM-206.
- Apr 18, 2026 — BYOM/ My LLM Usage tab showed "5h ago" for
  every `last_used_at` row. Same dtype issue, read side.
  Jira: ASETPLTFRM-324.

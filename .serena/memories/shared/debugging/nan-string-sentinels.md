# Stringified-NaN Sentinel Leak

## Problem

`safe_str` / `safe_sector` in `backend/market_utils.py` reject
numeric NaN, `None`, and empty / whitespace-only strings. But
a separate failure mode is **stringified-NaN tokens**:

- `"NaN"`, `"nan"`, `"NAN"`
- `"None"`, `"null"`
- `"N/A"`, `"na"`
- `"NaT"` (pandas naive-timestamp NA)

These appear when pandas / `json.dumps` / `repr()` stringifies
a float NaN somewhere upstream (e.g. during Iceberg→JSON
round-trip, or when a column dtype changes to `object`). A
naïve `safe_str(val).strip() or "Other"` preserves the token.

## Concrete bites

- LLM recommendation prompt contained a concentration-risk
  entry: *"large weight of NaN (41.8%)"* — the `sector_weights`
  dict had a literal `"NaN"` key from an ETF row.
- Sectors tab on the Insights page showed an unnamed row with
  3 ETFs — the `sector` column held `""` (empty) for some
  rows and `"NaN"` for others.
- Portfolio allocation pie chart had a slice labelled just
  "None" for ETF holdings without a stored sector.

## Fix

`safe_str` rejects all of the above via a case-insensitive
post-strip check:

```python
_MISSING_SENTINELS = frozenset((
    "nan", "none", "null", "n/a", "na", "nat",
))

def safe_str(val) -> str | None:
    ...
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped:
            return None
        if stripped.lower() in _MISSING_SENTINELS:
            return None
        return stripped
    ...
```

`safe_sector(val, fallback="Other")` is a thin wrapper that
returns `fallback` when `safe_str` returns `None`.

## Non-regressions

Legit values that contain "nan" as a substring pass through
unchanged:
- `"Naniwa"` → `"Naniwa"`
- `"Financial Services"` (contains "nan") → `"Financial Services"`

Check with `stripped.lower() in _MISSING_SENTINELS` — the
`in` on a frozenset is exact equality.

## Where to apply

At **read paths** consuming Iceberg / external API string
columns:
- Recommendation engine — stage 2 sector weights.
- Sectors tab aggregator.
- Dashboard portfolio summary.
- Report builder.
- Insights routes — company_info joins.

At **write paths**:
- Stocks repository — before inserting into `company_info`.
- Pipeline fundamentals / universe / screener jobs.
- Stock data tool.

Both sides needed because existing rows may already hold the
bad tokens.

## Test

`tests/backend/test_market_utils_safe.py` — 25 cases covering
numeric NaN, None, empty, whitespace, all sentinel strings
case-insensitively, legit-substring preservation.

## Related

- `shared/debugging/nan-handling-iceberg-pandas` — covers the
  numeric float-NaN truthy trap (sibling issue).

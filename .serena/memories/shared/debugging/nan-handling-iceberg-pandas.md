# NaN Handling: Iceberg NULL → Pandas NaN → Python Gotcha

## The Problem

When reading Iceberg tables with NULL numeric columns, pandas represents them as `NaN` (not `None`). This creates a subtle Python bug because **`NaN` is truthy** and **all comparisons return `False`**.

```python
import math

val = float("nan")  # What pandas gives you

# WRONG — these all fail silently:
val or 0        # → NaN (NaN is truthy!)
val <= 0        # → False
val >= 0        # → False
val == 0        # → False
bool(val)       # → True
```

This means the common fallback pattern `float(row.get("price", 0) or 0)` silently passes NaN through, and any downstream `if val <= 0: use_fallback()` never triggers.

## The Fix

Use a NaN-safe conversion helper:

```python
def _safe_float(val) -> float:
    """Convert to float; NaN / None → 0.0."""
    if val is None:
        return 0.0
    try:
        import math as _m
        f = float(val)
        return 0.0 if _m.isnan(f) else f
    except (ValueError, TypeError):
        return 0.0
```

Then use it wherever reading numeric columns from Iceberg/pandas:
```python
bp = _safe_float(row.get("price"))
if bp <= 0:
    bp = fallback_value  # Now this actually triggers
```

## Where This Applies

- Any `get_portfolio_holdings()` / `get_portfolio_transactions()` numeric fields
- Any Iceberg table column that can be NULL (price, quantity, fees)
- Any pandas DataFrame `.get()` or `.iterrows()` value extraction

## Key Rule

**Never use `val or default` for numeric pandas values.** Always use `math.isnan()` or the `_safe_float()` helper. The `or` operator treats NaN as truthy and won't fall through to the default.

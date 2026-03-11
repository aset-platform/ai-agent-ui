# Mock Patching Gotchas

## Lazy Import Rule

Lazy imports inside functions CANNOT be patched on the importing
module. Patch at the SOURCE:

```python
# WRONG
@patch("stocks.backfill_adj_close.StockRepository")

# RIGHT
@patch("stocks.repository.StockRepository")
```

For `tools.*` in dashboard, use `patch.object()` on the imported
module.

## DataFrame Mutation

Functions that mutate DataFrames in-place also mutate the mock's
return value. Save lookup data BEFORE calling the function under
test.

## Cross-Package Imports

Dashboard tests needing `tools.*` MUST add `backend/` to `sys.path`.

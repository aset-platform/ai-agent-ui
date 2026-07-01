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

## `@asynccontextmanager` Functions

`patch("...disposable_pg_session", return_value=mock_session)` silently
fails. Python's `async with` dispatches `__aenter__` via the TYPE (not
the instance), so the mock's `return_value` never becomes `session`.

Fix: replace with a real `@asynccontextmanager` shim and patch at the
SOURCE module:

```python
from contextlib import asynccontextmanager
from unittest.mock import patch

@asynccontextmanager
async def _fake_disposable():
    yield mock_session

with patch("backend.db.engine.disposable_pg_session", new=_fake_disposable):
    ...
```

Setting `mock.__aenter__ = AsyncMock(return_value=mock_session)` on an
`AsyncMock` instance also does NOT work — magic methods are looked up on
the class, not the instance.

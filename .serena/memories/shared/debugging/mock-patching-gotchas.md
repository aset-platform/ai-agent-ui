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

## Stale Patch Target After Route Refactor (ASETPLTFRM-360)

A route refactor that swaps its internal data-access call (e.g.
`StockRepository.get_ohlcv_batch()` → a direct `query_iceberg_df()` /
`_duckdb_read()` call) silently orphans any test still patching the
OLD call site — the mock decorator applies fine (no error), but
never intercepts anything, so the REAL function runs unmocked. This
is the network-leak pattern behind CI flakes that "just started
failing after a squash merge": the test still passes locally against
whatever real data happens to exist, but fails in a fresh CI runner
(empty/unreachable DB, live yfinance 404s for non-Indian tickers
mistaken as `.NS`).

**Detection**: if a test's mocked return value never shows up in the
assertion (e.g. asserting a fixed float but getting `None`, or a
fixed list but getting `[]`), suspect a stale patch target before
suspecting fixture/data issues — grep the route for what it ACTUALLY
calls now, don't trust the test's own patch string.

**Fix pattern**: patch every real call site the route hits, at
SOURCE (see Lazy Import Rule above) — including any `try/except`
fallback path (e.g. `_duckdb_read` returning empty on a caught
exception looks identical to "no data" in assertions, so mock it to
return a populated frame, not rely on the exception path). Also
force any market-hours/live-overlay branch (`is_market_open`) to a
known value when the ticker's `detect_market()` could route into a
live broker-quote call — otherwise the test's network-safety is
timezone/time-of-day dependent.

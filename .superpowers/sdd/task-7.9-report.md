# Task 7.9 Report: Kite token-expiry + GTT read/delete error narrowing

**Status:** COMPLETE
**Commit:** `ca049d5` — `fix(broker): surface token expiry; don't mask GTT fetch/delete failures`
**Branch:** `feature/rsi2-exit-strategy`

---

## Changes Made

### 1. `backend/algo/broker/exceptions.py`
Added `TokenExpiredError(Exception)` class between `BrokerResponseError` and
`PartialChunkPlacementError`. Docstring: Kite session/token expired — re-auth
required; raised instead of silently degrading.

### 2. `backend/algo/broker/kite_client.py`
**New imports:**
```python
from kiteconnect.exceptions import InputException, TokenException
from backend.algo.broker.exceptions import (..., TokenExpiredError)
```

**`get_gtts` rewrite** (was: catch-all return `[]`):
- `TokenException` -> `_logger.error(..., exc_info=True)` + raise `TokenExpiredError`
- `Exception` (catch-all) -> `_logger.error(..., exc_info=True)` + re-raise
- Activates the already-written Task 3.3 fail-visible path in
  `_ensure_gtts_for_hydrated_positions` (was dead because get_gtts swallowed
  to `[]`, making a read failure look like "no GTTs" -> placed duplicates)

**`delete_gtt` rewrite** (was: catch-all warn + silently swallow):
- `TokenException` -> `_logger.error(..., exc_info=True)` + raise `TokenExpiredError`
- `InputException` -> benign no-op with `_logger.info` (GTT not-found/already-triggered)
- `Exception` (catch-all) -> `_logger.error(..., exc_info=True)` + re-raise

### 3. `backend/algo/broker/tests/test_kite_gtt.py`
- Added `from backend.algo.broker.exceptions import TokenExpiredError` import
- **Renamed/updated** `test_returns_empty_list_on_network_error` ->
  `test_get_gtts_raises_on_network_error` (assert raises `NetworkException`)
- **Renamed/updated** `test_returns_empty_list_on_generic_error` ->
  `test_get_gtts_raises_on_generic_error` (assert raises `RuntimeError`)
- **Kept** happy-path `test_returns_list`
- **Kept** `test_noop_on_already_triggered_exception` (InputException still benign)
- **Added** `test_get_gtts_raises_token_expired_error_on_token_exception`
- **Added** `TestDeleteGttErrors.test_delete_gtt_raises_token_expired_error_on_token_exception`
- **Added** `TestDeleteGttErrors.test_delete_gtt_raises_on_network_error`

---

## Pytest Commands and Output

### GTT tests only (TDD red -> green):

Before implementation (5 new tests fail, 10 existing pass):
```
docker compose exec -T backend python -m pytest backend/algo/broker/tests/test_kite_gtt.py -q
# 5 failed, 10 passed in 0.20s
```

After implementation:
```
docker compose exec -T backend python -m pytest backend/algo/broker/tests/test_kite_gtt.py -q
# 15 passed in 0.17s
```

### Full broker + live suite:
```
docker compose exec -T backend python -m pytest backend/algo/broker backend/algo/live -q
# 10 failed, 378 passed, 2 warnings in 282.54s (0:04:42)
```

The 10 live failures are all pre-existing (same as baseline):
- `test_balance_cap.py` (5 failures)
- `test_live_order_gate.py::test_two_buys_one_tick_second_rejected`
- `test_position_hydration.py::test_zero_qty_positions_and_non_mis_are_skipped`
- `test_trailing_gtt_live.py` (2 failures: test_no_op_when_atr_missing, test_place_gtt_uses_limit_headroom)
- `test_trailing_ratchet.py::TestRatchetAllGtts::test_ratchet_uses_limit_headroom`

No new failures introduced.

---

## Lint

All three modified files pass black (applied), isort (--profile black, confirmed
OK via Python API), and flake8 with zero errors. Pre-commit hook generated 23
non-blocking docstring warnings — pre-existing pattern in the repo (test method
docstrings not required).

---

## Concerns / Follow-up

1. **Broader TokenException handling in order path** (out of scope per brief):
   `place_order`, `orders()`, `positions()` all lack narrowed TokenException
   handling. A future task should apply the same pattern so the runtime surfaces
   token expiry uniformly across all Kite interactions, not just GTT reads/deletes.

2. **`delete_gtt` callers** (6 in `runtime.py` at lines 1327, 1454, 1503, 1680,
   2428, 3144) are all already wrapped in `try/except` — new raises are caught
   and logged by existing caller-side handling. No caller changes required.

3. **`get_gtts` callers** — the hydration caller (runtime.py ~line 2007) now
   correctly enters the `gtt_verification_failed` branch on read failure,
   preventing duplicate GTT placement. The cleanup caller (~line 2397) catches
   the raise and sets `all_gtts=[]` (tolerates raise, as documented in brief).

---

## Follow-up: 7.9 GTT-cancel failure no longer skips emergency SELL

**Status:** COMPLETE
**Commit:** (see below)
**Date:** 2026-06-27

### Problem

Task 7.9 made `delete_gtt` raise on real errors (token expiry / network).
The STOP_HIT branch in `_ratchet_all_gtts` called `delete_gtt` before the
emergency SELL. With the old code, a `delete_gtt` raise would propagate up
and skip the protective SELL — the exact outcome Task 7.9 was meant to
surface, now accidentally introduced into the STOP_HIT path.

### Fix

Wrapped the `self._kite.delete_gtt(old_gtt_id)` call in the STOP_HIT branch
(`backend/algo/live/runtime.py` ~line 1507) in a `try/except Exception` with
`exc_info=True` logging. The emergency SELL always proceeds regardless of
whether the GTT cancel succeeded.

### Regression test added

`backend/algo/live/tests/test_trailing_ratchet.py::TestRatchetAllGtts::`
`test_stop_hit_sell_fires_despite_delete_gtt_failure`

- `delete_gtt.side_effect = RuntimeError("GTT cancel: boom")`
- `rt._loop = None` (sync/fallback path — direct `place_order` SELL route)
- Asserts: no exception propagates; `place_order` called with SELL for INFY;
  trailing manager cleaned up (sold=True reached).

### Test run results

Targeted (test_trailing_ratchet.py + test_stop_hit_tracked.py):
```
17 passed, 1 pre-existing failure (test_ratchet_uses_limit_headroom) in 8.39s
```
New regression test: **PASSED**

Full `backend/algo/live` suite:
```
10 failed, 312 passed in 269.83s
```
Still exactly 10 pre-existing failures — no new failures introduced.

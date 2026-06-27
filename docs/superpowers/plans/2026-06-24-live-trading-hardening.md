# Live-Trading Path Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediate the 46 findings from the 2026-06-24 live-trading review
(`docs/reviews/2026-06-24-live-trading-review.md`) so the real-money path fails
closed, accounts capital atomically, never leaves a position unprotected, and
keeps paper faithful to live.

**Architecture:** Eight phases mapped to the review's root-cause themes, ordered
by money-risk. Each phase is independently shippable and testable. Fixes are
surgical edits to existing modules — no new subsystems. Every behavioural change
gets a unit test (happy + one failure path minimum, per CLAUDE.md §4.4 #26).

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy 2.0 async (asyncpg), PyIceberg,
kiteconnect SDK, Redis, pytest. Run tests in the backend container
(`docker compose exec -T backend python -m pytest ...`) — the host has no pytest.

## Global Constraints

- Line length 79 (black/isort/flake8). `X | None` not `Optional[X]`.
- No bare `except:`; caught exceptions in long-running loops MUST log
  `exc_info=True` (§4.2 #10, #13).
- Mutable state → PG; Iceberg append-only + scoped deletes.
- Scheduler-job PG access uses `disposable_pg_session()` (NullPool), NOT the
  cached `get_session_factory()` (loop-bound). (§5.1)
- No blocking sync I/O on the asyncio loop — wrap in `asyncio.to_thread`.
- Indian equities only (INR, `.NS`); IST timezone; container `TZ=Asia/Kolkata`.
- **Safety gates MUST fail CLOSED** (reject) on any error — the governing
  principle of Phase 0.
- Co-Authored-By: `Abhay Kumar Singh <asequitytrading@gmail.com>`.
- Branch off `dev`; commit per task; squash-merge.

---

## Phase 0 — Fail-Closed Safety Sweep (Theme A) · CRITICAL

The highest-leverage phase: every safety gate currently defaults permissive on
error. Flip each to fail closed.

### Task 0.1: Kill switch fails closed (Critical C3)

**Files:**
- Modify: `backend/algo/paper/kill_switch_repo.py:30-41`
- Test: `backend/algo/paper/tests/test_kill_switch_repo.py` (create)

**Interfaces:**
- Produces: `KillSwitchRepo.is_active(self, user_id: UUID, *, session_factory=None) -> bool`
  — same name; add optional `session_factory` so the Redis-miss path can read
  the durable PG flag. When `session_factory` is None and Redis is
  unavailable, return `True` (fail closed).

- [ ] **Step 1: Write failing tests**

```python
# backend/algo/paper/tests/test_kill_switch_repo.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from backend.algo.paper.kill_switch_repo import KillSwitchRepo

@pytest.mark.asyncio
async def test_redis_armed_returns_true():
    r = AsyncMock(); r.get.return_value = b"1"
    assert await KillSwitchRepo(r).is_active(uuid4()) is True

@pytest.mark.asyncio
async def test_redis_error_fails_closed_without_pg():
    r = AsyncMock(); r.get.side_effect = RuntimeError("redis down")
    # No session_factory → cannot confirm safe → fail CLOSED (armed).
    assert await KillSwitchRepo(r).is_active(uuid4()) is True

@pytest.mark.asyncio
async def test_redis_error_falls_back_to_pg_active():
    r = AsyncMock(); r.get.side_effect = RuntimeError("redis down")
    repo = KillSwitchRepo(r)
    async def fake_get(session, *, user_id):
        return {"active": True}
    repo.get = AsyncMock(side_effect=fake_get)  # type: ignore
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    sf.return_value.__aexit__ = AsyncMock(return_value=False)
    assert await repo.is_active(uuid4(), session_factory=sf) is True

@pytest.mark.asyncio
async def test_no_redis_no_pg_fails_closed():
    assert await KillSwitchRepo(None).is_active(uuid4()) is True
```

- [ ] **Step 2: Run, verify fail**

Run: `docker compose exec -T backend python -m pytest backend/algo/paper/tests/test_kill_switch_repo.py -q`
Expected: FAIL (current code returns False on error).

- [ ] **Step 3: Implement fail-closed `is_active`**

```python
async def is_active(
    self,
    user_id: UUID,
    *,
    session_factory=None,  # noqa: ANN001
) -> bool:
    """Fast read — Redis first. On Redis failure, fall back to the
    durable PG flag; if neither can confirm a SAFE (disarmed) state,
    FAIL CLOSED (return True) so a halted strategy never resumes
    trading on an infra hiccup (real-money safety gate)."""
    if self._redis is not None:
        try:
            v = await self._redis.get(_redis_key(user_id))
            return bool(v)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "Redis kill-switch read failed for %s — falling "
                "back to PG",
                user_id,
                exc_info=True,
            )
    if session_factory is not None:
        try:
            async with session_factory() as session:
                row = await self.get(session, user_id=user_id)
            return bool(row.get("active"))
        except Exception:  # noqa: BLE001
            _logger.error(
                "Kill-switch PG fallback failed for %s — failing "
                "CLOSED",
                user_id,
                exc_info=True,
            )
            return True
    # No Redis result and no PG fallback available → fail closed.
    return True
```

- [ ] **Step 4: Wire the durable fallback at the call site**

Modify `backend/algo/live/runtime.py:2707` to pass a `disposable_pg_session`
factory so the Redis-miss path reads PG:

```python
from backend.db.engine import disposable_pg_session
kill_switch_active=await self._kill_switch_repo.is_active(
    self._user_id, session_factory=disposable_pg_session,
),
```

- [ ] **Step 5: Run tests, verify pass**

Run: `docker compose exec -T backend python -m pytest backend/algo/paper/tests/test_kill_switch_repo.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/algo/paper/kill_switch_repo.py \
  backend/algo/paper/tests/test_kill_switch_repo.py \
  backend/algo/live/runtime.py
git commit -m "fix(algo): kill switch fails closed on Redis outage, reads durable PG flag"
```

### Task 0.2: Broker-cash cap fails closed (High #6)

**Files:**
- Modify: `backend/algo/live/budget.py:200-218` (`fetch_kite_available_cash`)
- Test: `backend/algo/live/tests/test_budget_fail_closed.py` (create)

- [ ] **Step 1: Failing test** — on Kite error in LIVE mode, return
  `Decimal("0")` (not `inf`); `live_balance` of `0` is honored (not masked by
  `cash`). Patch the module's margins fetch (extract it to a small private
  `_kite_margins(user_id)` fn if it's currently inline, so it's patchable).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — on exception `return Decimal("0")`; replace
  `avail.get("live_balance") or avail.get("cash", 0)` with:

```python
lb = avail.get("live_balance")
raw = lb if lb is not None else avail.get("cash", 0)
```

  Keep the dry-run `Infinity` exemption at the call site (`safety.py:185`).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `fix(algo): broker-cash cap fails closed on Kite error; honor zero live_balance`.

### Task 0.3: `pre_trade_check` self-protects (fail closed) (Medium)

**Files:** Modify `backend/algo/live/safety.py:175-220`; Test add to
`backend/algo/live/tests/test_safety_fail_closed.py`.

- [ ] **Step 1: Failing test** — when any budget I/O raises, `pre_trade_check`
  returns a `LIVE_BUDGET_CAP` reject (not propagate / not accept).
- [ ] **Step 2: Run, fail.**
- [ ] **Step 3: Implement** — wrap the Cap-0 budget block in try/except:

```python
if signal.side != "SELL":
    try:
        user_budget = await load_user_budget(user_id)
        open_pos_cost = await sum_open_position_cost(user_id)
        active_reserved = await sum_active_reservations(user_id)
        kite_available = (
            Decimal("Infinity") if dry_run
            else await fetch_kite_available_cash(user_id)
        )
    except Exception:  # noqa: BLE001
        _logger.error(
            "pre_trade_check: budget I/O failed for %s — failing "
            "closed", user_id, exc_info=True,
        )
        return _reject_live(RejectReason.LIVE_BUDGET_CAP)
    # ... existing headroom math unchanged ...
```

- [ ] **Step 4: Run, pass.**
- [ ] **Step 5: Commit** `fix(algo): pre_trade_check fails closed on budget I/O error`.

### Task 0.4: `dry_run_flag` arm/disarm write-failure surfaces (Low→safety)

**Files:** Modify `backend/algo/live/dry_run_flag.py:79-102`; test in
`backend/algo/live/tests/test_dry_run_flag.py`.

- [ ] **Step 1: Failing test** — `arm`/`disarm` raise on Redis write failure
  instead of returning the env default.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — on write exception `raise` (route returns 5xx),
  log `exc_info=True`.
- [ ] **Step 4: pass.**
- [ ] **Step 5: Commit** `fix(algo): dry-run flag write failure surfaces instead of returning env default`.

### Task 0.5: Risk-engine NaN guards fail closed (Medium)

**Files:** Modify `backend/algo/paper/risk_engine.py:71-165`; test in
`backend/algo/paper/tests/test_risk_engine_nan.py`.

- [ ] **Step 1: Failing test** — `gate()` rejects when `last_price`,
  `current_equity_inr`, or either P&L component is NaN (currently NaN
  comparisons are False → silently passes).
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — at the top of `gate()`:

```python
import math
def _is_nan(x) -> bool:  # module-level helper
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return False
# in gate(): reject if any critical input is NaN
if _is_nan(account.current_equity_inr) or _is_nan(last_price) \
   or _is_nan(account.daily_realised_pnl_inr) \
   or _is_nan(account.daily_unrealised_pnl_inr):
    return _reject(RiskReason.INVALID_INPUT)  # fail closed
```

  Standardize cap comparisons to `>=` (Low finding) while here.
- [ ] **Step 4: pass.**
- [ ] **Step 5: Commit** `fix(algo): risk gate fails closed on NaN inputs; consistent >= cap bounds`.

---

## Phase 1 — Order Integrity (Theme H; Criticals C1, C2; High #7, #19)

### Task 1.1: Tick-size rounding on all order/GTT prices (Critical C2)

**Files:**
- Modify: `backend/algo/broker/kite_client.py` — import `get_tick_size`,
  round in `_place_single_chunk` (`:1137-1138`), `place_gtt` (`:1819`,
  `:1836-1846`), and any `modify_order` price.
- Test: `backend/algo/broker/tests/test_tick_size_rounding.py` (create)

**Interfaces:**
- Consumes: `freeze_cache.get_tick_size(tradingsymbol: str, exchange: str = "NSE") -> Decimal`
- Produces: `kite_client._round_to_tick(price: float, tick: Decimal, *, side: str, is_stop: bool) -> float`
  — BUY LIMIT rounds DOWN to a valid tick; SELL LIMIT rounds UP; SELL stop
  trigger rounds DOWN; BUY stop trigger rounds UP.

- [ ] **Step 1: Failing test**

```python
from decimal import Decimal
from backend.algo.broker.kite_client import _round_to_tick

def test_buy_limit_rounds_down_to_tick():
    assert _round_to_tick(1234.5678, Decimal("0.05"),
                          side="BUY", is_stop=False) == 1234.55

def test_sell_limit_rounds_up_to_tick():
    assert _round_to_tick(1234.5678, Decimal("0.05"),
                          side="SELL", is_stop=False) == 1234.60

def test_sell_stop_trigger_rounds_down():
    assert _round_to_tick(99.123, Decimal("0.05"),
                          side="SELL", is_stop=True) == 99.10
```

- [ ] **Step 2: Run, fail** (`_round_to_tick` undefined).
- [ ] **Step 3: Implement helper + apply it** — add `_round_to_tick` (Decimal
  quantize to the tick multiple, direction per args), then in
  `_place_single_chunk` round `price` before building params, and in
  `place_gtt` round `trigger_price`/`limit_price`. Fetch tick via
  `get_tick_size(tradingsymbol, exchange)`; if it returns 0/None, fall back to
  `Decimal("0.05")` (NSE default) and log a warning.
- [ ] **Step 4: Run, pass.**
- [ ] **Step 5: Commit** `fix(broker): round order & GTT prices to symbol tick size`.

### Task 1.2: Chunked-order partial-failure is transactional (Critical C1)

**Files:**
- Modify: `backend/algo/broker/kite_client.py:1045-1071`,
  `backend/algo/broker/exceptions.py` (new exc), `backend/algo/live/runtime.py`
  (`_submit_order` except handling)
- Test: `backend/algo/broker/tests/test_chunk_partial_failure.py` (create)

**Interfaces:**
- Produces: `PartialChunkPlacementError(placed_order_ids: list[str], failed_chunk: int, cause: Exception)`
  carrying already-live order ids — the caller must NOT blind-retry the full qty.

- [ ] **Step 1: Failing test** — mock `_place_single_chunk` to succeed on
  chunks 0,1 and raise on chunk 2; assert `place_order` raises
  `PartialChunkPlacementError` whose `placed_order_ids == [oid0, oid1]`, and that
  an `order_partial_chunk_failure` event was emitted.
- [ ] **Step 2: Run, fail.**
- [ ] **Step 3: Implement** — wrap the chunk loop; on exception emit the event
  and raise:

```python
placed: list[str] = []
for idx, chunk_qty in enumerate(chunks):
    try:
        oid = self._place_single_chunk(... chunk_index=idx ...)
    except Exception as exc:  # noqa: BLE001
        self._emit_partial_chunk_failure_event(
            events_sink=events_sink, ..., placed_order_ids=placed,
            failed_chunk=idx, total_chunks=len(chunks), cause=str(exc),
        )
        _logger.error(
            "place_order: chunk %d/%d FAILED after %d live chunks "
            "(order_ids=%s) — NOT retrying full qty",
            idx, len(chunks), len(placed), placed, exc_info=True,
        )
        raise PartialChunkPlacementError(placed, idx, exc) from exc
    placed.append(oid)
return placed[0]
```

- [ ] **Step 4: Update the runtime caller** — in `_submit_order`'s except block
  catch `PartialChunkPlacementError`: transition the budget reservation to a
  PARTIAL/needs-reconcile state, record the placed ids in `_in_flight`, and do
  NOT re-submit. Add a runtime test asserting no re-submit.
- [ ] **Step 5: Run, pass. Commit** `fix(broker): chunked order partial failure raises with placed ids, no blind full-qty retry`.

### Task 1.3: Reject phantom empty order_id (High #7)

**Files:** Modify `backend/algo/broker/kite_client.py:1149-1153`,
`backend/algo/broker/exceptions.py`; test in `test_chunk_partial_failure.py`.

- [ ] **Step 1: Failing test** — when `place_order` SDK returns `None`/`{}`/a
  dict without `order_id`, `_place_single_chunk` raises (no submitted event with
  empty id).
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — after parsing, `if not order_id: raise
  BrokerResponseError(f"place_order returned no order_id: {resp!r}")`; log raw
  response; only emit `_emit_submitted_event` after a non-empty id.
- [ ] **Step 4: pass. Commit** `fix(broker): treat empty order_id as failure, not phantom success`.

### Task 1.4: Dedup keyed on internal_order_id; fail-closed for large notional (High #19)

**Files:** Modify `backend/algo/broker/redis_keys.py` (`build_dedup_key`),
`kite_client.py:1244-1273`; test in `backend/algo/broker/tests/test_dedup.py`.

- [ ] **Step 1: Failing tests** — (a) dedup key derives from
  `internal_order_id` (a qty-recompute retry of the same logical order
  collides); (b) on Redis error for an order with notional ≥
  `ALGO_DEDUP_FAILCLOSED_INR` (default 100000) the order is BLOCKED (raise), not
  allowed through.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — key includes `internal_order_id`; Redis-error
  branch raises for notional ≥ threshold, else fail-open with warning.
- [ ] **Step 4: pass. Commit** `fix(broker): dedup keyed on internal_order_id; fail-closed above notional threshold`.

---

## Phase 2 — Budget Atomicity (Theme B; Critical C4; High #12; Medium netting)

### Task 2.1: Atomic, uncached, headroom-aware reserve (Critical C4)

**Files:**
- Modify: `backend/algo/live/budget_repo.py` (reserve insert), `budget.py`
  (`budget_reserve`), `safety.py:175-220`.
- Test: `backend/algo/live/tests/test_budget_atomic_reserve.py` (create)

**Interfaces:**
- Produces: `budget_repo.reserve_if_headroom(session, *, user_id, strategy_id, ticker, side, qty, reserved_inr, allocated_inr, metadata) -> UUID | None`
  — ONE transaction: (a) `SELECT ... FOR UPDATE` on the user's
  `algo.user_budget` row to serialize per-user; (b) recompute
  `allocated_inr − Σopen_cost − Σactive_reservations` uncached inside the txn;
  (c) insert the PENDING reservation only if `headroom >= reserved_inr`, else
  return `None`. Returns the new `reservation_id` on success.

- [ ] **Step 1: Failing test** (integration, real PG via the existing
  `backend/algo/live/tests` session fixture): two concurrent
  `reserve_if_headroom` calls for one user whose combined cost exceeds
  `allocated_inr` — exactly one returns a UUID, the other `None`; a single call
  within headroom succeeds; one exceeding headroom returns `None`.
- [ ] **Step 2: Run, fail.**
- [ ] **Step 3: Implement** `reserve_if_headroom` with `SELECT ... FOR UPDATE` +
  `INSERT ... SELECT ... WHERE (headroom subquery) >= :cost`; headroom subqueries
  reuse the existing `sum_*` SQL but executed inside the same txn, uncached;
  return `None` when 0 rows inserted.
- [ ] **Step 4: Rewire gating** — `pre_trade_check` Cap-0 no longer decides on
  the 5s-cached headroom; the reservation (Task 2.2) is the gate. Keep cached
  reads for read-only UI only.
- [ ] **Step 5: Run, pass. Commit** `fix(algo): atomic headroom-aware budget reservation (fixes TOCTOU over-deploy)`.

### Task 2.2: Reserve before the gate decision; count in-flight (Critical C4 cont.)

**Files:** Modify `backend/algo/live/runtime.py` (gate on `reserve_if_headroom`
result), `runtime.py:2718-2725` (`committed_inr_now`). Test in
`test_budget_atomic_reserve.py`.

- [ ] **Step 1: Failing test** (runtime-level, mocked budget): two BUY signals
  in one tick for the same strategy whose combined cost exceeds remaining
  `max_inr` — the second is rejected (currently both pass).
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — order path calls `reserve_if_headroom` before
  committing to submit; on `None` emit `signal_rejected/insufficient_balance`
  and abort; add active BUY reservations into `committed_inr_now`
  (deployed = filled + active).
- [ ] **Step 4: pass. Commit** `fix(algo): gate on atomic reservation; include in-flight reservations in deployed capital`.

### Task 2.3: `transition()` terminal-state guard + monotonic ordering (High #12)

**Files:** Modify `backend/algo/live/budget.py:260-310`, `budget_repo.py`
(`get_current_state`, `sum_*` DISTINCT ON ordering); migration if a monotonic
`seq` column is needed (enroll per §6.2). Test in `test_budget_atomic_reserve.py`.

- [ ] **Step 1: Failing test** — a `transition()` from TERMINAL
  (FILLED/CANCELLED/REJECTED) is refused; "current state" resolves by `seq`/PK,
  not `transitioned_at`.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — terminal guard; switch ordering to the monotonic
  key; add migration if needed.
- [ ] **Step 4: pass. Commit** `fix(algo): budget transition rejects terminal-state changes; order by monotonic seq`.

### Task 2.4: Per-ticker cost-basis (no global SELL netting) (Medium)

**Files:** Modify `backend/algo/live/budget_repo.py:194-239`. Test in
`test_budget_atomic_reserve.py`.

- [ ] **Step 1: Failing test** — user with open BUY on A + a profitable closed
  round-trip on B: `sum_open_position_cost` reflects only A's basis.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — per-ticker `max(0, Σbuy_qty − Σsell_qty) ×
  avg_buy_price` then sum; floor each row's `reserved−filled` at 0
  (`GREATEST(...,0)`); drop the global floor masking net-negative.
- [ ] **Step 4: pass. Commit** `fix(algo): per-ticker cost basis in budget headroom (no cross-ticker netting)`.

---

## Phase 3 — Restart / GTT Protection (Theme E; Critical C5; High #13)

### Task 3.1: Place protective GTT/trailing on every BUY source (Critical C5)

**Files:** Modify `backend/algo/live/runtime.py:738-816` (`_sync_fills_from_pg`
BUY branch) and the `_recover_unhydrated_positions` injection path. Test:
`backend/algo/live/tests/test_sync_fills_gtt.py` (create).

- [ ] **Step 1: Failing test** — feed a filled BUY through `_sync_fills_from_pg`
  with trailing enabled; assert `on_buy_fill_trailing` (or GTT placement) is
  invoked and a trailing manager exists for the ticker.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — in the BUY branch after `apply_fill`, if
  `self._trailing_enabled and ticker not in self._trailing_managers`, call
  `on_buy_fill_trailing(ticker=ticker, fill_price=float(fill_price), qty=qty)`
  via the same async/sync bridge `_sync_fills_from_pg` already uses.
- [ ] **Step 4: pass. Commit** `fix(live): place protective GTT/trailing when a BUY is applied via fill-sync`.

### Task 3.2: Carry real `opened_at` on restart; refuse zero-entry GTT (High #13)

**Files:** Modify `backend/algo/live/runtime.py:757-767`, `:1496-1507`;
`caps_repo.get_filled_buys_from_previous_runs` to return the original
submit/fill date + fill_price. Test in `test_sync_fills_gtt.py`.

- [ ] **Step 1: Failing tests** — (a) a position recovered on restart whose
  original fill date was 5 days ago reports that `opened_at` (so a
  `max_holding_days=3` time-stop fires); (b) recovery with `avg_price<=0` emits
  `gtt_skipped_no_entry_price` and places no GTT.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — thread the original date into `Fill.fill_date` in
  both paths (from `submitted_at`/events payload); parse fill_price as
  `Decimal(str(...))`; refuse GTT when `avg_price <= 0`.
- [ ] **Step 4: pass. Commit** `fix(live): preserve real opened_at on restart; refuse GTT with zero entry price`.

---

## Phase 4 — Reconciliation Robustness (Theme E/F; High #8, #9, #10; Medium)

### Task 4.1: NullPool session + non-blocking timed broker call (High #8, #9)

**Files:** Modify `backend/algo/live/reconciliation.py:106,146,168`. Test:
`backend/algo/live/tests/test_reconciliation_loop.py`.

- [ ] **Step 1: Failing test** — both fetchers use `disposable_pg_session`
  (assert cached `get_session_factory` NOT called), and `kite.get_positions`
  runs via `asyncio.to_thread` under `asyncio.wait_for`.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — replace both `get_session_factory()` with
  `async with disposable_pg_session() as session:`; wrap
  `raw = await asyncio.wait_for(asyncio.to_thread(kite.get_positions), timeout=_RECON_KITE_TIMEOUT_S)`.
- [ ] **Step 4: pass. Commit** `fix(live): reconciliation uses NullPool session + non-blocking timed broker call`.

### Task 4.2: Fill-during-cancel handling (High #10)

**Files:** Modify `backend/algo/live/order_timeout.py:255-322`. Test:
`backend/algo/live/tests/test_order_timeout_race.py`.

- [ ] **Step 1: Failing test** — cancel on an order that filled in the race
  window (`order_history` COMPLETE / `filled_quantity == quantity`) emits
  `order_filled_before_cancel` and routes the fill to the position/GTT path.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — after cancel, re-fetch the single order; branch on
  terminal state; emit the correct event; surface `filled_quantity` on partials.
- [ ] **Step 4: pass. Commit** `fix(live): order-timeout handles fill-during-cancel; no mislabeled cancel`.

### Task 4.3: Direction-aware drift escalation (Medium)

**Files:** Modify `backend/algo/live/reconciliation.py:182-286`. Test in
`test_reconciliation_loop.py`.

- [ ] **Step 1: Failing test** — `broker_qty>0 and our_qty==0` emits a
  high-severity `position_drift_untracked` event.
- [ ] **Step 2: fail.** → **Step 3: Implement** branch by direction. →
  **Step 4: pass. Commit** `fix(live): escalate untracked-broker-position drift`.

---

## Phase 5 — WebSocket Resiliency (Theme D/F; High #11; Medium)

### Task 5.1: Guard bad ticks so one packet can't kill the WS thread (High #11)

**Files:** Modify `backend/algo/broker/ws_multiplexer.py:419-431`. Test:
`backend/algo/broker/tests/test_ws_tick_guard.py`.

- [ ] **Step 1: Failing test** — an `on_ticks` batch with a zero-price packet
  and a malformed packet processes the good ticks and drops the bad without
  raising.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — `if ltp_val <= 0: continue` before constructing
  `Tick`, AND wrap the per-tick body in `try/except Exception:
  _logger.warning(..., exc_info=True); continue`.
- [ ] **Step 4: pass. Commit** `fix(broker): guard malformed/zero-price ticks; one bad packet can't kill the WS loop`.

### Task 5.2: Track/cancel gap-fill tasks; single-flight; backfill all tokens (Medium)

**Files:** Modify `backend/algo/broker/ws_multiplexer.py:601-618,277,659-663`.
Test in `test_ws_tick_guard.py`.

- [ ] **Step 1: Failing test** — scheduling gap-fill twice cancels the prior
  task; `close()` cancels it; replay routes through `_enqueue_tick`.
- [ ] **Step 2: fail.** → **Step 3: Implement** store `self._gap_fill_task`,
  cancel-before-reschedule, await/cancel in `close()`; gap-fill off the union of
  subscribed tokens; replay via `_enqueue_tick`. → **Step 4: pass. Commit**
  `fix(broker): track + single-flight gap-fill; backfill all subscribed tokens`.

### Task 5.3: Batch per-tick LTP Redis writes + prune bp dicts (Medium/Low)

**Files:** Modify `backend/algo/broker/ws_multiplexer.py:431-439`, `unsubscribe`
(`:229-270`). Test in `test_ws_tick_guard.py`.

- [ ] **Step 1: Failing test** — one `on_ticks` batch issues a single pipelined
  write; `unsubscribe` pops `_bp_drops`/`_bp_last_emit_ns`.
- [ ] **Step 2: fail.** → **Step 3: Implement** accumulate `{key: val}` per
  batch, one pipeline w/ per-key TTL; prune the bp dicts on unsubscribe. →
  **Step 4: pass. Commit** `perf(broker): coalesce per-tick LTP writes; prune backpressure dicts on unsubscribe`.

---

## Phase 6 — Paper ↔ Live Parity (Theme C; High #16, #17, #18, #20; Medium)

### Task 6.1: Mark-to-market equity + unrealised P&L (High #16)

**Files:** Modify `backend/algo/paper/runtime.py:1296-1306`
(`_account_snapshot`), `:1269,1283` (sizing NAV/cash). Test:
`backend/algo/paper/tests/test_paper_parity.py`.

- [ ] **Step 1: Failing test** — with open positions + cached LTP, the snapshot
  includes open-position MV in `current_equity_inr`, populates
  `daily_unrealised_pnl_inr`, and passes `cash = nav − deployed_cost` to sizing.
- [ ] **Step 2: fail.** → **Step 3: Implement** mark to `last_price_per_ticker`.
  → **Step 4: pass. Commit** `fix(paper): mark-to-market equity/unrealised so caps & sizing match live`.

### Task 6.2: Slippage model in paper fills (High #16)

**Files:** Modify `backend/algo/paper/broker.py:37-58`. Test in
`test_paper_parity.py`.

- [ ] **Step 1: Failing test** — BUY fills above and SELL below `last_price` by
  `ALGO_PAPER_SLIPPAGE_BPS` (directional); fees unchanged.
- [ ] **Step 2: fail.** → **Step 3: Implement** directional bps slippage on
  `fill_price`. → **Step 4: pass. Commit** `fix(paper): apply directional slippage to paper fills`.

### Task 6.3: Emit event on qty=0 drop (High #17)

**Files:** Modify `backend/algo/paper/runtime.py:1166-1167,1224-1248,1289`.
Test in `test_paper_parity.py`.

- [ ] **Step 1: Failing test** — qty<=0 emits `signal_dropped_qty_zero` with the
  reason instead of silent `None`.
- [ ] **Step 2: fail.** → **Step 3: Implement** emit before returning `None`
  (mirror live `_maybe_emit_qty_zero_rejection`). → **Step 4: pass. Commit**
  `fix(paper): emit signal_dropped_qty_zero instead of silent drop`.

### Task 6.4: Periodic crash-safe event flush (High #18)

**Files:** Modify `backend/algo/paper/runtime.py` (run loop + `_events`). Test
in `test_paper_parity.py`.

- [ ] **Step 1: Failing test** — events flush on a size/time threshold during
  the run; a simulated mid-run crash still has prior fills persisted.
- [ ] **Step 2: fail.** → **Step 3: Implement** flush when `len(_events) >= N`
  or every T seconds (batched single commit, §4.1 #7). → **Step 4: pass. Commit**
  `fix(paper): periodic batched event flush (crash-safe promotion gate)`.

### Task 6.5: `rebuild_all` exc_info + supervisor reap (High #20, Medium)

**Files:** Modify `backend/algo/paper/replay_rebuilder.py:149-160`,
`backend/algo/paper/supervisor.py:84-123`. Test:
`backend/algo/paper/tests/test_supervisor_reap.py`.

- [ ] **Step 1: Failing tests** — (a) per-user replay failure logs `exc_info`;
  (b) `_on_done` removes the entry so `start_run` can re-arm; a crashed run
  reports `failed`.
- [ ] **Step 2: fail.** → **Step 3: Implement** add `exc_info=True`; pop/flag in
  `_on_done`; distinguish `failed`/`cancelled`/`completed` in `_public_row`. →
  **Step 4: pass. Commit** `fix(paper): log replay failures with exc_info; supervisor reaps completed/crashed runs`.

---

## Phase 7 — Scale, Memory & Misc (Theme F/G + remaining Medium/Low)

Each: failing test (where testable) → fix → commit, in this order.

### Task 7.1: Offload per-bar reads in live runtime (Medium)
`runtime.py:2219-2244` — wrap `_ensure_regime_cache`,
`_ensure_daily_overlay_cache`, `emit_features_for_bar` in `asyncio.to_thread`
(or pre-warm at startup like the factor cache). Test: bar-close path doesn't call
the sync reader directly. Commit `perf(live): offload per-bar Iceberg reads off the event loop`.

### Task 7.2: Bound live-runtime caches (High #15)
`runtime.py` — cap `_bars_by_ticker[ticker]` to max indicator lookback (e.g.
300) on append; evict `_closed_entry_cache` keys older than 2 trading days at
bar-close. Test: feed >300 bars, assert cap. Commit `fix(live): bound per-ticker bar history and closed-entry cache`.

### Task 7.3: Resampler time-flush + eviction (Medium)
`stream/resampler.py:44-72` — finalize any open bar whose `bar_open+interval` is
past relative to the latest tick; evict its key. Test: a quiet ticker's bar is
emitted on the sweep. Commit `fix(stream): time-driven bar flush + open-bar eviction`.

### Task 7.4: Idempotent bars_writer (Medium)
`stream/bars_writer.py:64-76` — scoped delete on
`(ticker, interval_sec, bar_open_ts_ns IN batch)` then append, wrapped in
`retry_iceberg_op`; mark shutdown-flushed partials. Test: re-flushing the same
window yields no duplicate rows. Commit `fix(stream): idempotent intraday bar writes (scoped delete + append)`.

### Task 7.5: Reconciliation batch order book (Medium)
`budget_reconciliation.py:362-379` — fetch `kite._kc.orders()` once/user, build
`{order_id: status}`, reconcile against the map; per-order history only as
fallback. Test: N reservations → 1 `orders()` call. Commit
`perf(algo): batch reconciliation against one orders() call per user`.

### Task 7.6: STOP_HIT emergency SELL through the order path (Medium)
`runtime.py:1217-1245` — route the emergency SELL via `run_coroutine_threadsafe`
into `_submit_order` (or append `_in_flight` + budget reservation + event + lock
discard). Test: emergency SELL records an `_in_flight` entry. Commit
`fix(live): emergency stop-hit SELL goes through tracked order path`.

### Task 7.7: MIS square-off cancels GTT + uses live LTP (Medium)
`runtime.py:1061-1093` — cancel any GTT for the ticker before square-off; use
`last_price_per_ticker` if available. Test: square-off cancels the GTT first.
Commit `fix(live): MIS square-off cancels GTT and prices off live LTP`.

### Task 7.8: Sizing guards — vol floor, DD/cash-floor visibility, peak guard (Medium)
`sizing/vol_target.py`, `composer.py:112-115`, `caps.py:46-53`,
`drawdown_throttle.py:41-45` — vol floor (reject/clamp `vol < 0.05`); when
`mult>0` but `int(capped*mult)==0` log/emit (not silent 0); emit when cash floor
zeroes an entry; `peak<=0` → halt multiplier. Tests per guard. Commit
`fix(sizing): vol floor, observable DD/cash-floor zero-drops, safe peak guard`.

### Task 7.9: Kite token-expiry + GTT read/delete error narrowing (Medium)
`kite_client.py:1860-1886` + token handling — catch `TokenException` distinctly
(raise typed `TokenExpiredError`); `get_gtts` raises on fetch error (not `[]`);
`delete_gtt` no-ops only on genuine "not found/already triggered". Test: token
error surfaces; fetch error not masked. Commit `fix(broker): surface token expiry; don't mask GTT fetch/delete failures`.

### Task 7.10: Intraday warmup row isolation + epoch filter (Medium/Low)
`intraday_bar_warmup.py:233-246` — wrap per-row `BarData` build in try/except
`continue`; add explicit `date >= '1980-01-01'` predicate to both warmup
readers. Test: one NULL `bar_open_ts_ns` drops one bar, not the universe. Commit
`fix(warmup): isolate malformed bar rows; explicit pre-1980 date filter`.

### Task 7.11: Low-severity cleanups (batch)
`_iceberg_retry` release lock during sleep (`_iceberg_retry.py:58-77`); STT
intraday-sell 0.02% rate row post-2024-10-01 (`fees.py`/`fee_rates.yaml` + pin
test); `quote()` BSE `.BO`→`BSE:` (`kite_client.py:597`); order-timeout
full-strategy-id tag match (`order_timeout.py:173`); `is_market_open_ist`
holiday calendar (`reconciliation.py:293`); `_NSE_DEFAULTS` → `MappingProxyType`
(`freeze_cache.py:41`); teardown `except` blocks log `exc_info`
(`runtime.py:1926-2024`, `ws_multiplexer.py:277`). Each: test where testable;
commit `chore(algo): low-severity review cleanups (batch N)`.

---

## Self-Review

- **Spec coverage:** All 5 Criticals → 0.1, 1.1, 1.2, 2.1/2.2, 3.1. All 15
  Highs → Phases 0–6. Medium/Low → Phases 4–7. Every review finding maps to a
  task.
- **Sequencing rationale:** fail-closed (0) + order-integrity (1) first — stop
  money loss with least code; budget atomicity (2) may need a migration; paper
  parity (6) is lower money-risk so later.
- **Type consistency:** `reserve_if_headroom`, `_round_to_tick`,
  `PartialChunkPlacementError`, `BrokerResponseError`,
  `is_active(..., session_factory=)` used consistently across referencing tasks.
- **Design-dependent tasks** (2.1 atomic reserve, 1.2 chunk transactionality,
  6.1 mark-to-market) carry concrete interfaces + tests; confirm internal SQL /
  async bridge wiring against the live schema during execution (flagged in-task).

## Verification (whole-phase gates)

After each phase: `docker compose exec -T backend python -m pytest backend/algo -q`
must stay green; lint `black/isort/flake8` on touched files. **Before any LIVE
session resumes, Phases 0–3 MUST be complete** (they cover all 5 Criticals).

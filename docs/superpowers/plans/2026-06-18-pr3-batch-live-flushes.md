# PR3 — Batch live-mode `algo.events` flushes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop committing to `algo.events` once per `signal_generated` (and once per qty=0 rejection). Buffer live-mode events and flush them on a fixed ~5s cadence (plus the existing terminal flush on stop), collapsing ~1,470 commits/session to single digits while keeping panel latency to a few seconds.

**Architecture:** A single per-runtime background task (`_periodic_event_flush`) calls the existing `_flush_events_now()` every `_EVENT_FLUSH_INTERVAL_S` (default 5, env-overridable). The five per-event `await self._flush_events_now()` calls in `_on_bar_close` / the order paths are removed — those sites only `self._events.append(...)` now. The task is started in `run()` alongside the other background tasks and cancelled in the `finally:` block *before* the existing terminal flush (which still drains the last buffer). Real Kite fills are unaffected — they arrive via the `/webhooks/kite/postback` handler's own write path, not the runtime buffer.

**Design note (timer vs. Redis cache):** The spec offered either a 30s timer + a Redis recent-signals cache for low panel latency, or an in-memory/WS push. This plan uses **only a 5s periodic timer**: it already satisfies "signals appear within a few seconds" with zero new infrastructure and no change to the events panel read path. A Redis recent-signals cache (reusing PR2's `ws_event_store` pattern) remains a documented follow-up *iff* sub-second panel latency is later required.

**Tech Stack:** Python 3.12, asyncio, pytest (`pytest.mark.asyncio`). Tests run in the backend container: `docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 python -m pytest <path> -v`.

---

## File Structure

- **Modify** `backend/algo/live/runtime.py` — add `_EVENT_FLUSH_INTERVAL_S` module constant + `self._event_flush_task` attr + `_periodic_event_flush()` method; start/cancel it in `run()`; remove the 5 per-event `_flush_events_now()` calls.
- **Create** `backend/algo/live/tests/test_live_event_flush.py` — unit test for the periodic flush task (flushes on cadence, stops on cancel).

The terminal flushes (`asyncio.to_thread(flush_events, self._events)` at the end of `run()`'s `finally:` and in `close()`) are **unchanged** — they remain the last-buffer safety net.

---

## Task 1: Periodic flush task

**Files:**
- Modify: `backend/algo/live/runtime.py`
- Test: `backend/algo/live/tests/test_live_event_flush.py`

- [ ] **Step 1: Write the failing test.** Create `backend/algo/live/tests/test_live_event_flush.py`:
```python
"""PR3 — the periodic event-flush task drains the buffer on a fixed
cadence and stops cleanly on cancel."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)
pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="Requires pyarrow + Python >=3.10 (Docker backend container)",
)


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "pr3 flush test strategy",
        "universe": {
            "type": "scope",
            "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d", "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        "root": {"type": "hold"},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80, "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


def _make_runtime():
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {"live_orders_enabled": True}
    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False
    with patch(
        "backend.algo.live.position_hydration.hydrate", return_value=[]
    ):
        return LiveRuntime(
            strategy=parse_strategy(_strategy_payload()),
            user_id=uuid4(),
            initial_capital_inr=Decimal("3000"),
            fee_as_of=date(2026, 4, 1),
            kite=MagicMock(dry_run=True),
            caps={"live_orders_enabled": True, "allowed_tickers": []},
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )


@pytest.mark.asyncio
async def test_periodic_flush_drains_then_stops(monkeypatch):
    import backend.algo.live.runtime as rt

    runtime = _make_runtime()
    calls = []

    async def _fake_flush():
        calls.append(1)

    monkeypatch.setattr(runtime, "_flush_events_now", _fake_flush)
    monkeypatch.setattr(rt, "_EVENT_FLUSH_INTERVAL_S", 0.01)

    task = asyncio.create_task(runtime._periodic_event_flush())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(calls) >= 2  # flushed repeatedly on the fast cadence
```

- [ ] **Step 2: Run it, verify it FAILS** (`AttributeError: ... has no attribute '_periodic_event_flush'` and/or no `_EVENT_FLUSH_INTERVAL_S`):
```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 \
  python -m pytest backend/algo/live/tests/test_live_event_flush.py -v
```

- [ ] **Step 3: Add the constant.** In `backend/algo/live/runtime.py`, near the other module-level constants (just below the `_MIN_EVAL_TIME_IST = _parse_ist_time(...)` block, around line 121), add:
```python
# PR3 — live-mode events are buffered and flushed on this cadence
# instead of one Iceberg commit per signal. The terminal flush on
# session stop drains whatever remains. Env-overridable for tuning.
_EVENT_FLUSH_INTERVAL_S = float(
    os.environ.get("ALGO_EVENT_FLUSH_INTERVAL_S", "5")
)
```

- [ ] **Step 4: Add the task attribute.** In `__init__`, right after `self._square_off_task: asyncio.Task | None = None`, add:
```python

        # PR3 — periodic algo.events flush task. Started in run(),
        # cancelled in its finally: before the terminal flush.
        self._event_flush_task: asyncio.Task | None = None
```

- [ ] **Step 5: Add the method.** Add this method to `LiveRuntime` (next to `_flush_events_now`):
```python
    async def _periodic_event_flush(self) -> None:
        """Flush buffered ``algo.events`` rows on a fixed cadence so
        live-mode events reach the panel within a few seconds without a
        commit per signal (PR3). Runs until cancelled at teardown."""
        try:
            while True:
                await asyncio.sleep(_EVENT_FLUSH_INTERVAL_S)
                try:
                    await self._flush_events_now()
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "periodic event flush failed", exc_info=True
                    )
        except asyncio.CancelledError:
            raise
```

- [ ] **Step 6: Run the test, verify PASS** (same command as Step 2). Expected: 1 passed.

- [ ] **Step 7: Lint + commit.**
```bash
docker exec -i -w /app ai-agent-ui-backend-1 \
  flake8 backend/algo/live/runtime.py backend/algo/live/tests/test_live_event_flush.py
git add backend/algo/live/runtime.py backend/algo/live/tests/test_live_event_flush.py
git commit -m "feat(algo): PR3.1 — periodic algo.events flush task for live runtime"
```

---

## Task 2: Start/cancel the task and remove the 5 per-event flushes

**Files:**
- Modify: `backend/algo/live/runtime.py` (`run()` start + `finally:` cancel; remove 5 flush calls)

- [ ] **Step 1: Start the task in `run()`.** Immediately after the MIS square-off block and BEFORE `try:` (i.e., after the lines that end the `if (... product == "MIS"): self._square_off_task = asyncio.create_task(self._schedule_mis_square_off())` block, just before `try:`), insert:
```python
        # PR3 — start the periodic algo.events flush (idempotent guard
        # so a re-entrant run() doesn't double-start). Cancelled in the
        # finally: block below before the terminal flush.
        if self._event_flush_task is None:
            self._event_flush_task = asyncio.create_task(
                self._periodic_event_flush(),
            )
```

- [ ] **Step 2: Cancel the task in `finally:` before the terminal flush.** In `run()`'s `finally:` block, immediately BEFORE the terminal-flush block (the `if self._events:` that calls `await asyncio.to_thread(flush_events, self._events)`), insert:
```python
            # PR3 — stop the periodic flush before the terminal drain
            # so they cannot race on self._events.
            if self._event_flush_task is not None:
                self._event_flush_task.cancel()
                try:
                    await self._event_flush_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._event_flush_task = None
```

- [ ] **Step 3: Remove the qty=0 immediate flush.** In `_on_bar_close`, in the `if daily_realtime and is_flat and last_bar_is_today:` block:
  (a) delete the line `_evt_n_before = len(self._events)` (it was only used by the flush check), and
  (b) delete the trailing block:
```python
            # The completed-bar eval may have buffered a qty=0
            # rejection (entry qualified but the account can't afford
            # one share). When no tradable BUY resulted there is no
            # downstream ``_flush_events_now`` to push it, so flush
            # here — bounded to once per (ticker, closed bar) because
            # the underlying emit is cache-miss-gated.
            if closed_entry is None and len(self._events) > _evt_n_before:
                await self._flush_events_now()
```
  Leave the rest of the daily block (the `closed_entry = self._eval_entry_on_closed_bar(...)` call and the acting-now / premature branches) unchanged. The qty=0 rejection event is still appended by `_maybe_emit_qty_zero_rejection`; the periodic task flushes it within ~5s.

- [ ] **Step 4: Remove the four remaining per-event flushes.** Delete each of these standalone `await self._flush_events_now()` lines (the preceding `self._events.append(...)` stays):
  - after the `signal_generated` event append (the line currently right after the `signal_generated` `event_row(...)` append block);
  - after the `signal_rejected` (pre-trade reject) event append;
  - after the `order_submitted_live` comment block (the `await self._flush_events_now()` following the "PaperEventsTimeline reads" comment);
  - after the dry-run synthetic-fill `order_filled_live` event append (the `await self._flush_events_now()` before the "Release the budget reservation" comment).

  Verify afterwards with:
```bash
grep -n "_flush_events_now\|flush_events" backend/algo/live/runtime.py
```
  Expected matches ONLY: the `from ... import ... flush_events` line; the `_flush_events_now` def + its `to_thread(flush_events, rows)`; the `_periodic_event_flush` call to `_flush_events_now`; and the two terminal `to_thread(flush_events, self._events)` drains (run() finally + close()). NO other `await self._flush_events_now()` call sites remain.

- [ ] **Step 5: Regression — run the live runtime test suite.**
```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 \
  python -m pytest backend/algo/live/tests/ backend/algo/tests/test_live_dry_run.py -q
```
Expected: all pass (stop-loss, MIS e2e, dry-run, qty-zero, and the new flush test). These drive `_on_bar_close` and the order paths, so a broken removal surfaces here. `test_live_qty_zero_rejection.py` calls `_maybe_emit_qty_zero_rejection` directly (unchanged) and does not reference `_evt_n_before`.

- [ ] **Step 6: Lint + commit.**
```bash
docker exec -i -w /app ai-agent-ui-backend-1 flake8 backend/algo/live/runtime.py
git add backend/algo/live/runtime.py
git commit -m "feat(algo): PR3.2 — batch live flushes (remove per-event commits)"
```

---

## Task 3: Integration verification (commit-rate drop)

**Files:** none (verification). **Requires a backend restart — controller MUST get explicit user confirmation first (standing instruction: do not restart without confirming).**

- [ ] **Step 1: With user confirmation, restart** `./run.sh restart backend`; wait for `Application startup complete`. (The running process caches the old runtime module — §6.2.)

- [ ] **Step 2: With a live run active for ~3 min, measure the commit (snapshot) delta.** Run before and after a ~3-min window:
```bash
docker exec -i -w /app -e PYTHONPATH=/app:/app/backend ai-agent-ui-backend-1 python3 -c "
from stocks.create_tables import _get_catalog
print('snapshots:', len(list(_get_catalog().load_table('algo.events').metadata.snapshots)))
"
```
And the flushed-row log rate:
```bash
docker logs --since 180s ai-agent-ui-backend-1 2>&1 | grep -c "flushed.*algo.events rows"
```
Expected: low single digits over 3 min (was effectively one per signal before).

- [ ] **Step 3: Confirm panel latency.** Generate/observe a signal and confirm it appears in `GET /v1/algo/paper/events?mode=live` within ~5s. (Manual / UI check.)

- [ ] **Step 4: Mark PR3 done.** Edit `docs/plans/2026-06-18-algo-events-bloat-redesign.md` PR 3 section → **DONE** with the PR3.1/3.2 commit shas.
```bash
git add docs/plans/2026-06-18-algo-events-bloat-redesign.md
git commit -m "docs(algo): mark PR3 (batch live flushes) done"
```

---

## Self-Review

**1. Spec coverage** (PR 3 in `docs/plans/2026-06-18-algo-events-bloat-redesign.md`):
- "Remove per-`signal_generated` `_flush_events_now()`; flush on min(timer, …)" → Task 2 Step 4 (signal_generated flush removed) + Task 1 (periodic timer). ✓
- "Fold in the qty=0 rejection flush — it must batch, not flush per-event" → Task 2 Step 3 (removes the `_on_bar_close` qty=0 immediate flush; the event still appends and rides the timer). ✓
- "Real-time panel latency: signals appear within a few seconds" → 5s timer (Design note documents the choice over the Redis cache). ✓
- "Acceptance: commits/session drop to single digits" → Task 3 Step 2 (snapshot-delta / flushed-count check). ✓

**2. Placeholder scan:** none. Every step is a concrete edit, command, or expected output. Task 2 Steps 3–4 are deletions located by quoted code/anchors; the post-removal `grep` (Step 4) pins the end state.

**3. Type/name consistency:** `_EVENT_FLUSH_INTERVAL_S` (constant), `_event_flush_task` (attr), `_periodic_event_flush` (method) are referenced identically in Task 1 (define), Task 2 (start/cancel), and the test. The method calls the existing `_flush_events_now()` (unchanged signature). `os` and `asyncio` are already imported in `runtime.py`; `time` is NOT needed (uses `asyncio.sleep`).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-18-pr3-batch-live-flushes.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks. Tasks 1–2 are pure code/tests (safe anytime); Task 3 needs a user-confirmed restart + a live run.
2. **Inline Execution** — execute in this session with checkpoints.

Which approach?

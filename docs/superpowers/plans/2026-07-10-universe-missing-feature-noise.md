# Universe Missing-Feature Noise — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `signal_rejected reason=missing_feature` noise for tickers that
are structurally outside a live strategy's tradeable scope (ASETPLTFRM-471),
and surface a warning when a user adds such a ticker to a strategy's
`allowed_tickers` in the first place, so the gap doesn't keep recurring.

**Architecture:** Two independent backend fixes plus one frontend surface
change, all sharing the same root cause: `stocks.universe_snapshot` is a
liquidity/fundamentals-screened subset of NSE tickers (ADTV ≥ ₹10cr/60d AND
market cap ≥ ₹500cr — ETFs never qualify, they have no Piotroski market-cap
row). Nothing stops a ticker outside that screen from landing in a live
strategy's `allowed_tickers`, and today the live runtime doesn't distinguish
"this ticker will structurally never trade" from "a real feature-wiring bug",
so it logs/emits the same noisy event forever. Part A silences the noise at
the runtime level (bug fix, no product decision needed). Part B adds a
non-blocking warning at the input point (`PUT /algo/live/caps/{id}`) so users
see the mismatch before it ever reaches the runtime.

**Tech Stack:** Python 3.12 / FastAPI / pytest-asyncio (backend), Next.js 16 /
React 19 / vitest + testing-library (frontend). No new dependencies.

## Global Constraints

- Line length 79 chars (black/isort/flake8).
- `X | None`, not `Optional[X]`.
- No bare `except:` — `except Exception` or specific.
- Tools/helpers that hit Iceberg via DuckDB must fail open (never raise into
  a caller that only wants a soft warning) and must log with the module
  logger, never `print()`.
- Every new interactive/warning UI element needs a `data-testid` if it's
  something E2E might assert on later (not required for a pure `title=`
  tooltip on an existing tested chip).
- Do NOT restart the backend without asking the user first — a running live
  Kite WS session would be killed. Confirm before any `docker compose
  restart backend`.
- Branch: continue on the current branch
  (`docs/capture-learnings-column-selector-bucket-gate`) — user has
  explicitly said to leave the pre-existing unrelated WIP (GTT-poll cooldown
  tracking in `runtime.py` L1682-2150, `webhooks.py`, `LiveSafetyBeltsForm.tsx`
  L268-274) untouched and proceed alongside it. Do not touch those lines.

---

## Task 1: `PositionTracker.has_position()` — O(1) membership check

**Files:**
- Modify: `backend/algo/backtest/positions.py:141` (add method after
  `open_positions`)
- Test: `backend/algo/backtest/tests/test_positions.py` (create if it
  doesn't exist; check first)

**Interfaces:**
- Produces: `PositionTracker.has_position(ticker: str) -> bool` — Task 2
  calls this instead of `ticker not in self._positions.open_positions()`
  (which does `dict(self._open)`, a full copy, on every call — wasteful once
  Task 2 makes this a per-bar-per-ticker hot-path check instead of a
  once-per-ticker check).

- [ ] **Step 1: Check for an existing test file**

Run: `ls backend/algo/backtest/tests/test_positions.py 2>&1 || echo "no file"`

If it exists, read it first to match its existing style/fixtures before
adding a new test class. If not, create it fresh per Step 2.

- [ ] **Step 2: Write the failing test**

If `test_positions.py` does not exist, create it:

```python
"""Unit tests for PositionTracker."""
from __future__ import annotations

from decimal import Decimal

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill


def _buy_fill(ticker: str, qty: int = 10) -> Fill:
    return Fill(
        ticker=ticker,
        side="BUY",
        qty=qty,
        price=Decimal("100.00"),
        fee=Decimal("0"),
        ts_ns=1_000_000_000,
    )


class TestHasPosition:
    def test_true_for_open_position(self):
        tracker = PositionTracker()
        tracker.apply_fill(_buy_fill("ITC.NS"))
        assert tracker.has_position("ITC.NS") is True

    def test_false_for_never_traded_ticker(self):
        tracker = PositionTracker()
        assert tracker.has_position("NEVER.NS") is False

    def test_matches_open_positions_membership(self):
        tracker = PositionTracker()
        tracker.apply_fill(_buy_fill("ITC.NS"))
        assert tracker.has_position("ITC.NS") == (
            "ITC.NS" in tracker.open_positions()
        )
```

If `test_positions.py` already exists, add the `TestHasPosition` class (with
whatever `Fill`-construction helper the existing file already uses — check
its actual field names/signature before copying the snippet above verbatim,
since `Fill`'s exact constructor args may differ from this sketch).

- [ ] **Step 3: Run test to verify it fails**

Run: `docker compose exec backend python -m pytest backend/algo/backtest/tests/test_positions.py -v -k HasPosition`
Expected: FAIL with `AttributeError: 'PositionTracker' object has no attribute 'has_position'`

- [ ] **Step 4: Implement**

In `backend/algo/backtest/positions.py`, immediately after the
`open_positions` method (currently ends at line 142 `return dict(self._open)`):

```python
    def has_position(self, ticker: str) -> bool:
        """O(1) membership check — prefer over
        ``ticker in open_positions()`` in a per-bar hot path, since
        ``open_positions()`` copies the whole dict on every call."""
        return ticker in self._open
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose exec backend python -m pytest backend/algo/backtest/tests/test_positions.py -v -k HasPosition`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/algo/backtest/positions.py backend/algo/backtest/tests/test_positions.py
git commit -m "feat(algo): add PositionTracker.has_position() O(1) check"
```

---

## Task 2: Stop repeating `signal_rejected reason=missing_feature` for out-of-scope tickers (ASETPLTFRM-471)

**Files:**
- Modify: `backend/algo/live/runtime.py:3462-3522` (`_on_bar_close`)
- Test: `backend/algo/live/tests/test_bucket_gate_allowed_ticker.py` (extend
  the existing file from PR #307 — same fixtures, same scenario family)

**Interfaces:**
- Consumes: `PositionTracker.has_position()` from Task 1.
- Produces: no new public interface — this is a behavior-only fix inside
  `_on_bar_close`.

### Root cause recap (already verified against current code)

`_on_bar_close` (`backend/algo/live/runtime.py:3421`) has a gate at
line ~3513 that only runs **once per ticker**, guarded by
`if history is None:` (line 3483). The very first bar for a ticker that's
absent from `_bucket_by_ticker` (i.e. absent from `stocks.universe_snapshot`)
AND has no open position AND isn't in the strategy's `allowed_tickers`
correctly sets `_bars_by_ticker[ticker] = []` and returns 0 — but every
**subsequent** bar for that same ticker finds `history` is no longer `None`
(it's `[]`), skips the gate entirely, and falls through to full feature
assembly + `eval_node`, which raises `KeyError` on any bar-derived feature
(e.g. `rsi_2`) and emits a fresh `signal_rejected reason=missing_feature`
WARNING + `algo.events` row (lines 3866-3894) — every single eval cycle,
forever. Confirmed live 2026-07-06 for `AHLUCONT.NS` / `PRUDENT.NS` (already
filed as ASETPLTFRM-471, status To Do).

### Fix

Move the out-of-scope check out of the once-per-ticker `if history is None:`
block into a **top-of-function** check that runs on every bar-close, so an
out-of-scope ticker short-circuits before touching `_bars_by_ticker`,
feature assembly, or `eval_node` at all — every time, not just the first.

- [ ] **Step 1: Write the failing test**

Open `backend/algo/live/tests/test_bucket_gate_allowed_ticker.py`. It already
has `_make_runtime()`, `_make_bar()`, and the `_force_eval_gate_open`
fixture — reuse them. The existing strategy payload
(`_strategy_payload()`, lines 60-84) uses a bare `{"type": "buy", ...}`
root with no feature conditions, so it can never hit the `KeyError` path —
add a **second** strategy-payload helper for this test that has an AST
condition referencing `rsi_2` (the same pattern
`backend/algo/live/tests/test_live_order_gate.py:61-71` uses), and a second
`_make_runtime`-style constructor that accepts a strategy payload override.

Add this to the bottom of the file:

```python
def _rsi_condition_strategy_payload() -> dict:
    """Same shape as _strategy_payload() but with an AST condition
    that references a bar-derived feature (rsi_2), so eval_node
    actually raises KeyError when features are empty — the bare
    {"type": "buy"} root in _strategy_payload() never exercises that
    path at all."""
    payload = _strategy_payload()
    payload["root"] = {
        "type": "if",
        "cond": {
            "type": "compare",
            "op": "<=",
            "left": {"feature": "rsi_2"},
            "right": {"literal": 5},
        },
        "then": {"type": "buy", "qty": {"shares": 1}},
        "else": {"type": "hold"},
    }
    return payload


def _make_runtime_with_payload(payload: dict):
    from backend.algo.live.runtime import LiveRuntime
    from backend.algo.strategy.ast import parse_strategy

    strategy = parse_strategy(payload)

    caps_repo = AsyncMock()
    caps_repo.get.return_value = {
        "live_orders_enabled": True,
        "max_inr": Decimal("10000000"),
        "max_orders_per_day": 100,
        "allowed_tickers": [],
        "cumulative_inr_today": Decimal("0"),
        "orders_count_today": 0,
    }
    caps_repo.update_in_flight = AsyncMock()
    caps_repo.increment_daily_counters = AsyncMock()

    kill_switch_repo = AsyncMock()
    kill_switch_repo.is_active.return_value = False

    kite = MagicMock()
    kite.dry_run = False
    kite.place_order = MagicMock(return_value="KITE_ORDER_TEST")

    caps = {"live_orders_enabled": True, "allowed_tickers": []}

    with patch(
        "backend.algo.live.position_hydration.hydrate",
        return_value=[],
    ), patch(
        "backend.algo.live.runtime.LiveRuntime._load_bucket_by_ticker",
        return_value={},
    ):
        runtime = LiveRuntime(
            strategy=strategy,
            user_id=uuid4(),
            initial_capital_inr=Decimal("500000"),
            fee_as_of=date(2026, 7, 4),
            kite=kite,
            caps=caps,
            run_id=uuid4(),
            caps_repo=caps_repo,
            kill_switch_repo=kill_switch_repo,
        )
    return runtime


@pytest.mark.asyncio
async def test_out_of_scope_ticker_never_reaches_eval_on_repeat_bars():
    """ASETPLTFRM-471. A ticker that is NEITHER in _bucket_by_ticker
    NOR in allowed_tickers NOR an open position must never emit
    signal_rejected reason=missing_feature — not on the first bar
    (already covered by the control test above) and NOT on any
    subsequent bar either. Before the fix, only the first bar was
    gated; every bar after that fell through to eval_node and
    emitted a fresh missing_feature event every single cycle."""
    runtime = _make_runtime_with_payload(
        _rsi_condition_strategy_payload(),
    )
    out_of_scope_ticker = "MOVALUE.NS"
    assert out_of_scope_ticker not in runtime._bucket_by_ticker
    assert out_of_scope_ticker not in (
        runtime._caps.get("allowed_tickers") or []
    )

    with patch(
        "backend.algo.live.daily_bar_warmup.preload_daily_bars",
    ) as mock_preload:
        # First bar.
        result_1 = await runtime._on_bar_close(
            bar=_make_bar(out_of_scope_ticker), last_price=_PRICE,
        )
        # Second bar for the SAME ticker — this is the case that
        # was broken: history is no longer None, so the old gate
        # (nested inside `if history is None:`) never re-ran.
        result_2 = await runtime._on_bar_close(
            bar=_make_bar(out_of_scope_ticker), last_price=_PRICE,
        )

    assert result_1 == 0
    assert result_2 == 0
    mock_preload.assert_not_called()
    missing_feature_events = [
        e for e in runtime._events
        if e.get("type") == "signal_rejected"
        and e.get("payload", {}).get("reason") == "missing_feature"
    ]
    assert missing_feature_events == [], (
        "out-of-scope ticker emitted signal_rejected "
        "reason=missing_feature — the per-bar short-circuit did not "
        "fire on a repeat bar-close"
    )
```

Before writing this, run
`docker compose exec backend python -c "from backend.algo.live.runtime import event_row; import inspect; print(inspect.signature(event_row))"`
to confirm the actual keys `event_row(...)` puts on each dict (`type` vs
`type_`, whether `payload` is nested) — adjust the assertion above to match
reality if the shape differs from what's assumed here.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_bucket_gate_allowed_ticker.py -v -k out_of_scope_ticker_never_reaches_eval`
Expected: FAIL — `missing_feature_events` is non-empty after the second
`_on_bar_close` call (the bug).

- [ ] **Step 3: Implement the fix**

In `backend/algo/live/runtime.py`, in `_on_bar_close`, immediately after the
line `strategy_interval = self._strategy.schedule.interval` (currently line
3462) and before the `if strategy_interval == "1d": bucket_key = ...` block,
insert:

```python
        # ASETPLTFRM-471 — a ticker can be ticked here purely because
        # it's in the user's watchlist/holdings (the live-WS
        # subscription scope; see routes/paper.py's
        # ``_scoped_tickers(user, "watchlist")``) while never having
        # been part of THIS strategy's own ``allowed_tickers`` and
        # also absent from the liquidity-screened
        # ``stocks.universe_snapshot`` (``_bucket_by_ticker``). Such a
        # ticker can never populate a bar-derived feature. The
        # PR #307 gate below (inside ``if history is None:``) already
        # recognizes this on the ticker's FIRST bar, but that gate
        # only ever runs once — every bar after the first fell
        # through to full feature assembly + eval_node, hitting the
        # SAME KeyError and emitting a fresh
        # ``signal_rejected reason=missing_feature`` WARNING +
        # algo.events row every single eval cycle, forever (confirmed
        # live 2026-07-06, AHLUCONT.NS / PRUDENT.NS recurring every
        # ~60s). Checking on EVERY bar-close closes this —
        # ``self._caps`` is refreshed each bar-close, so a ticker
        # added to allowed_tickers mid-run still reaches the real
        # preload attempt below on its very next bar.
        if (
            strategy_interval == "1d"
            and bar.ticker not in self._bucket_by_ticker
            and not self._positions.has_position(bar.ticker)
            and bar.ticker
            not in (self._caps.get("allowed_tickers") or [])
        ):
            self._bars_by_ticker.setdefault(bar.ticker, [])
            return 0

```

Then simplify the now-partially-dead inner gate. Replace (currently lines
3483-3522, the `if history is None:` block's opening comment + nested
`if (...): ... return 0` — everything from `if history is None:` through the
line `self._bars_by_ticker[bar.ticker] = []` / `return 0` inclusive) with:

```python
        history = self._bars_by_ticker.get(bar.ticker)
        if history is None:
            # The out-of-scope short-circuit above already returns
            # early for any ticker that's neither in
            # _bucket_by_ticker, an open position, nor in
            # allowed_tickers — so anything reaching this point for
            # strategy_interval == "1d" always warrants a real
            # preload attempt. (Intraday strategies have no
            # equivalent short-circuit above — unchanged from prior
            # behavior — so this preload always fires for them too.)
            try:
```

i.e. delete the big explanatory comment block and the nested
`if (...): self._bars_by_ticker[bar.ticker] = []; return 0` — keep
everything from `try:` onward (the `preload_daily_bars` /
`preload_intraday_bars` call) exactly as-is.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/test_bucket_gate_allowed_ticker.py -v`
Expected: PASS — all 3 tests (2 existing + 1 new).

- [ ] **Step 5: Run the full live-runtime test suite to check for regressions**

Run: `docker compose exec backend python -m pytest backend/algo/live/tests/ backend/algo/tests/test_live_runtime_allow_list_staleness.py backend/algo/tests/test_live_runtime_gtt_headroom_staleness.py -v`
Expected: PASS, no new failures. If anything relies on the removed inner
comment/branch by name (unlikely — it's dead code once the top-level check
is in place) investigate before proceeding.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/live/runtime.py backend/algo/live/tests/test_bucket_gate_allowed_ticker.py
git commit -m "fix(algo): stop repeating missing_feature noise for out-of-scope tickers (ASETPLTFRM-471)"
```

---

## Task 3: `get_off_universe_tickers()` helper

**Files:**
- Create: `backend/algo/universe/membership.py`
- Test: `backend/algo/universe/tests/test_membership.py` (check if
  `backend/algo/universe/tests/` exists first; create `__init__.py` if the
  dir doesn't exist and the sibling `pit_resolver`/`snapshot_job` tests live
  elsewhere — check with
  `find backend/algo/universe -name "test_*" -o -name "__init__.py"` first)

**Interfaces:**
- Produces: `get_off_universe_tickers(tickers: list[str]) -> list[str]` —
  Task 4's route handler calls this with a strategy's `allowed_tickers`.

- [ ] **Step 1: Check test directory layout**

Run: `find backend/algo/universe -type f`

Match whatever pattern already exists for this package's tests (a
`tests/` subpackage vs. flat files vs. tests living in
`backend/algo/tests/`) — adjust the file path below if the convention
differs from `backend/algo/universe/tests/test_membership.py`.

- [ ] **Step 2: Write the failing test**

```python
"""Unit tests for get_off_universe_tickers()."""
from __future__ import annotations

from unittest.mock import patch

from backend.algo.universe.membership import get_off_universe_tickers


class TestGetOffUniverseTickers:
    def test_empty_input_returns_empty(self):
        assert get_off_universe_tickers([]) == []

    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_ticker_in_universe_not_flagged(self, mock_query):
        mock_query.return_value = [
            {"ticker": "ITC.NS"}, {"ticker": "TCS.NS"},
        ]
        result = get_off_universe_tickers(["ITC.NS"])
        assert result == []

    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_ticker_absent_from_universe_flagged(self, mock_query):
        mock_query.return_value = [{"ticker": "ITC.NS"}]
        result = get_off_universe_tickers(
            ["ITC.NS", "MOVALUE.NS", "SMALLCAP.NS"],
        )
        assert result == ["MOVALUE.NS", "SMALLCAP.NS"]

    @patch("backend.algo.universe.membership.query_iceberg_table")
    def test_query_failure_fails_open(self, mock_query):
        mock_query.side_effect = RuntimeError("duckdb boom")
        result = get_off_universe_tickers(["ANYTHING.NS"])
        assert result == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `docker compose exec backend python -m pytest backend/algo/universe/tests/test_membership.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.algo.universe.membership'`

- [ ] **Step 4: Implement**

Create `backend/algo/universe/membership.py`:

```python
"""Current stocks.universe_snapshot ticker membership — the SAME
set LiveRuntime._bucket_by_ticker treats as tradeable (any row with
a non-null liquidity_bucket at any rebalance, not just the latest
top-200 cohort). Used to warn (not block) when a user adds a ticker
to a strategy's allowed_tickers that can never satisfy this set —
see ASETPLTFRM-471.
"""
from __future__ import annotations

import logging

from backend.db.duckdb_engine import query_iceberg_table

_logger = logging.getLogger(__name__)


def get_off_universe_tickers(tickers: list[str]) -> list[str]:
    """Subset of ``tickers`` absent from ``stocks.universe_snapshot``.

    Fail-open: returns ``[]`` on an empty input or a query failure —
    this drives a soft UI warning, never a hard block, so silently
    under-warning on a transient DuckDB hiccup is the safe failure
    mode (never falsely flag every ticker as off-universe).
    """
    if not tickers:
        return []
    try:
        rows = query_iceberg_table(
            "stocks.universe_snapshot",
            "SELECT DISTINCT ticker FROM universe_snapshot "
            "WHERE liquidity_bucket IS NOT NULL",
            [],
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "get_off_universe_tickers: query failed: %s — "
            "skipping off-universe check (fail-open)",
            exc,
        )
        return []
    in_universe = {r["ticker"] for r in rows if r.get("ticker")}
    return sorted(t for t in tickers if t not in in_universe)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose exec backend python -m pytest backend/algo/universe/tests/test_membership.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/algo/universe/membership.py backend/algo/universe/tests/test_membership.py
git commit -m "feat(algo): add get_off_universe_tickers() helper"
```

---

## Task 4: Surface `off_universe_tickers` on the caps route

**Files:**
- Modify: `backend/algo/routes/live.py:137-151` (`CapsResponse`, `get_caps`,
  `upsert_caps`)
- Test: `backend/algo/tests/test_live_caps_off_universe_warning.py` (create)

**Interfaces:**
- Consumes: `get_off_universe_tickers(tickers: list[str]) -> list[str]`
  from Task 3.
- Produces: `CapsResponse.off_universe_tickers: list[str]` — Task 5's
  frontend hook type mirrors this field name exactly.

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for off_universe_tickers on GET/PUT
/v1/algo/live/caps/{strategy_id} — ASETPLTFRM-471 input-side
warning: a ticker added to allowed_tickers that's absent from
stocks.universe_snapshot can never populate a bar-derived feature.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import UUID

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
_STRATEGY_ID = UUID("33333333-3333-3333-3333-333333333333")
_USER_CTX = UserContext(
    user_id=str(_USER_ID), email="t@t.com", role="pro",
)


def _app():
    from fastapi import FastAPI
    from backend.algo.routes.live import create_live_router

    app = FastAPI()
    app.include_router(create_live_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: _USER_CTX
    return app


class TestOffUniverseWarning:
    @patch(
        "backend.algo.routes.live.get_off_universe_tickers",
    )
    @patch(
        "backend.algo.routes.live._compute_strategy_commitment",
    )
    @patch("backend.algo.live.caps_repo.CapsRepo.get_or_default")
    def test_get_caps_includes_off_universe_tickers(
        self, mock_get, mock_commitment, mock_off_universe,
    ):
        from fastapi.testclient import TestClient

        mock_get.return_value = {
            "user_id": _USER_ID,
            "strategy_id": _STRATEGY_ID,
            "max_inr": Decimal("100000"),
            "max_orders_per_day": 5,
            "allowed_tickers": ["ITC.NS", "MOVALUE.NS"],
            "live_orders_enabled": False,
            "gtt_limit_headroom_pct": Decimal("0.01"),
        }
        mock_commitment.return_value = (Decimal("0"), 0)
        mock_off_universe.return_value = ["MOVALUE.NS"]

        client = TestClient(_app())
        resp = client.get(
            f"/v1/algo/live/caps/{_STRATEGY_ID}",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["off_universe_tickers"] == ["MOVALUE.NS"]
        mock_off_universe.assert_called_once_with(
            ["ITC.NS", "MOVALUE.NS"],
        )

    @patch(
        "backend.algo.routes.live.get_off_universe_tickers",
    )
    @patch("backend.algo.live.caps_repo.CapsRepo.upsert")
    def test_upsert_caps_includes_off_universe_tickers(
        self, mock_upsert, mock_off_universe,
    ):
        from fastapi.testclient import TestClient

        mock_upsert.return_value = {
            "user_id": _USER_ID,
            "strategy_id": _STRATEGY_ID,
            "max_inr": Decimal("100000"),
            "max_orders_per_day": 5,
            "allowed_tickers": ["SMALLCAP.NS"],
            "live_orders_enabled": False,
            "gtt_limit_headroom_pct": Decimal("0.01"),
            "cumulative_inr_today": Decimal("0"),
            "orders_count_today": 0,
        }
        mock_off_universe.return_value = ["SMALLCAP.NS"]

        client = TestClient(_app())
        resp = client.put(
            f"/v1/algo/live/caps/{_STRATEGY_ID}",
            json={
                "max_inr": "100000",
                "max_orders_per_day": 5,
                "allowed_tickers": ["SMALLCAP.NS"],
            },
        )

        assert resp.status_code == 200
        assert resp.json()["off_universe_tickers"] == ["SMALLCAP.NS"]
```

Check the exact import path for `CapsRepo` used inside
`backend/algo/routes/live.py`'s `get_caps`/`upsert_caps` (both currently do
`from backend.algo.live.caps_repo import CapsRepo` locally inside the
function body — confirm the `@patch` target string
`"backend.algo.live.caps_repo.CapsRepo.get_or_default"` / `.upsert` matches
that — patch the class where it's DEFINED per this repo's
`mock-patching-gotchas` convention, not where it's imported, since the
import is local to the function anyway).

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_live_caps_off_universe_warning.py -v`
Expected: FAIL — `KeyError: 'off_universe_tickers'` or a 500 (field not on
`CapsResponse` yet / `get_off_universe_tickers` not imported into
`backend.algo.routes.live`).

- [ ] **Step 3: Implement**

In `backend/algo/routes/live.py`:

1. Add the import near the top (with the other local-scope-friendly
   imports, or at module level next to existing `from backend.algo...`
   imports — check the file's existing import style at the top before
   picking module-level vs. function-local):

```python
from backend.algo.universe.membership import get_off_universe_tickers
```

2. Add the field to `CapsResponse` (currently ending at line 149
   `gtt_limit_headroom_pct: Decimal = Decimal("0.01")`):

```python
    gtt_limit_headroom_pct: Decimal = Decimal("0.01")
    off_universe_tickers: list[str] = Field(default_factory=list)
```

3. In `get_caps` (currently lines 1102-1125), after the `committed,
   open_count = await _compute_strategy_commitment(...)` call and before
   building `row = {...}`, add:

```python
        off_universe = get_off_universe_tickers(
            row.get("allowed_tickers") or [],
        )
```

   and add `"off_universe_tickers": off_universe,` as a key in the `row = {
   **row, ... }` dict update.

4. In `upsert_caps` (currently lines 1132-1151), after the `row = await
   repo.upsert(...)` call and before `return CapsResponse(...)`, add:

```python
        off_universe = get_off_universe_tickers(
            row.get("allowed_tickers") or [],
        )
        row = {**row, "off_universe_tickers": off_universe}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_live_caps_off_universe_warning.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the broader caps/route test suite for regressions**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_live_strategy_commitment.py backend/algo/tests/test_live_holdings.py -v`
Expected: PASS, no new failures.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/routes/live.py backend/algo/tests/test_live_caps_off_universe_warning.py
git commit -m "feat(algo): warn on off-universe tickers in live caps GET/PUT (ASETPLTFRM-471)"
```

---

## Task 5: Frontend — warn on off-universe ticker chips

**Files:**
- Modify: `frontend/hooks/useLiveCaps.ts:12-25` (`LiveCaps` interface)
- Modify: `frontend/components/algo-trading/LiveSafetyBeltsForm.tsx:14-131`
  (`TickerChip`, `TickerTagInput`, and the call site that renders the chip
  list — do NOT touch lines 268-274, the pre-existing unrelated
  `cumulative_inr_today` formatting diff already on this branch)
- Test: `frontend/components/algo-trading/__tests__/TickerChip.test.tsx`
  (create)

**Interfaces:**
- Consumes: `CapsResponse.off_universe_tickers` from Task 4 (same field
  name, surfaced through `LiveCaps.off_universe_tickers` in the hook).

- [ ] **Step 1: Add the field to the hook type**

In `frontend/hooks/useLiveCaps.ts`, in the `LiveCaps` interface (currently
lines 12-25, ending `gtt_limit_headroom_pct: number;`), add:

```typescript
  gtt_limit_headroom_pct: number;
  off_universe_tickers: string[];
}
```

- [ ] **Step 2: Write the failing test**

Create `frontend/components/algo-trading/__tests__/TickerChip.test.tsx`:

```tsx
/**
 * TickerChip — unit tests.
 *
 * Verifies:
 * 1. Normal chip renders the ticker with no warning styling.
 * 2. offUniverse=true renders the amber/red warning affordance +
 *    tooltip, mirroring the FeatureChip "unwired" convention.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { TickerChip } from "../LiveSafetyBeltsForm";

describe("TickerChip", () => {
  it("renders a plain chip with no warning for an in-universe ticker", () => {
    render(
      <TickerChip ticker="ITC.NS" offUniverse={false} onRemove={vi.fn()} />,
    );
    const chip = screen.getByText("ITC.NS");
    expect(chip).toBeDefined();
    expect(screen.queryByText("⚠")).toBeNull();
  });

  it("renders a warning affordance for an off-universe ticker", () => {
    render(
      <TickerChip
        ticker="MOVALUE.NS"
        offUniverse={true}
        onRemove={vi.fn()}
      />,
    );
    const chip = screen.getByTestId("live-caps-ticker-MOVALUE.NS");
    expect(chip.title).toContain("liquidity");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/algo-trading/__tests__/TickerChip.test.tsx`
Expected: FAIL — `TickerChip` is not exported from `LiveSafetyBeltsForm.tsx`
yet (it's currently a module-private function), and it doesn't accept an
`offUniverse` prop or have a `data-testid="live-caps-ticker-{ticker}"` on
the outer `<span>`.

- [ ] **Step 4: Implement**

In `frontend/components/algo-trading/LiveSafetyBeltsForm.tsx`, replace the
`TickerChip` function (currently lines 14-51) with:

```tsx
export function TickerChip({
  ticker,
  offUniverse,
  onRemove,
}: {
  ticker: string;
  offUniverse: boolean;
  onRemove: () => void;
}) {
  return (
    <span
      className={
        offUniverse
          ? "inline-flex items-center gap-1 rounded-full border " +
            "border-red-300 bg-red-50 pl-2.5 pr-1 py-0.5 text-[11px] " +
            "font-mono font-medium text-red-700 dark:border-red-700 " +
            "dark:bg-red-900/20 dark:text-red-400"
          : "inline-flex items-center gap-1 rounded-full border " +
            "border-indigo-200 bg-indigo-100 pl-2.5 pr-1 py-0.5 " +
            "text-[11px] font-mono font-medium text-indigo-800 " +
            "dark:border-indigo-700 dark:bg-indigo-900/40 " +
            "dark:text-indigo-300"
      }
      title={
        offUniverse
          ? "Not in the liquidity-screened trading universe " +
            "(ADTV/market-cap floor, or an ETF with no fundamentals " +
            "row). Bar-derived features can never populate for this " +
            "ticker — signals on it will always be rejected."
          : undefined
      }
      data-testid={`live-caps-ticker-${ticker}`}
    >
      {offUniverse ? "⚠ " : ""}
      {ticker}
      <button
        type="button"
        onClick={onRemove}
        className="ml-0.5 flex h-4 w-4 items-center justify-center
          rounded-full hover:bg-black/10 dark:hover:bg-white/15
          transition-colors"
        aria-label={`Remove ${ticker}`}
        data-testid={`live-caps-ticker-remove-${ticker}`}
      >
        <svg
          className="h-2.5 w-2.5"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
        >
          <path d="M18 6 6 18M6 6l12 12" />
        </svg>
      </button>
    </span>
  );
}
```

Then update `TickerTagInput` (currently lines 55-131) to accept and thread
through an `offUniverseTickers` prop. Change its signature (currently lines
55-61):

```tsx
function TickerTagInput({
  value,
  onChange,
  offUniverseTickers,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  offUniverseTickers: string[];
}) {
```

And update the chip-rendering map (currently lines 105-111):

```tsx
      {value.map((t) => (
        <TickerChip
          key={t}
          ticker={t}
          offUniverse={offUniverseTickers.includes(t)}
          onRemove={() => onChange(value.filter((x) => x !== t))}
        />
      ))}
```

Finally, find the call site inside `LiveSafetyBeltsForm` that renders
`<TickerTagInput value={tickerList} onChange={setTickerList} />` (currently
line 294) and pass the new prop:

```tsx
        <TickerTagInput
          value={tickerList}
          onChange={setTickerList}
          offUniverseTickers={caps?.off_universe_tickers ?? []}
        />
```

(Confirm the exact variable name holding the `useLiveCaps` result at that
call site — it's referenced as `caps` elsewhere in the file per the
existing `caps?.cumulative_inr_today` usage at line ~271; use whatever name
is actually in scope there.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run components/algo-trading/__tests__/TickerChip.test.tsx`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full algo-trading component test suite for regressions**

Run: `cd frontend && npx vitest run components/algo-trading/`
Expected: PASS, no new failures.

- [ ] **Step 7: Manual smoke test in the browser**

Start the frontend (`./run.sh status` to confirm it's already up — it was
at session start) and navigate to a strategy's Live Safety Belts panel.
Temporarily add a known off-universe ticker (e.g. `MOVALUE.NS` or
`SMALLCAP.NS`, confirmed absent from `universe_snapshot` earlier in this
investigation) to `Allowed tickers` and verify the amber/red warning chip
+ tooltip render. Remove it again before saving if this is a real user's
strategy — do not leave a test ticker in a live strategy's caps.

- [ ] **Step 8: Commit**

```bash
git add frontend/hooks/useLiveCaps.ts frontend/components/algo-trading/LiveSafetyBeltsForm.tsx frontend/components/algo-trading/__tests__/TickerChip.test.tsx
git commit -m "feat(algo): warn on off-universe tickers in Live Safety Belts UI (ASETPLTFRM-471)"
```

---

## Task 6: Close out ASETPLTFRM-471 in Jira

**Files:** none (Jira only)

- [ ] **Step 1: Add implementation comment**

Use `mcp__atlassian__jira_add_comment` on ASETPLTFRM-471 summarizing: root
cause confirmed (per-bar gate only ran once), fix (top-of-function
short-circuit re-checked every bar-close), PRs/commits, plus the
input-side warning added as a bonus preventive measure (off_universe_tickers
on the caps route + UI warning chip) so this class of mismatch surfaces at
add-time going forward.

- [ ] **Step 2: Transition to Done**

Use `mcp__atlassian__jira_transition_issue` with transition id `31` (Done,
per this project's `reference_jira` convention).

---

## Self-Review Notes

- **Spec coverage:** ASETPLTFRM-471's 3 acceptance criteria are covered:
  (1) no repeating WARNING/event for out-of-scope tickers → Task 2; (2)
  regression test for "WS-scoped but not allowed_tickers" → Task 2's new
  test; (3) genuine missing-feature detection for in-scope tickers is
  unchanged → Task 2 only adds a check that requires ALL THREE conditions
  (not in bucket, no position, not in allowed_tickers) to short-circuit, so
  any in-scope ticker still reaches `eval_node` exactly as before, and the
  existing `test_ticker_added_mid_run_still_gets_preloaded` regression test
  from PR #307 continues to guard that. The input-side validation (Task 3-5)
  was the user's explicitly-chosen second fix direction, scoped to the one
  endpoint that actually writes into a live-tradeable `allowed_tickers` list
  (not the general watchlist, which legitimately allows ETFs/illiquid names
  for research purposes outside of algo trading).
- **Fail-open discipline:** `get_off_universe_tickers` (Task 3) fails open
  on any query error — a soft UI warning must never become a false-positive
  storm from a transient DuckDB hiccup.
- **No backend restart assumed:** all new/changed Python is either
  interpreted at test time (pytest) or, for the route/runtime changes to
  actually take effect against a running dev backend, requires a restart —
  per this session's explicit instruction, do NOT restart without asking
  the user first. Flag this as the last step before calling the plan fully
  "live-verified."

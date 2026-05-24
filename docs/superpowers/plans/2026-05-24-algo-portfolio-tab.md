# Algo Portfolio Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third "Algo" tab to the dashboard's `WatchlistWidget` showing currently-open algo-attributed positions (intraday MIS + overnight CNC) with strategy + days-held.

**Architecture:** New `GET /v1/algo/portfolio/positions` endpoint runs Kite `positions()` + `holdings()` in parallel, joins `algo.events` attribution (all-time via a `since_date` kwarg added to the existing `_fetch_strategy_attribution`), drops bare Kite rows, computes `days_held` in IST, returns one unified list cached 60 s in Redis. Frontend hook + `AlgoPositionsTab` component lift into `WatchlistWidget` behind a `algoTabEnabled` prop computed from `useProfile().role`.

**Tech Stack:** Python 3.12 (FastAPI, SQLAlchemy 2.0 async, pyarrow/iceberg), Next.js 16 + React 19 (Vitest, SWR), Playwright (E2E).

**Reference spec:** `docs/superpowers/specs/2026-05-24-algo-portfolio-tab-design.md`.

**Branch:** `feature/algo-portfolio-tab` (already created off `dev`). Squash merge per CLAUDE.md §4.4 #27.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `backend/algo/routes/portfolio.py` | create | New endpoint module — `AlgoPositionRow` / `AlgoPositionsResponse` models + `_get_algo_positions_impl` + `create_portfolio_router()` |
| `backend/algo/routes/live.py` | modify | Add `since_date: str \| None = None` kwarg to `_fetch_strategy_attribution`; default preserves existing behavior |
| `backend/algo/routes/__init__.py` | modify | Export `create_portfolio_router` |
| `backend/routes.py` | modify | `app.include_router(create_portfolio_router(), prefix="/v1")` |
| `backend/algo/tests/test_portfolio_routes.py` | create | 6 unit tests on the `_impl` functions |
| `frontend/lib/types/algoPortfolio.ts` | create | `AlgoPositionView` + `AlgoPositionsResponse` TS shapes |
| `frontend/hooks/useAlgoPositions.ts` | create | SWR hook, 5s/60s refresh |
| `frontend/components/widgets/algo/AlgoPositionsTab.tsx` | create | Tab body — table + empty state + loading + error |
| `frontend/components/widgets/algo/AlgoPositionRow.tsx` | create | One row component |
| `frontend/components/widgets/WatchlistWidget.tsx` | modify | Third tab button + body branch; new `algoTabEnabled` prop |
| `frontend/app/(authenticated)/dashboard/DashboardClient.tsx` | modify | Compute `algoTabEnabled` from `useProfile().role`; pass down |
| `frontend/components/widgets/algo/__tests__/AlgoPositionsTab.test.tsx` | create | 3 vitest tests |
| `frontend/components/widgets/__tests__/WatchlistWidget.algo-tab.test.tsx` | create | 1 vitest test (tab button hidden when `algoTabEnabled=false`) |
| `e2e/utils/selectors.ts` | modify | Add 4 testids |
| `e2e/tests/frontend/dashboard-algo-tab.spec.ts` | create | Smoke test |
| `PROGRESS.md` | modify | Dated session entry |

---

## Task 1: Backend Pydantic types + extend `_fetch_strategy_attribution`

**Files:**
- Modify: `backend/algo/routes/live.py` — add `since_date: str | None = None` kwarg to `_fetch_strategy_attribution`
- Create: `backend/algo/routes/portfolio.py` — types only at this step (no route yet)
- Create: `backend/algo/tests/test_portfolio_routes.py` — type-roundtrip test only

- [ ] **Step 1.1: Read the existing helper to confirm signature**

```bash
sed -n '484,580p' backend/algo/routes/live.py
```

Expected: function `_fetch_strategy_attribution(user_id: UUID, symbols: list[str]) -> dict[str, dict[str, Any]]`. Body currently hardcodes `today_ist = _ist_midnight_str()` and filters `ts_date >= ?`.

- [ ] **Step 1.2: Extend `_fetch_strategy_attribution` with `since_date` kwarg**

Edit `backend/algo/routes/live.py`. Locate `async def _fetch_strategy_attribution(`. Replace the function signature and the `today_ist = _ist_midnight_str()` line:

```python
async def _fetch_strategy_attribution(
    user_id: UUID,
    symbols: list[str],
    *,
    since_date: str | None = None,
) -> dict[str, dict[str, Any]]:
    """For each symbol, find the first live BUY fill in
    ``algo.events`` since ``since_date`` (default: today IST).

    Pass ``since_date=None`` (default) to preserve today-only
    behavior for the existing LiveDashboard positions endpoint.
    Pass a YYYY-MM-DD string (e.g. ``"2024-01-01"``) to widen
    the lookback — used by the dashboard Algo tab to attribute
    CNC overnight holdings opened on prior trading days.
    """
    if not symbols:
        return {}
    wanted_symbols = set(symbols)
    cutoff_date = since_date or _ist_midnight_str()
    try:
        rows = await asyncio.to_thread(
            query_iceberg_table,
            "algo.events",
            "SELECT event_id, ts_ns, ts_date, strategy_id, "
            "       payload_json "
            "FROM events "
            "WHERE user_id = ? "
            "  AND mode = 'live' "
            "  AND type = 'order_filled_live' "
            "  AND ts_date >= ? "
            "ORDER BY ts_ns ASC",
            [str(user_id), cutoff_date],
        )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "attribution read failed",
            exc_info=True,
        )
        return {}

    # First BUY per symbol wins. (existing comment block below
    # remains unchanged.)
    by_key: dict[str, dict[str, Any]] = {}
    ...rest of function unchanged...
```

Keep the rest of the body (the BUY-per-symbol loop and the `_strategy_name_lookup` resolution) exactly as it is.

- [ ] **Step 1.3: Run regression on the existing live tests**

```bash
docker compose exec backend python -m pytest \
  backend/algo/tests/test_live_pre_trade_check.py \
  backend/algo/tests/test_live_kill_switch.py \
  -v 2>&1 | tail -5
```

Expected: all green. The kwarg-only `since_date` defaults preserve existing callers.

- [ ] **Step 1.4: Write Pydantic types in `backend/algo/routes/portfolio.py`**

Create `backend/algo/routes/portfolio.py`:

```python
"""Algo Portfolio dashboard tab — GET
/v1/algo/portfolio/positions.

Returns currently-open algo-attributed positions (intraday
MIS from kc.positions().net + overnight CNC from
kc.holdings()), joined with strategy attribution and
augmented with days_held + t1_pending flags.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# Lookback window for joining algo.events attribution.
# 365 days covers any CNC overnight position that has been
# held since the launch of v1 algo trading. Plenty of margin.
_ATTRIBUTION_SINCE = "2024-01-01"

# Redis cache TTL (matches the existing
# /v1/algo/live/positions endpoint behavior — TTL-only, no
# write-through invalidation in v1).
_CACHE_TTL_S = 60


class AlgoPositionRow(BaseModel):
    """One algo-attributed open position."""

    model_config = ConfigDict(extra="forbid")

    tradingsymbol: str
    internal_ticker: str
    product: Literal["MIS", "CNC"]
    quantity: int = Field(ge=0)
    avg_price: Decimal = Field(ge=Decimal("0"))
    last_price: Decimal = Field(ge=Decimal("0"))
    pnl_inr: Decimal
    pnl_pct: Decimal
    strategy_id: UUID
    strategy_name: str
    entry_ts: datetime | None = None
    days_held: int = Field(ge=0)
    t1_pending: bool = False


class AlgoPositionsResponse(BaseModel):
    """Wire shape for the dashboard Algo tab."""

    model_config = ConfigDict(extra="forbid")

    positions: list[AlgoPositionRow]
    as_of: datetime
    market_open: bool


def _days_held(entry_ts: datetime | None) -> int:
    """Floor(today_ist - entry_date_ist).days, clamped ≥ 0."""
    if entry_ts is None:
        return 0
    today_ist = datetime.now(_IST).date()
    entry_ist = entry_ts.astimezone(_IST).date()
    return max(0, (today_ist - entry_ist).days)
```

- [ ] **Step 1.5: Write a type-roundtrip test**

Create `backend/algo/tests/test_portfolio_routes.py`:

```python
"""Tests for /v1/algo/portfolio/positions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.algo.routes.portfolio import (
    AlgoPositionRow,
    AlgoPositionsResponse,
    _days_held,
)


_IST = ZoneInfo("Asia/Kolkata")


def test_algo_position_row_minimal_valid():
    row = AlgoPositionRow(
        tradingsymbol="INFY",
        internal_ticker="INFY.NS",
        product="MIS",
        quantity=50,
        avg_price=Decimal("1500.00"),
        last_price=Decimal("1572.50"),
        pnl_inr=Decimal("3625.00"),
        pnl_pct=Decimal("4.83"),
        strategy_id=uuid4(),
        strategy_name="RSI(2) v3",
        entry_ts=datetime.now(timezone.utc),
        days_held=0,
    )
    assert row.t1_pending is False
    assert row.product == "MIS"


def test_days_held_returns_zero_for_today_ist():
    # An entry_ts in today's IST date (e.g. now) → 0 days held.
    today_ist_midnight_utc = (
        datetime.now(_IST)
        .replace(hour=10, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )
    assert _days_held(today_ist_midnight_utc) == 0


def test_days_held_returns_three_for_three_ist_days_ago():
    # 3 IST calendar days ago (any time of day) → 3.
    ts = (
        datetime.now(_IST)
        - timedelta(days=3)
    ).replace(hour=10, minute=0).astimezone(timezone.utc)
    assert _days_held(ts) == 3


def test_days_held_zero_when_entry_ts_none():
    assert _days_held(None) == 0


def test_response_roundtrip():
    row = AlgoPositionRow(
        tradingsymbol="TCS",
        internal_ticker="TCS.NS",
        product="CNC",
        quantity=20,
        avg_price=Decimal("3450.50"),
        last_price=Decimal("3401.20"),
        pnl_inr=Decimal("-986.00"),
        pnl_pct=Decimal("-1.43"),
        strategy_id=uuid4(),
        strategy_name="Mean Rev MIS",
        entry_ts=None,
        days_held=0,
    )
    resp = AlgoPositionsResponse(
        positions=[row],
        as_of=datetime.now(timezone.utc),
        market_open=False,
    )
    assert len(resp.positions) == 1
    assert resp.market_open is False
```

- [ ] **Step 1.6: Run the new tests**

```bash
docker compose exec backend python -m pytest \
  backend/algo/tests/test_portfolio_routes.py -v
```

Expected: 5 passed.

- [ ] **Step 1.7: Commit**

```bash
git add backend/algo/routes/live.py \
        backend/algo/routes/portfolio.py \
        backend/algo/tests/test_portfolio_routes.py
git commit -m "$(cat <<'EOF'
feat(algo-portfolio): Pydantic types + attribution lookback

Adds the Pydantic models (AlgoPositionRow,
AlgoPositionsResponse) + _days_held helper that the new
GET /v1/algo/portfolio/positions endpoint will use.

Extends _fetch_strategy_attribution with an optional
since_date kwarg so the dashboard Algo tab can attribute
CNC overnight holdings opened on prior trading days.
Default behavior unchanged (today-only) — preserves the
existing LiveDashboard /v1/algo/live/positions semantics.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: `_get_algo_positions_impl` + route + 6 lift-to-module tests

**Files:**
- Modify: `backend/algo/routes/portfolio.py` — add `_row_from_position`, `_row_from_holding`, `_get_algo_positions_impl`, `create_portfolio_router`
- Modify: `backend/algo/tests/test_portfolio_routes.py` — add 6 impl tests

- [ ] **Step 2.1: Write the failing impl tests**

Append to `backend/algo/tests/test_portfolio_routes.py`:

```python
from backend.algo.routes.portfolio import (
    _get_algo_positions_impl,
)


def _kite_position_row(
    symbol: str, qty: int, avg: float, ltp: float,
) -> dict:
    return {
        "tradingsymbol": symbol,
        "quantity": qty,
        "average_price": avg,
        "last_price": ltp,
        "pnl": qty * (ltp - avg),
        "product": "MIS",
    }


def _kite_holding_row(
    symbol: str, qty: int, t1_qty: int, avg: float, ltp: float,
) -> dict:
    return {
        "tradingsymbol": symbol,
        "quantity": qty,
        "t1_quantity": t1_qty,
        "average_price": avg,
        "last_price": ltp,
        "product": "CNC",
    }


def _attr(
    sid: str, name: str, entry_ts_utc: str | None = None,
) -> dict:
    return {
        "strategy_id": sid,
        "strategy_name": name,
        "entry_ts_utc": entry_ts_utc,
        "entry_reason": None,
    }


def _fake_kite(positions_net, holdings):
    """Build a MagicMock KiteClient with ._kc.positions /
    ._kc.holdings that the impl will call via to_thread."""
    kc_inner = MagicMock()
    kc_inner.positions = MagicMock(
        return_value={"net": positions_net},
    )
    kc_inner.holdings = MagicMock(return_value=holdings)
    outer = MagicMock()
    outer._kc = kc_inner
    return outer


@pytest.mark.asyncio
async def test_returns_empty_when_no_kite_positions(
    monkeypatch,
):
    fake_kite = _fake_kite([], [])
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        AsyncMock(return_value=fake_kite),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_fetch_strategy_attribution",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "is_market_open_ist",
        lambda: False,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    assert out.positions == []


@pytest.mark.asyncio
async def test_filters_out_unattributed_positions(
    monkeypatch,
):
    net = [_kite_position_row("INFY", 10, 1500, 1520)]
    fake_kite = _fake_kite(net, [])
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        AsyncMock(return_value=fake_kite),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_fetch_strategy_attribution",
        AsyncMock(return_value={}),  # no attribution
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "is_market_open_ist",
        lambda: False,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    assert out.positions == []  # dropped — no attribution


@pytest.mark.asyncio
async def test_merges_mis_and_cnc_into_one_response(
    monkeypatch,
):
    net = [_kite_position_row("INFY", 10, 1500, 1520)]
    hold = [_kite_holding_row("TCS", 5, 0, 3400, 3450)]
    fake_kite = _fake_kite(net, hold)
    sid_mis = str(uuid4())
    sid_cnc = str(uuid4())
    attr = {
        "INFY.NS": _attr(sid_mis, "RSI(2) v3"),
        "TCS.NS": _attr(sid_cnc, "Bollinger"),
    }
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        AsyncMock(return_value=fake_kite),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_fetch_strategy_attribution",
        AsyncMock(return_value=attr),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "is_market_open_ist",
        lambda: True,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    syms = {(r.tradingsymbol, r.product) for r in out.positions}
    assert syms == {("INFY", "MIS"), ("TCS", "CNC")}
    assert out.market_open is True


@pytest.mark.asyncio
async def test_t1_pending_flagged_on_cnc_settling(
    monkeypatch,
):
    """holdings row with quantity=0 + t1_quantity=10 →
    t1_pending=True, qty=10."""
    hold = [_kite_holding_row("HDFC", 0, 10, 1700, 1720)]
    fake_kite = _fake_kite([], hold)
    sid = str(uuid4())
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        AsyncMock(return_value=fake_kite),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_fetch_strategy_attribution",
        AsyncMock(
            return_value={
                "HDFC.NS": _attr(sid, "Bollinger"),
            },
        ),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "is_market_open_ist",
        lambda: False,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    assert len(out.positions) == 1
    assert out.positions[0].t1_pending is True
    assert out.positions[0].quantity == 10


@pytest.mark.asyncio
async def test_sorted_by_pnl_inr_desc(monkeypatch):
    net = [
        _kite_position_row("A", 10, 100, 110),  # +100 pnl
        _kite_position_row("B", 10, 100, 90),   # -100 pnl
    ]
    fake_kite = _fake_kite(net, [])
    sid_a = str(uuid4())
    sid_b = str(uuid4())
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        AsyncMock(return_value=fake_kite),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_fetch_strategy_attribution",
        AsyncMock(
            return_value={
                "A.NS": _attr(sid_a, "S1"),
                "B.NS": _attr(sid_b, "S2"),
            },
        ),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "is_market_open_ist",
        lambda: False,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    syms = [r.tradingsymbol for r in out.positions]
    assert syms == ["A", "B"]


@pytest.mark.asyncio
async def test_cache_hit_short_circuits(monkeypatch):
    """A populated Redis cache entry skips the Kite calls."""
    cached = AlgoPositionsResponse(
        positions=[],
        as_of=datetime.now(timezone.utc),
        market_open=False,
    )
    fake_cache = MagicMock()
    fake_cache.get = MagicMock(
        return_value=cached.model_dump_json(),
    )
    fake_cache.set = MagicMock()
    monkeypatch.setattr(
        "backend.algo.routes.portfolio.get_cache",
        lambda: fake_cache,
    )
    # Build a kite mock that would fail if called.
    fake_kite_builder = AsyncMock(
        side_effect=AssertionError("should not be called"),
    )
    monkeypatch.setattr(
        "backend.algo.routes.portfolio."
        "_build_kite_for_user",
        fake_kite_builder,
    )
    out = await _get_algo_positions_impl(
        user_id=uuid4(),
    )
    assert out.positions == []
    fake_kite_builder.assert_not_called()
```

- [ ] **Step 2.2: Run tests — expect 6 NEW failures**

```bash
docker compose exec backend python -m pytest \
  backend/algo/tests/test_portfolio_routes.py -v
```

Expected: 5 pass (Task 1 tests), 6 fail (`_get_algo_positions_impl` doesn't exist).

- [ ] **Step 2.3: Implement `_get_algo_positions_impl` + helpers + router**

Append to `backend/algo/routes/portfolio.py` (after the `_days_held` helper):

```python
def _to_internal_ticker(tradingsymbol: str) -> str:
    """Kite tradingsymbol → internal ticker (e.g. INFY.NS).

    Indian-only mapping for now (matches existing
    backend/algo/live/position_hydration.py behavior).
    """
    if not tradingsymbol:
        return ""
    return f"{tradingsymbol}.NS"


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _safe_decimal(v: Any) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except (TypeError, ValueError):
        return Decimal("0")


def _row_from_position(
    raw: dict[str, Any],
    attr: dict[str, Any],
) -> AlgoPositionRow:
    """Convert a Kite positions().net row to AlgoPositionRow."""
    tsym = raw.get("tradingsymbol") or ""
    qty = _safe_int(raw.get("quantity"))
    avg = _safe_decimal(raw.get("average_price"))
    ltp = _safe_decimal(raw.get("last_price"))
    pnl_inr = Decimal(qty) * (ltp - avg)
    pnl_pct = (
        ((ltp - avg) / avg) * Decimal("100")
        if avg > 0
        else Decimal("0")
    )
    entry_ts_str = attr.get("entry_ts_utc")
    entry_ts = (
        datetime.fromisoformat(entry_ts_str)
        if entry_ts_str
        else None
    )
    return AlgoPositionRow(
        tradingsymbol=tsym,
        internal_ticker=_to_internal_ticker(tsym),
        product="MIS",
        quantity=abs(qty),
        avg_price=avg,
        last_price=ltp,
        pnl_inr=pnl_inr,
        pnl_pct=pnl_pct,
        strategy_id=UUID(attr["strategy_id"]),
        strategy_name=attr.get("strategy_name") or "",
        entry_ts=entry_ts,
        days_held=_days_held(entry_ts),
        t1_pending=False,
    )


def _row_from_holding(
    raw: dict[str, Any],
    attr: dict[str, Any],
) -> AlgoPositionRow:
    """Convert a Kite holdings() row to AlgoPositionRow.

    SEBI T+1: a CNC BUY shows quantity=0 + t1_quantity=N
    during settlement. Both pools are 'held' from the algo
    perspective; we sum them and flag t1_pending when the
    settled pool is empty.
    """
    tsym = raw.get("tradingsymbol") or ""
    settled = _safe_int(raw.get("quantity"))
    t1 = _safe_int(raw.get("t1_quantity"))
    effective = settled + t1
    avg = _safe_decimal(raw.get("average_price"))
    ltp = _safe_decimal(raw.get("last_price"))
    pnl_inr = Decimal(effective) * (ltp - avg)
    pnl_pct = (
        ((ltp - avg) / avg) * Decimal("100")
        if avg > 0
        else Decimal("0")
    )
    entry_ts_str = attr.get("entry_ts_utc")
    entry_ts = (
        datetime.fromisoformat(entry_ts_str)
        if entry_ts_str
        else None
    )
    return AlgoPositionRow(
        tradingsymbol=tsym,
        internal_ticker=_to_internal_ticker(tsym),
        product="CNC",
        quantity=effective,
        avg_price=avg,
        last_price=ltp,
        pnl_inr=pnl_inr,
        pnl_pct=pnl_pct,
        strategy_id=UUID(attr["strategy_id"]),
        strategy_name=attr.get("strategy_name") or "",
        entry_ts=entry_ts,
        days_held=_days_held(entry_ts),
        t1_pending=(settled == 0 and t1 > 0),
    )


async def _get_algo_positions_impl(
    *,
    user_id: UUID,
) -> AlgoPositionsResponse:
    """Pure async impl, testable without HTTP harness."""
    # Lazy imports to break circular deps / keep import-time
    # cost low.
    from backend.cache import get_cache
    from backend.algo.live.budget import _build_kite_for_user
    from backend.algo.live.reconciliation import (
        is_market_open_ist,
    )
    from backend.algo.routes.live import (
        _fetch_strategy_attribution,
    )

    cache = get_cache()
    cache_key = (
        f"cache:algo:portfolio:positions:{user_id}"
    )
    if cache is not None:
        cached_raw = cache.get(cache_key)
        if cached_raw:
            try:
                return (
                    AlgoPositionsResponse
                    .model_validate_json(cached_raw)
                )
            except (ValueError, TypeError):
                # Corrupted cache entry — recompute.
                pass

    try:
        kite = await _build_kite_for_user(user_id)
    except RuntimeError as exc:
        # No Kite creds / expired token → return empty so
        # the dashboard tab renders empty-state.
        _logger.info(
            "algo portfolio: no Kite for user=%s: %s",
            user_id, exc,
        )
        return AlgoPositionsResponse(
            positions=[],
            as_of=datetime.now(timezone.utc),
            market_open=is_market_open_ist(),
        )

    kc = kite._kc
    try:
        raw_pos, raw_hold = await asyncio.gather(
            asyncio.to_thread(kc.positions),
            asyncio.to_thread(kc.holdings),
        )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "algo portfolio: kite read failed",
            exc_info=True,
        )
        raw_pos, raw_hold = {}, []

    net = (
        raw_pos.get("net", [])
        if isinstance(raw_pos, dict)
        else []
    )
    open_pos = [
        r for r in net
        if _safe_int(r.get("quantity")) != 0
    ]
    open_hold = [
        r for r in (raw_hold or [])
        if (
            _safe_int(r.get("quantity"))
            + _safe_int(r.get("t1_quantity")) > 0
        )
    ]

    symbols = sorted({
        _to_internal_ticker(r.get("tradingsymbol", ""))
        for r in open_pos + open_hold
    } - {""})

    attr = await _fetch_strategy_attribution(
        user_id, symbols, since_date=_ATTRIBUTION_SINCE,
    )

    rows: list[AlgoPositionRow] = []
    for r in open_pos:
        sym = _to_internal_ticker(r.get("tradingsymbol", ""))
        ctx = attr.get(sym)
        if not ctx or not ctx.get("strategy_id"):
            continue
        rows.append(_row_from_position(r, ctx))
    for r in open_hold:
        sym = _to_internal_ticker(r.get("tradingsymbol", ""))
        ctx = attr.get(sym)
        if not ctx or not ctx.get("strategy_id"):
            continue
        rows.append(_row_from_holding(r, ctx))

    rows.sort(
        key=lambda r: (-r.pnl_inr, r.tradingsymbol),
    )

    resp = AlgoPositionsResponse(
        positions=rows,
        as_of=datetime.now(timezone.utc),
        market_open=is_market_open_ist(),
    )

    if cache is not None:
        try:
            cache.set(
                cache_key,
                resp.model_dump_json(),
                ttl=_CACHE_TTL_S,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "algo portfolio: cache set failed",
                exc_info=True,
            )

    return resp


def create_portfolio_router() -> APIRouter:
    """Builder so `backend/routes.py` mounts it under /v1."""
    router = APIRouter(
        prefix="/algo/portfolio",
        tags=["algo-trading"],
    )

    @router.get(
        "/positions",
        response_model=AlgoPositionsResponse,
    )
    async def get_positions(
        user: UserContext = Depends(pro_or_superuser),
    ) -> AlgoPositionsResponse:
        return await _get_algo_positions_impl(
            user_id=UUID(user.user_id),
        )

    return router
```

- [ ] **Step 2.4: Run tests — expect 11 PASS**

```bash
docker compose exec backend python -m pytest \
  backend/algo/tests/test_portfolio_routes.py -v
```

Expected: 11 passed (5 from Task 1 + 6 from Task 2).

- [ ] **Step 2.5: Commit**

```bash
git add backend/algo/routes/portfolio.py \
        backend/algo/tests/test_portfolio_routes.py
git commit -m "$(cat <<'EOF'
feat(algo-portfolio): /v1/algo/portfolio/positions endpoint

Pure async _get_algo_positions_impl runs Kite positions() +
holdings() in parallel, joins attribution (year-wide
lookback so CNC overnight holdings stay attributed), drops
unattributed rows, sorts by pnl_inr DESC. 60s Redis cache,
TTL-only invalidation.

Fail-open on missing/expired Kite creds (returns empty list
+ market_open flag, so the FE empty-state renders cleanly
for users who haven't connected Kite yet).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: Mount router + backend smoke

**Files:**
- Modify: `backend/algo/routes/__init__.py`
- Modify: `backend/routes.py`

- [ ] **Step 3.1: Export the router builder**

Edit `backend/algo/routes/__init__.py`. Add the import alphabetically alongside the existing `create_sweep_router` / `create_walkforward_router` imports. Reference pattern at lines 41-50:

```python
from backend.algo.routes.portfolio import (
    create_portfolio_router,
)
```

Add `"create_portfolio_router",` to the `__all__` list in alphabetical order.

- [ ] **Step 3.2: Mount in `backend/routes.py`**

Edit `backend/routes.py`. Around lines 4102-4156 is the algo-routes mount block.

Find the import block:
```python
from backend.algo.routes import (
    create_budget_router,
    create_sweep_router,
    create_walkforward_router,
)
```

Add `create_portfolio_router` to that import (alphabetically):
```python
from backend.algo.routes import (
    create_budget_router,
    create_portfolio_router,
    create_sweep_router,
    create_walkforward_router,
)
```

Find the mount block:
```python
app.include_router(create_walkforward_router(), prefix="/v1")
app.include_router(create_sweep_router(), prefix="/v1")
```

Add after them:
```python
app.include_router(
    create_portfolio_router(),
    prefix="/v1",
)
```

- [ ] **Step 3.3: Restart backend (route registration requires it)**

```bash
docker compose restart backend && sleep 5
```

Per CLAUDE.md §6.2 — new `@router.get(...)` decorators need a backend restart.

- [ ] **Step 3.4: Smoke-test the router shape**

```bash
docker compose exec backend python -c "
from backend.algo.routes.portfolio import create_portfolio_router
r = create_portfolio_router()
for route in r.routes:
    print(route.methods, route.path)
"
```

Expected:
```
{'GET'} /algo/portfolio/positions
```

- [ ] **Step 3.5: Hit the endpoint via curl (requires a valid superuser session cookie)**

Skip this if you don't have a session cookie handy — the pytest suite already exercises the impl. The point of this check is that uvicorn picked up the new route after restart.

```bash
docker compose exec backend python -c "
from backend.routes import app
paths = [r.path for r in app.routes if hasattr(r, 'path')]
print('/v1/algo/portfolio/positions' in paths)
"
```

Expected: `True`.

- [ ] **Step 3.6: Commit**

```bash
git add backend/algo/routes/__init__.py backend/routes.py
git commit -m "$(cat <<'EOF'
feat(algo-portfolio): mount /v1/algo/portfolio router

create_portfolio_router exported from algo.routes package
and mounted under /v1 alongside the budget/sweep/walkforward
routers.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Frontend types + `useAlgoPositions` hook

**Files:**
- Create: `frontend/lib/types/algoPortfolio.ts`
- Create: `frontend/hooks/useAlgoPositions.ts`
- Create: `frontend/components/widgets/algo/__tests__/useAlgoPositions.test.ts`

- [ ] **Step 4.1: Create the TS shapes**

Create `frontend/lib/types/algoPortfolio.ts`:

```typescript
// Mirrors backend/algo/routes/portfolio.py.

export interface AlgoPositionView {
  tradingsymbol: string;
  internal_ticker: string;
  product: "MIS" | "CNC";
  quantity: number;
  avg_price: string;        // Decimal as string
  last_price: string;
  pnl_inr: string;
  pnl_pct: string;
  strategy_id: string;
  strategy_name: string;
  entry_ts: string | null;
  days_held: number;
  t1_pending: boolean;
}

export interface AlgoPositionsResponse {
  positions: AlgoPositionView[];
  as_of: string;
  market_open: boolean;
}
```

- [ ] **Step 4.2: Write the hook smoke test**

Create `frontend/components/widgets/algo/__tests__/useAlgoPositions.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useAlgoPositions } from "@/hooks/useAlgoPositions";

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      positions: [
        {
          tradingsymbol: "INFY",
          internal_ticker: "INFY.NS",
          product: "MIS",
          quantity: 50,
          avg_price: "1500.00",
          last_price: "1572.50",
          pnl_inr: "3625.00",
          pnl_pct: "4.83",
          strategy_id: "00000000-0000-0000-0000-000000000001",
          strategy_name: "RSI(2) v3",
          entry_ts: "2026-05-24T10:00:00Z",
          days_held: 0,
          t1_pending: false,
        },
      ],
      as_of: "2026-05-24T10:30:00Z",
      market_open: true,
    }),
  }),
}));

describe("useAlgoPositions", () => {
  it("returns positions array on success", async () => {
    const { result } = renderHook(() => useAlgoPositions());
    await waitFor(() => {
      expect(result.current.positions.length).toBe(1);
    });
    expect(result.current.positions[0].tradingsymbol).toBe(
      "INFY",
    );
    expect(result.current.marketOpen).toBe(true);
  });
});
```

- [ ] **Step 4.3: Run test — expect failure**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx vitest run \
  components/widgets/algo/__tests__/useAlgoPositions.test.ts
```

Expected: ERROR — `useAlgoPositions` not exported.

- [ ] **Step 4.4: Implement the hook**

Create `frontend/hooks/useAlgoPositions.ts`:

```typescript
"use client";
import useSWR from "swr";
import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type {
  AlgoPositionView,
  AlgoPositionsResponse,
} from "@/lib/types/algoPortfolio";

const fetcher = async (url: string) => {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export function useAlgoPositions() {
  const { data, error, isLoading, mutate } = useSWR<
    AlgoPositionsResponse
  >(
    `${API_URL}/algo/portfolio/positions`,
    fetcher,
    {
      revalidateOnFocus: false,
      // 5s during market hours, 60s off-hours — matches the
      // existing useLivePortfolioTotals cadence.
      refreshInterval: (latest) =>
        latest?.market_open ? 5_000 : 60_000,
    },
  );

  const positions: AlgoPositionView[] =
    data?.positions ?? [];

  return {
    positions,
    asOf: data?.as_of ?? null,
    marketOpen: data?.market_open ?? false,
    isLoading,
    error,
    mutate,
  };
}
```

- [ ] **Step 4.5: Run test — expect PASS**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx vitest run \
  components/widgets/algo/__tests__/useAlgoPositions.test.ts
```

Expected: 1 passed.

- [ ] **Step 4.6: Commit**

```bash
git add frontend/lib/types/algoPortfolio.ts \
        frontend/hooks/useAlgoPositions.ts \
        frontend/components/widgets/algo/__tests__/useAlgoPositions.test.ts
git commit -m "$(cat <<'EOF'
feat(algo-portfolio-ui): types + useAlgoPositions hook

TS mirror of backend shapes; SWR hook polls
/v1/algo/portfolio/positions at 5s during market hours,
60s off-hours (refreshInterval reads market_open from the
response).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: `AlgoPositionsTab` component + vitest tests

**Files:**
- Create: `frontend/components/widgets/algo/AlgoPositionRow.tsx`
- Create: `frontend/components/widgets/algo/AlgoPositionsTab.tsx`
- Create: `frontend/components/widgets/algo/__tests__/AlgoPositionsTab.test.tsx`

- [ ] **Step 5.1: Create the row component**

Create `frontend/components/widgets/algo/AlgoPositionRow.tsx`:

```tsx
"use client";

import type { AlgoPositionView } from "@/lib/types/algoPortfolio";

interface Props {
  row: AlgoPositionView;
  onSelectTicker?: (ticker: string) => void;
}

function inr(s: string): string {
  const n = Number(s);
  if (!Number.isFinite(n)) return "₹0";
  return `₹${n.toLocaleString("en-IN", {
    maximumFractionDigits: 2,
  })}`;
}

function pctStr(s: string): string {
  const n = Number(s);
  if (!Number.isFinite(n)) return "0.00%";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

export function AlgoPositionRow({ row, onSelectTicker }: Props) {
  const pnl = Number(row.pnl_pct);
  const positive = Number.isFinite(pnl) && pnl >= 0;
  return (
    <tr
      onClick={() =>
        onSelectTicker?.(row.internal_ticker)
      }
      className="border-t border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
      data-testid={
        `dashboard-algo-row-${row.tradingsymbol}`
      }
    >
      <td className="px-3 py-2 text-xs font-medium">
        {row.tradingsymbol}
      </td>
      <td className="px-3 py-2 text-xs tabular-nums">
        {row.t1_pending ? (
          <span>
            0+<em className="not-italic text-amber-600">
              {row.quantity}
            </em>{" "}
            <span className="text-amber-600">T+1</span>
          </span>
        ) : (
          row.quantity
        )}
      </td>
      <td className="px-3 py-2 text-xs tabular-nums text-right">
        {inr(row.avg_price)}
      </td>
      <td className="px-3 py-2 text-xs tabular-nums text-right">
        {inr(row.last_price)}
      </td>
      <td
        className={`px-3 py-2 text-xs tabular-nums text-right ${
          positive ? "text-emerald-600" : "text-rose-600"
        }`}
      >
        {pctStr(row.pnl_pct)}
      </td>
      <td
        className="px-3 py-2 text-xs truncate max-w-[14ch]"
        title={row.strategy_name}
      >
        {row.strategy_name}
      </td>
      <td className="px-3 py-2 text-xs text-right text-gray-500">
        {row.days_held}
      </td>
    </tr>
  );
}
```

- [ ] **Step 5.2: Create the tab body**

Create `frontend/components/widgets/algo/AlgoPositionsTab.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useAlgoPositions } from "@/hooks/useAlgoPositions";
import { AlgoPositionRow } from "./AlgoPositionRow";

interface Props {
  onSelectTicker?: (ticker: string) => void;
}

export function AlgoPositionsTab({ onSelectTicker }: Props) {
  const { positions, isLoading, error } = useAlgoPositions();

  if (isLoading) {
    return (
      <div
        className="px-5 py-10 text-center"
        data-testid="dashboard-algo-positions-loading"
      >
        <div className="animate-spin h-6 w-6 border-2 border-indigo-500 border-t-transparent rounded-full mx-auto" />
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="m-5 rounded-md border border-rose-200 bg-rose-50 dark:bg-rose-950/30 p-3 text-xs text-rose-700"
        data-testid="dashboard-algo-positions-error"
      >
        Algo positions unavailable
      </div>
    );
  }

  if (positions.length === 0) {
    return (
      <div
        className="m-5 rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/30 p-4 text-xs space-y-2"
        data-testid="dashboard-algo-positions-empty"
      >
        <p className="font-medium text-amber-900 dark:text-amber-200">
          No algo positions open.
        </p>
        <p className="text-amber-800 dark:text-amber-300">
          Live algo trading places intraday + overnight
          positions that show up here.
        </p>
        <Link
          href="/algo-trading/strategies?tab=live"
          className="inline-block rounded bg-indigo-600 text-white px-3 py-1.5 text-xs"
          data-testid="dashboard-algo-positions-cta"
        >
          Set up a live strategy →
        </Link>
      </div>
    );
  }

  return (
    <div
      className="overflow-x-auto"
      data-testid="dashboard-algo-positions-table"
    >
      <table className="w-full text-xs">
        <thead className="bg-gray-50 dark:bg-gray-800 border-b border-gray-100 dark:border-gray-800">
          <tr>
            <th className="px-3 py-2 text-left font-semibold text-gray-500">
              Symbol
            </th>
            <th className="px-3 py-2 text-left font-semibold text-gray-500">
              Qty
            </th>
            <th className="px-3 py-2 text-right font-semibold text-gray-500">
              Avg
            </th>
            <th className="px-3 py-2 text-right font-semibold text-gray-500">
              LTP
            </th>
            <th className="px-3 py-2 text-right font-semibold text-gray-500">
              PnL %
            </th>
            <th className="px-3 py-2 text-left font-semibold text-gray-500">
              Strategy
            </th>
            <th className="px-3 py-2 text-right font-semibold text-gray-500">
              Days
            </th>
          </tr>
        </thead>
        <tbody>
          {positions.map((row) => (
            <AlgoPositionRow
              key={`${row.tradingsymbol}-${row.product}`}
              row={row}
              onSelectTicker={onSelectTicker}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 5.3: Write the 3 vitest tests**

Create `frontend/components/widgets/algo/__tests__/AlgoPositionsTab.test.tsx`:

```typescript
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { AlgoPositionsTab } from "../AlgoPositionsTab";

const ROW = {
  tradingsymbol: "INFY",
  internal_ticker: "INFY.NS",
  product: "MIS" as const,
  quantity: 50,
  avg_price: "1500.00",
  last_price: "1572.50",
  pnl_inr: "3625.00",
  pnl_pct: "4.83",
  strategy_id: "00000000-0000-0000-0000-000000000001",
  strategy_name: "RSI(2) v3",
  entry_ts: "2026-05-24T10:00:00Z",
  days_held: 0,
  t1_pending: false,
};

vi.mock("@/hooks/useAlgoPositions", () => ({
  useAlgoPositions: vi.fn(),
}));

import { useAlgoPositions } from "@/hooks/useAlgoPositions";

afterEach(() => cleanup());

describe("AlgoPositionsTab", () => {
  it("renders rows when positions present", () => {
    (useAlgoPositions as ReturnType<typeof vi.fn>)
      .mockReturnValue({
        positions: [ROW],
        asOf: "2026-05-24T10:30:00Z",
        marketOpen: true,
        isLoading: false,
        error: null,
        mutate: vi.fn(),
      });
    render(<AlgoPositionsTab />);
    expect(
      screen.getByTestId("dashboard-algo-positions-table"),
    ).toBeDefined();
    expect(
      screen.getByTestId("dashboard-algo-row-INFY"),
    ).toBeDefined();
  });

  it("renders empty state with deep link", () => {
    (useAlgoPositions as ReturnType<typeof vi.fn>)
      .mockReturnValue({
        positions: [],
        asOf: null,
        marketOpen: false,
        isLoading: false,
        error: null,
        mutate: vi.fn(),
      });
    render(<AlgoPositionsTab />);
    const cta = screen.getByTestId(
      "dashboard-algo-positions-cta",
    );
    expect(cta).toBeDefined();
    expect(cta.getAttribute("href")).toBe(
      "/algo-trading/strategies?tab=live",
    );
  });

  it("row click calls onSelectTicker with internal_ticker", () => {
    (useAlgoPositions as ReturnType<typeof vi.fn>)
      .mockReturnValue({
        positions: [ROW],
        asOf: "2026-05-24T10:30:00Z",
        marketOpen: true,
        isLoading: false,
        error: null,
        mutate: vi.fn(),
      });
    const onSelectTicker = vi.fn();
    render(
      <AlgoPositionsTab onSelectTicker={onSelectTicker} />,
    );
    fireEvent.click(
      screen.getByTestId("dashboard-algo-row-INFY"),
    );
    expect(onSelectTicker).toHaveBeenCalledWith("INFY.NS");
  });
});
```

- [ ] **Step 5.4: Run vitest — expect 3 PASS**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx vitest run \
  components/widgets/algo/__tests__/AlgoPositionsTab.test.tsx
```

Expected: 3 passed.

- [ ] **Step 5.5: Lint**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx eslint \
  components/widgets/algo/AlgoPositionsTab.tsx \
  components/widgets/algo/AlgoPositionRow.tsx \
  components/widgets/algo/__tests__/AlgoPositionsTab.test.tsx \
  --fix
```

Expected: 0 errors.

- [ ] **Step 5.6: Commit**

```bash
git add frontend/components/widgets/algo/AlgoPositionRow.tsx \
        frontend/components/widgets/algo/AlgoPositionsTab.tsx \
        frontend/components/widgets/algo/__tests__/AlgoPositionsTab.test.tsx
git commit -m "$(cat <<'EOF'
feat(algo-portfolio-ui): AlgoPositionsTab + AlgoPositionRow

Compact table with 7 columns (Symbol | Qty | Avg | LTP |
PnL% | Strategy | Days). Empty state amber card + deep
link to /algo-trading/strategies?tab=live. Row click calls
onSelectTicker(internal_ticker). T+1 pending qty styled
italic with amber accent.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: Wire third tab into `WatchlistWidget` + `DashboardClient`

**Files:**
- Modify: `frontend/components/widgets/WatchlistWidget.tsx`
- Modify: `frontend/app/(authenticated)/dashboard/DashboardClient.tsx`
- Create: `frontend/components/widgets/__tests__/WatchlistWidget.algo-tab.test.tsx`

- [ ] **Step 6.1: Write the failing tab-visibility test**

Create `frontend/components/widgets/__tests__/WatchlistWidget.algo-tab.test.tsx`:

```typescript
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { WatchlistWidget } from "../WatchlistWidget";

vi.mock("@/hooks/useAlgoPositions", () => ({
  useAlgoPositions: () => ({
    positions: [],
    asOf: null,
    marketOpen: false,
    isLoading: false,
    error: null,
    mutate: vi.fn(),
  }),
}));

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn(),
}));

const BASE_PROPS = {
  data: {
    loading: false,
    error: null,
    value: { tickers: [] },
  },
  selectedTicker: null,
  onSelectTicker: vi.fn(),
  onRefresh: vi.fn(),
  portfolio: [],
  portfolioLoading: false,
} as const;

afterEach(() => cleanup());

describe("WatchlistWidget — algo tab gating", () => {
  it("hides the Algo tab when algoTabEnabled=false", () => {
    render(
      <WatchlistWidget
        {...BASE_PROPS}
        algoTabEnabled={false}
      />,
    );
    expect(
      screen.queryByTestId("dashboard-watchlist-tab-algo"),
    ).toBeNull();
  });

  it("shows the Algo tab when algoTabEnabled=true", () => {
    render(
      <WatchlistWidget
        {...BASE_PROPS}
        algoTabEnabled={true}
      />,
    );
    expect(
      screen.getByTestId("dashboard-watchlist-tab-algo"),
    ).toBeDefined();
  });
});
```

- [ ] **Step 6.2: Run test — expect failure**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx vitest run \
  components/widgets/__tests__/WatchlistWidget.algo-tab.test.tsx
```

Expected: FAIL — `algoTabEnabled` is not a prop on `WatchlistWidget` yet, and `dashboard-watchlist-tab-algo` testid doesn't exist.

- [ ] **Step 6.3: Patch `WatchlistWidget.tsx`**

Edit `frontend/components/widgets/WatchlistWidget.tsx`. Four changes:

(a) Extend the WidgetTab type at line ~77:

Replace
```typescript
type WidgetTab = "portfolio" | "watchlist";
```
with
```typescript
type WidgetTab = "portfolio" | "watchlist" | "algo";
```

(b) Add `algoTabEnabled` to the props interface. Find `WatchlistWidgetProps` (search for `portfolioLoading?` to land near the props block) and add:

```typescript
algoTabEnabled?: boolean;
```

(c) Destructure it in the component signature alongside `portfolioLoading = false`:

```typescript
algoTabEnabled = false,
```

(d) Add the third tab button + body. Find the tab strip at line ~232 (right after the existing Portfolio + Watchlist buttons inside the `inline-flex rounded-lg` div). Add a new button after the Watchlist button — only when `algoTabEnabled`:

```tsx
{algoTabEnabled && (
  <button
    data-testid="dashboard-watchlist-tab-algo"
    onClick={() => setActiveTab("algo")}
    className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
      activeTab === "algo"
        ? "bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm"
        : "text-gray-500 dark:text-gray-400"
    }`}
  >
    Algo
  </button>
)}
```

Then find the tab-body conditionals. The Portfolio body starts at `{activeTab === "portfolio" && (` around line ~293. The Watchlist body starts later. Add a third body branch AFTER the watchlist body but inside the same outer `<div data-testid="dashboard-watchlist-table">` block:

```tsx
{activeTab === "algo" && (
  <AlgoPositionsTab onSelectTicker={onSelectTicker} />
)}
```

Add the import near the other algo-related imports at the top of the file:

```typescript
import { AlgoPositionsTab } from "./algo/AlgoPositionsTab";
```

Also, update the right-side count chip. Find the `<span>` near line ~285 that renders `${portfolio.length} stock…` / `${tickers.length} ticker…`. Add an algo branch (the count comes from a small helper we render — easiest is to leave the chip's count placeholder when `activeTab === "algo"`):

```tsx
<span className="text-xs text-gray-400 dark:text-gray-500">
  {activeTab === "portfolio"
    ? `${portfolio.length} stock${portfolio.length !== 1 ? "s" : ""}`
    : activeTab === "watchlist"
    ? `${tickers.length} ticker${tickers.length !== 1 ? "s" : ""}`
    : "live"}
</span>
```

(The literal `"live"` is fine for v1 — the row count is visible in the table itself; we don't need a chip count.)

- [ ] **Step 6.4: Run test — expect PASS**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx vitest run \
  components/widgets/__tests__/WatchlistWidget.algo-tab.test.tsx \
  components/widgets/algo/__tests__/AlgoPositionsTab.test.tsx \
  components/widgets/algo/__tests__/useAlgoPositions.test.ts
```

Expected: 6 passed (2 new + 3 existing AlgoPositionsTab + 1 useAlgoPositions).

- [ ] **Step 6.5: Wire `algoTabEnabled` in `DashboardClient.tsx`**

Edit `frontend/app/(authenticated)/dashboard/DashboardClient.tsx`. Locate the `<WatchlistWidget` JSX render (search for `<WatchlistWidget`). Add the `algoTabEnabled` prop derived from `profile?.role`:

```tsx
<WatchlistWidget
  data={...}                  // existing
  selectedTicker={...}        // existing
  onSelectTicker={...}        // existing
  onRefresh={...}             // existing
  portfolio={...}             // existing
  portfolioLoading={...}      // existing
  onAddStock={...}            // existing (if present)
  algoTabEnabled={
    profile?.role === "pro"
    || profile?.role === "superuser"
  }
/>
```

The `profile` variable already exists at line ~126 (`const profile = profileData.value;`).

- [ ] **Step 6.6: Lint + restart dev server (if running)**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/frontend && \
  npx eslint \
  components/widgets/WatchlistWidget.tsx \
  app/\(authenticated\)/dashboard/DashboardClient.tsx \
  --fix
```

Expected: 0 errors.

- [ ] **Step 6.7: Manual browser smoke (optional but recommended)**

Open `http://localhost:3000/dashboard` as a superuser. Verify:
- Three tabs visible: Portfolio · Watchlist · Algo.
- Clicking Algo with no positions shows the amber empty-state with the deep link.
- Logging out and back in as a general user shows only Portfolio · Watchlist.

- [ ] **Step 6.8: Commit**

```bash
git add frontend/components/widgets/WatchlistWidget.tsx \
        frontend/app/\(authenticated\)/dashboard/DashboardClient.tsx \
        frontend/components/widgets/__tests__/WatchlistWidget.algo-tab.test.tsx
git commit -m "$(cat <<'EOF'
feat(algo-portfolio-ui): wire Algo tab into WatchlistWidget

Third tab "Algo" inside the dashboard's WatchlistWidget,
conditionally rendered when algoTabEnabled=true.
DashboardClient computes the flag from useProfile().role —
pro/superuser see the tab; general users do not.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: E2E + PROGRESS + PR

**Files:**
- Modify: `e2e/utils/selectors.ts`
- Create: `e2e/tests/frontend/dashboard-algo-tab.spec.ts`
- Modify: `PROGRESS.md`

- [ ] **Step 7.1: Add testids to the FE registry**

Edit `e2e/utils/selectors.ts`. Inside the `FE = { ... }` object, add:

```typescript
dashboardWatchlistTabAlgo: "dashboard-watchlist-tab-algo",
dashboardAlgoPositionsTable: "dashboard-algo-positions-table",
dashboardAlgoPositionsEmpty: "dashboard-algo-positions-empty",
dashboardAlgoPositionsCta: "dashboard-algo-positions-cta",
```

- [ ] **Step 7.2: Write the E2E smoke spec**

Create `e2e/tests/frontend/dashboard-algo-tab.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";
import { FE } from "../../utils/selectors";

test.use({ storageState: ".auth/superuser.json" });

test.describe("Dashboard Algo tab", () => {
  test("renders and accepts a click", async ({ page }) => {
    await page.goto("/dashboard");

    // Algo tab button is present for superuser.
    const tab = page.getByTestId(FE.dashboardWatchlistTabAlgo);
    await expect(tab).toBeVisible();
    await tab.click();

    // Either the positions table OR the empty-state CTA
    // renders — we don't assume the test env has algo
    // positions. The test passes as long as one of the
    // two is visible (i.e. the route + cookie + gate work).
    const table = page.getByTestId(
      FE.dashboardAlgoPositionsTable,
    );
    const empty = page.getByTestId(
      FE.dashboardAlgoPositionsEmpty,
    );
    await expect(table.or(empty)).toBeVisible();
  });
});
```

- [ ] **Step 7.3: Run the spec**

```bash
cd /Users/abhay/Documents/projects/ai-agent-ui/e2e && \
  npx playwright test dashboard-algo-tab.spec.ts \
  --project=frontend-chromium
```

Expected: 1 passed.

If the spec fails because the dev server isn't up, start it (`./run.sh start frontend`) and retry. If it fails because `.auth/superuser.json` is stale, regenerate per `e2e/auth.setup.ts` and retry.

- [ ] **Step 7.4: Add PROGRESS.md entry**

Edit `PROGRESS.md`. Insert at the top (above the existing 2026-05-24 entries):

```markdown
### 2026-05-24 — Algo Portfolio dashboard tab (Epic B)

New "Algo" tab in the dashboard's WatchlistWidget showing
currently-open algo-attributed positions (intraday MIS +
overnight CNC) with Symbol / Qty / Avg / LTP / PnL% /
Strategy / Days-held columns. Pro/superuser gated;
general users don't see the tab.

Backend: GET /v1/algo/portfolio/positions runs Kite
positions() + holdings() in parallel, joins
_fetch_strategy_attribution (extended with a since_date
kwarg so CNC overnight positions opened on prior days stay
attributed), drops bare Kite rows, sorts by pnl_inr DESC.
60 s Redis cache, TTL-only invalidation, fail-open on
missing/expired Kite creds.

Frontend: useAlgoPositions SWR hook polls 5 s / 60 s per
market_open. Empty-state amber card with deep link to
/algo-trading/strategies?tab=live.

Out of scope (v1): closed positions, slide-over detail,
group-by-strategy subtotals, multi-broker, multi-currency,
sortable headers, CSV download, pagination, per-row close
buttons.

Spec: `docs/superpowers/specs/2026-05-24-algo-portfolio-tab-design.md`
Plan: `docs/superpowers/plans/2026-05-24-algo-portfolio-tab.md`
```

- [ ] **Step 7.5: Commit + push + open PR**

```bash
git add e2e/utils/selectors.ts \
        e2e/tests/frontend/dashboard-algo-tab.spec.ts \
        PROGRESS.md
git commit -m "$(cat <<'EOF'
feat(algo-portfolio): E2E smoke + PROGRESS entry

Playwright smoke verifies the Algo tab button is visible
for a superuser and clicking it renders either the
positions table or the empty-state CTA (the env-dependent
branch is tolerated). PROGRESS entry summarises Epic B.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"

git push -u origin feature/algo-portfolio-tab

gh pr create \
  --base dev \
  --title "Algo portfolio dashboard tab (Epic B, v1)" \
  --body "$(cat <<'EOF'
## Summary

- New `GET /v1/algo/portfolio/positions` endpoint merges Kite intraday MIS + overnight CNC, joins `algo.events` attribution (year-wide lookback), drops bare Kite rows, sorts by `pnl_inr DESC`
- Third tab "Algo" in the dashboard's `WatchlistWidget` — Symbol / Qty / Avg / LTP / PnL% / Strategy / Days-held
- Pro / superuser gated; general users don't see the tab
- 60 s Redis cache, fail-open on missing Kite creds
- Empty-state amber card with deep link to `/algo-trading/strategies?tab=live`
- Reuses Epic A's `_build_kite_for_user` helper

Spec: `docs/superpowers/specs/2026-05-24-algo-portfolio-tab-design.md`
Plan: `docs/superpowers/plans/2026-05-24-algo-portfolio-tab.md`

## Test plan

- [x] Backend: 11 tests in `test_portfolio_routes.py` — all green
- [x] Frontend: 6 Vitest tests (`useAlgoPositions`, `AlgoPositionsTab`, `WatchlistWidget.algo-tab`) — all green
- [ ] E2E: `cd e2e && npx playwright test dashboard-algo-tab.spec.ts --project=frontend-chromium`
- [ ] Manual: superuser sees Algo tab; general user doesn't

## Out of scope (deferred)

- Closed positions / today's exited trades
- Slide-over with strategy AST detail
- Group-by-strategy subtotals
- Multi-broker
- Multi-currency
- Sortable column headers
- CSV download
- Pagination

## Companion epic

- **C**: Watchlist bulk ops + universe binding

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review

**1. Spec coverage**

Going section-by-section through `docs/superpowers/specs/2026-05-24-algo-portfolio-tab-design.md`:

| Spec section | Implemented by |
|---|---|
| §3 — currently-open only, intraday MIS + CNC | Task 2 `open_pos` / `open_hold` filtering by quantity / t1_quantity |
| §3 — algo-attributed only | Task 2 `if not ctx: continue` drop |
| §3 — third tab inside WatchlistWidget | Task 6 third tab button + body branch |
| §3 — columns Symbol / Qty / Avg / LTP / PnL% / Strategy / Days | Task 5 `AlgoPositionsTab` table + `AlgoPositionRow` cells |
| §3 — new combined endpoint | Task 2 `_get_algo_positions_impl` + Task 3 mount |
| §3 — 5s / 60s refresh | Task 4 `refreshInterval` conditional on `market_open` |
| §3 — row click selects ticker | Task 5 `AlgoPositionRow.onClick → onSelectTicker(internal_ticker)` |
| §3 — empty state deep link | Task 5 `AlgoPositionsTab` empty branch |
| §3 — pro/superuser gating | Task 6 `algoTabEnabled` prop + `DashboardClient` derivation |
| §6 — Pydantic models + days_held | Task 1 + Task 2 |
| §6 — `entry_ts_utc` from attribution → `entry_ts` datetime on the row | Task 2 `_row_from_position` / `_row_from_holding` parse with `datetime.fromisoformat` |
| §7 — UI surface (loading, populated, empty, error) | Task 5 all 4 branches |
| §8 — Redis 60 s TTL, TTL-only invalidation | Task 2 `_CACHE_TTL_S = 60`, `cache.set(... ttl=...)` after compute |
| §8 — SWR `refreshInterval` 5s/60s | Task 4 |
| §9 — 6 backend tests | Task 1 (5) + Task 2 (6) = 11 total |
| §9 — 3 FE tests | Task 5 (3) + Task 6 (2 tab-visibility) + Task 4 (1 hook smoke) = 6 total |
| §9 — 1 E2E smoke | Task 7 |
| §11 — read-only, attribution authoritative, `as_of` = server time, no new env vars | Task 2 (no write paths in `_get_algo_positions_impl`, attribution drop logic, `as_of=datetime.now(utc)`, no env vars added) |

All spec requirements have implementation steps.

**2. Placeholder scan**

No "TBD" / "TODO" / "fill in details" anywhere. Every step has actual content. Step 3.5 is the only step that mentions "skip if you don't have a session cookie" — but the alternative path (the Python expression check) is fully written out.

**3. Type consistency**

- Backend: `AlgoPositionRow` defined in Task 1, consumed in Task 2 (`_row_from_position` returns it). `_fetch_strategy_attribution` signature with `*, since_date` defined in Task 1, called in Task 2 with `since_date=_ATTRIBUTION_SINCE`. ✓
- FE: `AlgoPositionView` / `AlgoPositionsResponse` defined in Task 4, consumed by `useAlgoPositions` (Task 4), `AlgoPositionsTab` (Task 5), `AlgoPositionRow` (Task 5). All references resolve. ✓
- The `onSelectTicker?: (ticker: string) => void` callback signature is identical across `AlgoPositionsTab`, `AlgoPositionRow`, and the existing `WatchlistWidget` ✓.
- `algoTabEnabled` prop defined in Task 6 step 6.3 (b/c), consumed in Task 6 step 6.3 (d). ✓.
- Test IDs used in E2E (`dashboard-watchlist-tab-algo`, `dashboard-algo-positions-table`, `dashboard-algo-positions-empty`, `dashboard-algo-positions-cta`) all defined in the components and registered in Task 7.1. ✓.

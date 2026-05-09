# Algo Trading — Session 4: Backtest Engine (Slice 7a, headless)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Slice 7a from the Algo Trading epic spec — a headless backend backtest engine that takes a stored strategy AST + a date range, walks daily OHLCV bars, evaluates the AST per bar against the user's universe, simulates fills via SimBroker (with `IndianFeeModel`), and persists run metadata + an event log + report metrics. Slice 7b (the Backtest tab UI with equity curve + trade table) becomes Session 5.

**Architecture:** Pure-async Python pipeline reading `stocks.ohlcv` via DuckDB, AST evaluation through a small `Evaluator` class (one method per node family, mirrors the validator), `SimBroker` performing fee-aware fills against the next-bar open (no look-ahead), position tracker accumulating realised+unrealised P&L, run summary written to `algo.runs` (Postgres) and per-event rows appended to `algo.events` (Iceberg, partitioned by `mode + date`). MinIO is reserved for Slice 7b artifact uploads.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic 2 / asyncpg / DuckDB / PyIceberg 0.11.1 / pytest. Reuses Session 1's `IndianFeeModel`, Session 2's `Strategy` AST + `parse_strategy`, Session 1's `algo.events` Iceberg table + `algo.runs` Postgres table.

**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md` (§ 9 Slice 7).

**Branch:** `feature/algo-trading-session-4-backtest-engine` (already cut off Session 3's tip `4e02ff4`).

**Conventions reminders:**
- Branch off `dev`; squash-only merge; Co-Authored-By Abhay; line length 79; `X | None`; `_logger`; backend restart after route/model changes.
- Reuse `IndianFeeModel`, `Strategy`/`parse_strategy`, `BrokerCredentialsRepo`, `_get_session_factory()` patterns.
- Per epic spec § 13: backtest must avoid look-ahead bias — fills happen at bar T+1 open, never T close.
- Per epic spec § 6.2: every `order_filled` event payload stamps `fee_rates_version` so re-runs after rate changes don't silently drift.
- Per epic spec § 9.2: this slice is pre-split as 7a (engine + tests + headless run endpoint, this session) + 7b (UI report tab, next session).

---

## File Structure

### Backend (new)

- `backend/algo/backtest/__init__.py` — package marker.
- `backend/algo/backtest/types.py` — `BacktestRequest`, `BacktestSummary`, `BarData`, `OrderIntent`, `Fill`, `Position` Pydantic models.
- `backend/algo/backtest/data_source.py` — `load_ohlcv_window(tickers, period_start, period_end)` over DuckDB; ban-future-dates guard.
- `backend/algo/backtest/sim_broker.py` — `SimBroker` fee-aware fills at next-bar open; emits `order_submitted` + `order_filled` events.
- `backend/algo/backtest/evaluator.py` — `Evaluator.eval_node(node, ctx)` dispatcher per AST node `type`; pure functions, no side effects.
- `backend/algo/backtest/positions.py` — `PositionTracker.apply_fill(fill)` mutates internal map + computes realised/unrealised P&L.
- `backend/algo/backtest/runner.py` — `run_backtest(strategy, request)` orchestrator: fetches bars, walks T0..Tn, gates AST through evaluator, dispatches order intents to SimBroker, snapshots PositionTracker daily, persists run summary + event log.
- `backend/algo/backtest/event_writer.py` — append-only writer for `algo.events` (Iceberg). Single batch commit at end of run for performance.
- `backend/algo/routes/backtest.py` — `POST /v1/algo/backtest/run` (kicks off async job; returns `run_id`); `GET /v1/algo/backtest/runs/{run_id}` (status + summary).

### Backend (modified)

- `backend/algo/routes/__init__.py` — re-export `create_backtest_router`.
- `backend/routes.py` — register the router.

### Tests (new)

- `backend/algo/tests/test_backtest_evaluator.py` — AST evaluation per node type.
- `backend/algo/tests/test_backtest_sim_broker.py` — fill mechanics + fee integration.
- `backend/algo/tests/test_backtest_positions.py` — long/exit, FIFO realised P&L.
- `backend/algo/tests/test_backtest_runner.py` — end-to-end on a 30-bar synthetic ticker.
- `backend/algo/tests/test_backtest_lookahead.py` — pinned-failure tests confirming the engine NEVER reads future bars.
- `backend/algo/tests/test_backtest_routes.py` — `POST /run` + `GET /runs/{id}` smokes.

---

## Task 1: Backtest types module

**Files:**
- Create: `backend/algo/backtest/__init__.py`
- Create: `backend/algo/backtest/types.py`

- [ ] **Step 1: Package marker**

```python
# backend/algo/backtest/__init__.py
"""Backtest engine — Slice 7a of the Algo Trading epic."""
```

- [ ] **Step 2: Implement types**

```python
# backend/algo/backtest/types.py
"""Pydantic models shared across the backtest engine.

Single source of truth for the wire shape between the runner,
evaluator, sim-broker, position tracker, and the run-summary
endpoint. Keep these stable — every downstream module imports
the types declared here.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class BacktestRequest(BaseModel):
    """Request body for POST /v1/algo/backtest/run."""
    model_config = ConfigDict(extra="forbid")

    strategy_id: UUID
    period_start: date
    period_end: date
    initial_capital_inr: Decimal = Field(
        default=Decimal("100000.00"), ge=Decimal("1000.00"),
    )


class BarData(BaseModel):
    """One day of OHLCV for one ticker."""
    model_config = ConfigDict(extra="forbid")

    ticker: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class OrderIntent(BaseModel):
    """Strategy → SimBroker handoff. Emitted by the evaluator."""
    model_config = ConfigDict(extra="forbid")

    intent_id: UUID = Field(default_factory=uuid4)
    ticker: str
    side: Literal["BUY", "SELL"]
    qty: int = Field(ge=1)
    intent_emitted_at: date  # bar T; fills at T+1 open


class Fill(BaseModel):
    """One executed fill emitted by SimBroker."""
    model_config = ConfigDict(extra="forbid")

    intent_id: UUID
    ticker: str
    side: Literal["BUY", "SELL"]
    qty: int
    fill_price: Decimal
    fill_date: date         # T+1
    fees_inr: Decimal       # IndianFeeModel.compute total
    fee_rates_version: str  # stamps the dated YAML row used


class Position(BaseModel):
    """Open or closed position from PositionTracker."""
    model_config = ConfigDict(extra="forbid")

    ticker: str
    qty: int
    avg_price: Decimal
    opened_at: date
    closed_at: date | None = None
    realised_pnl_inr: Decimal = Field(default=Decimal("0.00"))


class BacktestSummary(BaseModel):
    """Run-level metrics persisted to algo.runs and returned by
    GET /v1/algo/backtest/runs/{id}."""
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    strategy_id: UUID
    period_start: date
    period_end: date
    initial_capital_inr: Decimal
    final_equity_inr: Decimal
    total_pnl_inr: Decimal
    total_pnl_pct: Decimal
    total_fees_inr: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    max_drawdown_pct: Decimal
    started_at: datetime
    completed_at: datetime
    fee_rates_version: str
```

- [ ] **Step 3: Smoke + commit**

```bash
docker compose exec backend python -c "
from backend.algo.backtest.types import (
    BacktestRequest, BarData, OrderIntent, Fill, Position,
    BacktestSummary,
)
print('ok')
" 2>&1 | tail -3

git add backend/algo/backtest/__init__.py backend/algo/backtest/types.py
git commit -m "$(cat <<'EOF'
feat(algo): backtest types module

Slice 7a of the Algo Trading epic. BacktestRequest, BarData,
OrderIntent, Fill, Position, BacktestSummary — single source of
truth shared across runner / evaluator / sim-broker / position
tracker. All Pydantic 2 with extra="forbid".

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: OHLCV data source over DuckDB (with look-ahead guard)

**Files:**
- Create: `backend/algo/backtest/data_source.py`
- Create: `backend/algo/tests/test_backtest_lookahead.py`

- [ ] **Step 1: Failing look-ahead-guard test**

```python
# backend/algo/tests/test_backtest_lookahead.py
"""Pinned-failure tests confirming the data-source layer cannot
return rows past the requested ``period_end`` even if the caller
sets a clamping date in the future. These guards block the
single most common backtest bug — peeking at tomorrow's close.
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.algo.backtest.data_source import (
    BackedFutureBarError,
    load_ohlcv_window,
)


def test_load_rejects_period_end_in_future():
    with pytest.raises(BackedFutureBarError):
        load_ohlcv_window(
            tickers=["TCS.NS"],
            period_start=date(2024, 1, 1),
            period_end=date(9999, 1, 1),
        )


def test_load_rejects_period_start_after_period_end():
    with pytest.raises(ValueError, match="period_start"):
        load_ohlcv_window(
            tickers=["TCS.NS"],
            period_start=date(2024, 6, 1),
            period_end=date(2024, 1, 1),
        )


def test_load_empty_tickers_returns_empty_dict():
    out = load_ohlcv_window(
        tickers=[],
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 31),
    )
    assert out == {}
```

- [ ] **Step 2: Run — expect ImportError**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_lookahead.py -v 2>&1 | tail -8
```

- [ ] **Step 3: Implement the data source**

```python
# backend/algo/backtest/data_source.py
"""Load daily OHLCV bars from ``stocks.ohlcv`` (Iceberg) into
in-memory dicts keyed by ticker → list[BarData].

Single bulk DuckDB query (CLAUDE.md §4.1 #1); ``period_end``
clamped to today UTC to enforce no-look-ahead at the data-source
layer (the evaluator further enforces T+1-fill semantics).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from backend.algo.backtest.types import BarData
from stocks.repository import _get_duckdb_connection

_logger = logging.getLogger(__name__)


class BackedFutureBarError(ValueError):
    """Raised when the caller asks for a period ending in the future."""


def load_ohlcv_window(
    *,
    tickers: list[str],
    period_start: date,
    period_end: date,
) -> dict[str, list[BarData]]:
    """Bulk-load OHLCV for *tickers* over the closed interval.

    Returns ``{ticker: [BarData, ...]}`` sorted by date ascending.
    Tickers with no rows in the period are absent from the dict.

    Raises:
        BackedFutureBarError: if ``period_end`` is past today UTC.
        ValueError: if ``period_start`` > ``period_end`` or
                    *tickers* contains an obviously invalid name.
    """
    today = datetime.now(timezone.utc).date()
    if period_end > today:
        raise BackedFutureBarError(
            f"period_end {period_end.isoformat()} is past today "
            f"{today.isoformat()} — backtest can't peek at the future."
        )
    if period_start > period_end:
        raise ValueError(
            f"period_start {period_start.isoformat()} is after "
            f"period_end {period_end.isoformat()}."
        )
    if not tickers:
        return {}

    placeholders = ",".join(f"'{t}'" for t in tickers)
    sql = (
        "SELECT ticker, date, open, high, low, close, volume "
        "FROM stocks.ohlcv "
        f"WHERE ticker IN ({placeholders}) "
        "  AND date BETWEEN ? AND ? "
        "ORDER BY ticker, date"
    )

    con = _get_duckdb_connection()
    rows = con.execute(sql, [period_start, period_end]).fetchall()

    grouped: dict[str, list[BarData]] = {}
    for r in rows:
        ticker = str(r[0])
        bar = BarData(
            ticker=ticker,
            date=r[1] if isinstance(r[1], date) else date.fromisoformat(str(r[1])),
            open=Decimal(str(r[2])),
            high=Decimal(str(r[3])),
            low=Decimal(str(r[4])),
            close=Decimal(str(r[5])),
            volume=int(r[6] or 0),
        )
        grouped.setdefault(ticker, []).append(bar)
    return grouped
```

- [ ] **Step 4: Tests pass**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_lookahead.py -v 2>&1 | tail -6
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/backtest/data_source.py backend/algo/tests/test_backtest_lookahead.py
git commit -m "$(cat <<'EOF'
feat(algo): backtest data source with look-ahead guard

Slice 7a of the Algo Trading epic. load_ohlcv_window() bulk-reads
stocks.ohlcv over a closed period; rejects future period_end with
BackedFutureBarError; rejects inverted ranges with ValueError;
empty ticker list short-circuits to {}.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: SimBroker — fee-aware T+1 fills

**Files:**
- Create: `backend/algo/backtest/sim_broker.py`
- Create: `backend/algo/tests/test_backtest_sim_broker.py`

- [ ] **Step 1: Failing tests**

```python
# backend/algo/tests/test_backtest_sim_broker.py
"""SimBroker fill mechanics + IndianFeeModel integration."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.algo.backtest.sim_broker import (
    NoBarAvailableError,
    SimBroker,
)
from backend.algo.backtest.types import BarData, OrderIntent


def _bar(ticker: str, day: date, openp: float, close: float) -> BarData:
    return BarData(
        ticker=ticker,
        date=day,
        open=Decimal(str(openp)),
        high=Decimal(str(close + 5)),
        low=Decimal(str(openp - 5)),
        close=Decimal(str(close)),
        volume=100_000,
    )


@pytest.fixture
def bars() -> dict[str, list[BarData]]:
    return {
        "RELIANCE.NS": [
            _bar("RELIANCE.NS", date(2024, 1, 1), 2900, 2920),
            _bar("RELIANCE.NS", date(2024, 1, 2), 2925, 2935),
            _bar("RELIANCE.NS", date(2024, 1, 3), 2940, 2950),
        ],
    }


def test_buy_fills_at_next_bar_open(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2024, 1, 1))
    intent = OrderIntent(
        ticker="RELIANCE.NS", side="BUY", qty=10,
        intent_emitted_at=date(2024, 1, 1),
    )
    fill = sb.execute(intent)
    assert fill is not None
    assert fill.fill_date == date(2024, 1, 2)
    assert fill.fill_price == Decimal("2925")
    assert fill.qty == 10


def test_sell_fills_at_next_bar_open(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2024, 1, 1))
    intent = OrderIntent(
        ticker="RELIANCE.NS", side="SELL", qty=5,
        intent_emitted_at=date(2024, 1, 2),
    )
    fill = sb.execute(intent)
    assert fill.fill_date == date(2024, 1, 3)
    assert fill.fill_price == Decimal("2940")


def test_no_next_bar_returns_none(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2024, 1, 1))
    # Last bar in fixture is Jan 3 — no T+1 available.
    intent = OrderIntent(
        ticker="RELIANCE.NS", side="BUY", qty=10,
        intent_emitted_at=date(2024, 1, 3),
    )
    assert sb.execute(intent) is None


def test_unknown_ticker_raises(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2024, 1, 1))
    with pytest.raises(NoBarAvailableError):
        sb.execute(OrderIntent(
            ticker="UNKNOWN.NS", side="BUY", qty=1,
            intent_emitted_at=date(2024, 1, 1),
        ))


def test_fee_is_non_zero_for_delivery_buy(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2024, 1, 1))
    fill = sb.execute(OrderIntent(
        ticker="RELIANCE.NS", side="BUY", qty=100,
        intent_emitted_at=date(2024, 1, 1),
    ))
    assert fill.fees_inr > Decimal("0")
    assert fill.fee_rates_version  # non-empty stamp


def test_intent_emitted_at_after_period_returns_none(bars):
    sb = SimBroker(bars=bars, fee_as_of=date(2024, 1, 1))
    intent = OrderIntent(
        ticker="RELIANCE.NS", side="BUY", qty=10,
        intent_emitted_at=date(2024, 1, 10),  # past last bar
    )
    assert sb.execute(intent) is None
```

- [ ] **Step 2: Run — expect ImportError**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_sim_broker.py -v 2>&1 | tail -8
```

- [ ] **Step 3: Implement SimBroker**

```python
# backend/algo/backtest/sim_broker.py
"""Fee-aware backtest broker. Fills BUY/SELL intents at the
NEXT bar's open price (T+1), never at the same bar's close —
this is the single most important look-ahead guard in the
engine.

Per epic spec § 6: every fill stamps the IndianFeeModel
``fee_rates_version`` so re-runs after a YAML rate change
don't silently drift.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from backend.algo.backtest.types import BarData, Fill, OrderIntent
from backend.algo.fees import IndianFeeModel, Trade

_logger = logging.getLogger(__name__)


class NoBarAvailableError(KeyError):
    """The intent's ticker has no bars in the loaded window."""


class SimBroker:
    """Stateless executor of OrderIntents against a pre-loaded
    bar dict. Construct once per backtest run.
    """

    def __init__(
        self,
        *,
        bars: dict[str, list[BarData]],
        fee_as_of: date,
    ) -> None:
        self._bars = bars
        self._fees = IndianFeeModel(as_of=fee_as_of)
        # Pre-compute date -> index lookup per ticker for O(1)
        # T+1 resolution.
        self._index: dict[str, dict[date, int]] = {
            t: {b.date: i for i, b in enumerate(blist)}
            for t, blist in bars.items()
        }

    def execute(self, intent: OrderIntent) -> Fill | None:
        """Return a Fill at T+1 open, or None if no next bar exists.

        Raises NoBarAvailableError if the ticker isn't in the
        loaded window (a real-world ingestion gap; runner should
        log + skip).
        """
        if intent.ticker not in self._bars:
            raise NoBarAvailableError(intent.ticker)

        idx = self._index[intent.ticker].get(intent.intent_emitted_at)
        if idx is None:
            # Intent emitted on a non-trading day for this ticker —
            # walk forward to the first bar at-or-after.
            future = [
                i for d, i in self._index[intent.ticker].items()
                if d > intent.intent_emitted_at
            ]
            if not future:
                return None
            next_idx = min(future)
        else:
            next_idx = idx + 1
            if next_idx >= len(self._bars[intent.ticker]):
                return None

        next_bar = self._bars[intent.ticker][next_idx]
        # Compute fees on the executed leg.
        product = "DELIVERY"  # v1 only
        exchange = "NSE"      # v1 only
        breakdown = self._fees.compute(
            Trade(
                symbol=intent.ticker,
                exchange=exchange,
                side=intent.side,
                product=product,
                qty=intent.qty,
                price=next_bar.open,
            ),
        )
        return Fill(
            intent_id=intent.intent_id,
            ticker=intent.ticker,
            side=intent.side,
            qty=intent.qty,
            fill_price=next_bar.open,
            fill_date=next_bar.date,
            fees_inr=breakdown.total_inr,
            fee_rates_version=breakdown.rates_version,
        )
```

- [ ] **Step 4: Tests pass**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_sim_broker.py -v 2>&1 | tail -10
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/backtest/sim_broker.py backend/algo/tests/test_backtest_sim_broker.py
git commit -m "$(cat <<'EOF'
feat(algo): SimBroker — fee-aware T+1 fills

Slice 7a of the Algo Trading epic. Stateless executor that fills
OrderIntents at the NEXT bar's open (never same-bar close — the
core look-ahead guard). Stamps IndianFeeModel rates_version on
every Fill so backtests re-run identically after YAML rate
changes. 6 unit tests cover buy/sell, no-next-bar, unknown
ticker, fee non-zero, intent past last bar.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Position tracker

**Files:**
- Create: `backend/algo/backtest/positions.py`
- Create: `backend/algo/tests/test_backtest_positions.py`

- [ ] **Step 1: Failing tests**

```python
# backend/algo/tests/test_backtest_positions.py
"""PositionTracker — long/exit, FIFO realised P&L."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.types import Fill


def _fill(ticker: str, side: str, qty: int, price: float, day: date) -> Fill:
    return Fill(
        intent_id=uuid4(),
        ticker=ticker,
        side=side,
        qty=qty,
        fill_price=Decimal(str(price)),
        fill_date=day,
        fees_inr=Decimal("0"),
        fee_rates_version="2026-04-01",
    )


def test_buy_opens_long_position():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    pos = pt.open_positions()["X"]
    assert pos.qty == 10
    assert pos.avg_price == Decimal("100")


def test_sell_closes_long_realises_pnl():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    pt.apply_fill(_fill("X", "SELL", 10, 110, date(2024, 1, 3)))
    assert pt.open_positions().get("X") is None
    closed = pt.closed_positions()
    assert len(closed) == 1
    # Realised PnL = (110 - 100) * 10 = 100
    assert closed[0].realised_pnl_inr == Decimal("100")


def test_partial_sell_keeps_remainder_open():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    pt.apply_fill(_fill("X", "SELL", 4, 105, date(2024, 1, 3)))
    pos = pt.open_positions()["X"]
    assert pos.qty == 6
    assert pos.avg_price == Decimal("100")
    closed = pt.closed_positions()
    # Realised PnL = (105 - 100) * 4 = 20
    assert closed[0].realised_pnl_inr == Decimal("20")


def test_average_price_blends_two_buys():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    pt.apply_fill(_fill("X", "BUY", 10, 120, date(2024, 1, 3)))
    pos = pt.open_positions()["X"]
    assert pos.qty == 20
    assert pos.avg_price == Decimal("110")


def test_short_side_not_supported_v1():
    pt = PositionTracker()
    # Selling without a long is a no-op in v1 (long-only).
    pt.apply_fill(_fill("X", "SELL", 5, 100, date(2024, 1, 2)))
    assert pt.open_positions() == {}


def test_total_realised_pnl_aggregates():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    pt.apply_fill(_fill("X", "SELL", 10, 110, date(2024, 1, 3)))
    pt.apply_fill(_fill("Y", "BUY", 5, 200, date(2024, 1, 2)))
    pt.apply_fill(_fill("Y", "SELL", 5, 190, date(2024, 1, 3)))
    # X: +100 ; Y: -50  →  total +50
    assert pt.total_realised_pnl_inr() == Decimal("50")


def test_unrealised_pnl_uses_mark_price():
    pt = PositionTracker()
    pt.apply_fill(_fill("X", "BUY", 10, 100, date(2024, 1, 2)))
    # Mark at 110 → unrealised = (110 - 100) * 10 = 100
    pnl = pt.unrealised_pnl_inr({"X": Decimal("110")})
    assert pnl == Decimal("100")
```

- [ ] **Step 2: Run — expect ImportError, then implement**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_positions.py -v 2>&1 | tail -8
```

```python
# backend/algo/backtest/positions.py
"""PositionTracker — long-only v1 with simple weighted-avg cost
basis. Realised P&L computed on every closing leg; unrealised
P&L computed on demand against a mark-price dict (typically
the most recent bar close at snapshot time).

v2 will add short positions, partial-fill grouping, and
options-style margin accounting.
"""
from __future__ import annotations

from decimal import Decimal

from backend.algo.backtest.types import Fill, Position


class PositionTracker:
    def __init__(self) -> None:
        self._open: dict[str, Position] = {}
        self._closed: list[Position] = []
        self._realised_total: Decimal = Decimal("0")

    def apply_fill(self, fill: Fill) -> None:
        if fill.side == "BUY":
            self._apply_buy(fill)
        else:
            self._apply_sell(fill)

    def _apply_buy(self, fill: Fill) -> None:
        existing = self._open.get(fill.ticker)
        if existing is None:
            self._open[fill.ticker] = Position(
                ticker=fill.ticker,
                qty=fill.qty,
                avg_price=fill.fill_price,
                opened_at=fill.fill_date,
            )
            return
        # Weighted average cost basis.
        total_qty = existing.qty + fill.qty
        new_avg = (
            (existing.avg_price * existing.qty)
            + (fill.fill_price * fill.qty)
        ) / total_qty
        self._open[fill.ticker] = existing.model_copy(update={
            "qty": total_qty,
            "avg_price": new_avg,
        })

    def _apply_sell(self, fill: Fill) -> None:
        existing = self._open.get(fill.ticker)
        if existing is None or existing.qty <= 0:
            # v1 long-only — bare sells are no-ops.
            return
        sell_qty = min(fill.qty, existing.qty)
        realised = (fill.fill_price - existing.avg_price) * sell_qty
        self._realised_total += realised
        if sell_qty == existing.qty:
            closed = existing.model_copy(update={
                "closed_at": fill.fill_date,
                "realised_pnl_inr": realised,
            })
            self._closed.append(closed)
            del self._open[fill.ticker]
        else:
            # Partial close: retain remainder open, archive the
            # closed slice as its own row.
            self._closed.append(Position(
                ticker=existing.ticker,
                qty=sell_qty,
                avg_price=existing.avg_price,
                opened_at=existing.opened_at,
                closed_at=fill.fill_date,
                realised_pnl_inr=realised,
            ))
            self._open[fill.ticker] = existing.model_copy(update={
                "qty": existing.qty - sell_qty,
            })

    def open_positions(self) -> dict[str, Position]:
        return dict(self._open)

    def closed_positions(self) -> list[Position]:
        return list(self._closed)

    def total_realised_pnl_inr(self) -> Decimal:
        return self._realised_total

    def unrealised_pnl_inr(
        self, marks: dict[str, Decimal],
    ) -> Decimal:
        total = Decimal("0")
        for ticker, pos in self._open.items():
            mark = marks.get(ticker)
            if mark is None:
                continue
            total += (mark - pos.avg_price) * pos.qty
        return total
```

- [ ] **Step 3: Tests pass + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_positions.py -v 2>&1 | tail -10

git add backend/algo/backtest/positions.py backend/algo/tests/test_backtest_positions.py
git commit -m "$(cat <<'EOF'
feat(algo): position tracker — long-only v1 + FIFO realised PnL

Slice 7a of the Algo Trading epic. PositionTracker.apply_fill
mutates open positions (weighted-avg cost basis on buys, partial
or full closes on sells). v1 long-only — bare sells without a
prior long are no-ops. unrealised_pnl_inr(marks) computes
mark-to-market for snapshot reporting. 7 unit tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: AST Evaluator (per-bar dispatch)

**Files:**
- Create: `backend/algo/backtest/evaluator.py`
- Create: `backend/algo/tests/test_backtest_evaluator.py`

- [ ] **Step 1: Failing tests**

```python
# backend/algo/tests/test_backtest_evaluator.py
"""AST evaluator dispatch tests. Inputs are minimal — the
evaluator only cares about the per-bar context; it's the
runner's job to assemble that context from data_source +
PositionTracker.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.algo.backtest.evaluator import (
    EvalContext,
    Evaluator,
)


@pytest.fixture
def ctx() -> EvalContext:
    return EvalContext(
        ticker="RELIANCE.NS",
        bar_date=date(2024, 1, 5),
        features={
            "today_ltp": Decimal("2945.20"),
            "sma_50": Decimal("2900.00"),
            "sma_200": Decimal("2800.00"),
            "rsi": Decimal("65"),
            "pscore": Decimal("8"),
        },
        open_qty=0,
    )


def test_compare_feature_to_literal_true(ctx):
    e = Evaluator()
    assert e.eval_node(
        {
            "type": "compare",
            "left": {"feature": "rsi"},
            "op": "<",
            "right": {"literal": 70},
        },
        ctx,
    ) is True


def test_compare_feature_to_literal_false(ctx):
    e = Evaluator()
    assert e.eval_node(
        {
            "type": "compare",
            "left": {"feature": "rsi"},
            "op": ">",
            "right": {"literal": 70},
        },
        ctx,
    ) is False


def test_and_short_circuits(ctx):
    e = Evaluator()
    assert e.eval_node(
        {
            "type": "and",
            "operands": [
                {
                    "type": "compare",
                    "left": {"feature": "today_ltp"},
                    "op": ">",
                    "right": {"feature": "sma_50"},
                },
                {
                    "type": "compare",
                    "left": {"feature": "pscore"},
                    "op": ">=",
                    "right": {"literal": 7},
                },
            ],
        },
        ctx,
    ) is True


def test_or_short_circuits(ctx):
    e = Evaluator()
    assert e.eval_node(
        {
            "type": "or",
            "operands": [
                {
                    "type": "compare",
                    "left": {"feature": "rsi"},
                    "op": "<",
                    "right": {"literal": 30},
                },
                {
                    "type": "compare",
                    "left": {"feature": "rsi"},
                    "op": "<",
                    "right": {"literal": 70},
                },
            ],
        },
        ctx,
    ) is True


def test_not_inverts(ctx):
    e = Evaluator()
    assert e.eval_node(
        {
            "type": "not",
            "operand": {
                "type": "compare",
                "left": {"feature": "rsi"},
                "op": ">",
                "right": {"literal": 70},
            },
        },
        ctx,
    ) is True


def test_if_then_returns_action_when_cond_true(ctx):
    e = Evaluator()
    out = e.eval_node(
        {
            "type": "if",
            "cond": {
                "type": "compare",
                "left": {"feature": "today_ltp"},
                "op": ">",
                "right": {"feature": "sma_50"},
            },
            "then": {"type": "set_target_weight", "weight": 0.20},
            "else": {"type": "hold"},
        },
        ctx,
    )
    assert out == {"type": "set_target_weight", "weight": 0.20}


def test_if_else_path(ctx):
    e = Evaluator()
    out = e.eval_node(
        {
            "type": "if",
            "cond": {
                "type": "compare",
                "left": {"feature": "rsi"},
                "op": ">",
                "right": {"literal": 90},
            },
            "then": {"type": "set_target_weight", "weight": 0.20},
            "else": {"type": "hold"},
        },
        ctx,
    )
    assert out == {"type": "hold"}


def test_unknown_feature_raises(ctx):
    e = Evaluator()
    with pytest.raises(KeyError, match="not_a_feature"):
        e.eval_node(
            {
                "type": "compare",
                "left": {"feature": "not_a_feature"},
                "op": "<",
                "right": {"literal": 100},
            },
            ctx,
        )


def test_hold_returns_self(ctx):
    e = Evaluator()
    out = e.eval_node({"type": "hold"}, ctx)
    assert out == {"type": "hold"}


def test_buy_returns_self(ctx):
    e = Evaluator()
    out = e.eval_node({"type": "buy", "qty": {"shares": 5}}, ctx)
    assert out == {"type": "buy", "qty": {"shares": 5}}
```

- [ ] **Step 2: Run — expect ImportError**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_evaluator.py -v 2>&1 | tail -8
```

- [ ] **Step 3: Implement evaluator**

```python
# backend/algo/backtest/evaluator.py
"""AST evaluator — per-bar dispatch.

Pure functions. Inputs: a node dict + an EvalContext (current
ticker + bar date + feature map + open position qty). Outputs:
a primitive (bool for conditions; an action dict for actions /
composites that resolve to actions).

The runner calls ``evaluator.eval_node(strategy_root, ctx)``
once per (ticker, bar) and translates returned action dicts
into OrderIntents.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class EvalContext:
    ticker: str
    bar_date: date
    features: dict[str, Decimal]
    open_qty: int  # current PositionTracker qty for this ticker


_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
}


def _resolve_operand(op: dict, ctx: EvalContext) -> Decimal:
    if "feature" in op:
        feature = op["feature"]
        if feature not in ctx.features:
            raise KeyError(f"Feature not in context: {feature}")
        return ctx.features[feature]
    if "literal" in op:
        return Decimal(str(op["literal"]))
    raise ValueError(f"Operand has neither feature nor literal: {op}")


class Evaluator:
    """Stateless dispatcher. Construct once per backtest run."""

    def eval_node(self, node: dict, ctx: EvalContext):  # noqa: ANN201
        t = node.get("type")
        if t == "compare":
            left = _resolve_operand(node["left"], ctx)
            right = _resolve_operand(node["right"], ctx)
            return _OPS[node["op"]](left, right)
        if t == "and":
            return all(
                bool(self.eval_node(c, ctx))
                for c in node["operands"]
            )
        if t == "or":
            return any(
                bool(self.eval_node(c, ctx))
                for c in node["operands"]
            )
        if t == "not":
            return not bool(self.eval_node(node["operand"], ctx))
        if t == "if":
            cond = bool(self.eval_node(node["cond"], ctx))
            branch = node["then"] if cond else node.get("else", {"type": "hold"})
            return self.eval_node(branch, ctx)
        # Action nodes pass through verbatim — runner translates
        # to OrderIntents.
        if t in {
            "buy", "sell", "exit", "hold", "set_target_weight",
        }:
            return dict(node)
        # crossover / between / select_top_n / weighted are v2.
        # In v1 the evaluator returns "hold" so the runner
        # gracefully no-ops.
        return {"type": "hold"}
```

- [ ] **Step 4: Tests pass + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_evaluator.py -v 2>&1 | tail -12

git add backend/algo/backtest/evaluator.py backend/algo/tests/test_backtest_evaluator.py
git commit -m "$(cat <<'EOF'
feat(algo): AST evaluator — per-bar dispatch

Slice 7a of the Algo Trading epic. Evaluator.eval_node dispatches
by AST node type: condition nodes return bool, action nodes
pass through verbatim, composite if-then-else recurses. Unknown
feature in context raises KeyError. Crossover / between /
select_top_n / weighted resolve to "hold" in v1 so the runner
no-ops gracefully — full support lands in 7b/v2. 10 unit tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: Backtest runner — end-to-end orchestration

**Files:**
- Create: `backend/algo/backtest/event_writer.py`
- Create: `backend/algo/backtest/runner.py`
- Create: `backend/algo/tests/test_backtest_runner.py`

- [ ] **Step 1: Event writer (single-batch Iceberg append)**

```python
# backend/algo/backtest/event_writer.py
"""Append-only writer for ``algo.events`` — used by the backtest
runner to flush all events at the end of a run (single Iceberg
commit instead of per-event writes per CLAUDE.md §4.1 #2).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa

from stocks.repository import StockRepository

_logger = logging.getLogger(__name__)


def event_row(
    *,
    session_id: UUID,
    user_id: UUID,
    strategy_id: UUID | None,
    mode: str,
    type_: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a single algo.events row dict ready for bulk append."""
    now = datetime.now(timezone.utc)
    ts_ns = int(now.timestamp() * 1_000_000_000)
    return {
        "event_id": str(uuid4()),
        "ts_ns": ts_ns,
        "ts_date": now.date().isoformat(),
        "session_id": str(session_id),
        "user_id": str(user_id),
        "strategy_id": str(strategy_id) if strategy_id else None,
        "mode": mode,
        "type": type_,
        "payload_json": json.dumps(payload, default=str),
        "written_at": now,
    }


def flush_events(rows: list[dict[str, Any]]) -> None:
    """Single Iceberg commit. No-op on empty list."""
    if not rows:
        return
    repo = StockRepository()
    arrow = pa.Table.from_pylist(rows)
    repo._retry_commit(  # noqa: SLF001 — internal-but-stable
        "algo.events", "append", arrow,
    )
    _logger.info("flushed %d algo.events rows", len(rows))
```

- [ ] **Step 2: Runner**

```python
# backend/algo/backtest/runner.py
"""Backtest orchestrator. Walks daily bars over a closed period,
evaluates the strategy AST per (ticker, bar), routes action
results to SimBroker, accumulates positions, and emits an event
log + summary.

Per CLAUDE.md §4.1: single bulk OHLCV read, single Iceberg
commit at the end (not per-event), no per-ticker hot loops.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from backend.algo.backtest.data_source import load_ohlcv_window
from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.sim_broker import (
    NoBarAvailableError,
    SimBroker,
)
from backend.algo.backtest.types import (
    BacktestRequest,
    BacktestSummary,
    OrderIntent,
)
from backend.algo.strategy.ast import Strategy

_logger = logging.getLogger(__name__)


def _features_for_bar(bar) -> dict[str, Decimal]:  # noqa: ANN001
    """Minimal v1 feature map — runner currently only exposes
    OHLCV-derived leaves. Slice 7b extends with technical /
    fundamental joins."""
    return {
        "today_ltp": bar.close,
        "today_vol": Decimal(bar.volume),
    }


def run_backtest(
    *,
    strategy: Strategy,
    request: BacktestRequest,
    user_id: UUID,
    universe: list[str],
) -> BacktestSummary:
    """Run a backtest end-to-end and return the summary.

    Caller responsibilities:
    - Persist the Strategy AST and generate ``request``.
    - Resolve ``universe`` from ``strategy.universe`` (Slice 7
      uses the user's watchlist union holdings; this function
      treats it as opaque input).
    - Persist the returned ``BacktestSummary`` to ``algo.runs``
      and the events emitted by ``flush_events`` to
      ``algo.events`` — both happen automatically at the end of
      this call.
    """
    started_at = datetime.now(timezone.utc)
    run_id = uuid4()
    session_id = run_id
    events: list[dict[str, Any]] = []

    events.append(event_row(
        session_id=session_id,
        user_id=user_id,
        strategy_id=strategy.id,
        mode="backtest",
        type_="backtest_run_started",
        payload={
            "period_start": request.period_start.isoformat(),
            "period_end": request.period_end.isoformat(),
            "universe_size": len(universe),
            "initial_capital_inr": str(request.initial_capital_inr),
        },
    ))

    bars = load_ohlcv_window(
        tickers=universe,
        period_start=request.period_start,
        period_end=request.period_end,
    )
    sim = SimBroker(bars=bars, fee_as_of=request.period_start)
    evaluator = Evaluator()
    pt = PositionTracker()

    fee_rates_version = ""
    total_fees = Decimal("0")
    equity_curve: list[Decimal] = [request.initial_capital_inr]
    peak_equity = request.initial_capital_inr
    max_drawdown_pct = Decimal("0")

    # Walk bars chronologically. We zip each ticker's series so
    # bar dates that are common across the universe step in lockstep.
    all_dates = sorted({
        b.date for blist in bars.values() for b in blist
    })

    for bar_date in all_dates:
        for ticker in universe:
            blist = bars.get(ticker)
            if not blist:
                continue
            current = next(
                (b for b in blist if b.date == bar_date), None,
            )
            if current is None:
                continue
            ctx = EvalContext(
                ticker=ticker,
                bar_date=bar_date,
                features=_features_for_bar(current),
                open_qty=(
                    pt.open_positions().get(ticker).qty
                    if ticker in pt.open_positions() else 0
                ),
            )
            action = evaluator.eval_node(
                strategy.root.model_dump(by_alias=True),
                ctx,
            )

            intent = _action_to_intent(
                action, ticker=ticker, bar_date=bar_date,
                pt=pt,
            )
            if intent is None:
                continue
            try:
                fill = sim.execute(intent)
            except NoBarAvailableError:
                continue
            if fill is None:
                continue

            pt.apply_fill(fill)
            total_fees += fill.fees_inr
            fee_rates_version = fill.fee_rates_version

            events.append(event_row(
                session_id=session_id,
                user_id=user_id,
                strategy_id=strategy.id,
                mode="backtest",
                type_="order_filled",
                payload={
                    "ticker": fill.ticker,
                    "side": fill.side,
                    "qty": fill.qty,
                    "fill_price": str(fill.fill_price),
                    "fill_date": fill.fill_date.isoformat(),
                    "fees_inr": str(fill.fees_inr),
                    "fee_rates_version": fill.fee_rates_version,
                },
            ))

        # End-of-day equity snapshot.
        marks = {
            t: blist[-1].close
            for t, blist in bars.items()
            if blist and blist[-1].date <= bar_date
        }
        equity = (
            request.initial_capital_inr
            + pt.total_realised_pnl_inr()
            + pt.unrealised_pnl_inr(marks)
            - total_fees
        )
        equity_curve.append(equity)
        if equity > peak_equity:
            peak_equity = equity
        if peak_equity > 0:
            dd = (peak_equity - equity) / peak_equity * Decimal("100")
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd

    final_equity = equity_curve[-1]
    total_pnl = final_equity - request.initial_capital_inr
    total_pnl_pct = (
        (total_pnl / request.initial_capital_inr) * Decimal("100")
        if request.initial_capital_inr > 0 else Decimal("0")
    )
    closed = pt.closed_positions()
    winning = sum(1 for p in closed if p.realised_pnl_inr > 0)
    losing = sum(1 for p in closed if p.realised_pnl_inr <= 0)
    win_rate = (
        Decimal(winning) / Decimal(len(closed)) * Decimal("100")
        if closed else Decimal("0")
    )

    summary = BacktestSummary(
        run_id=run_id,
        strategy_id=strategy.id,
        period_start=request.period_start,
        period_end=request.period_end,
        initial_capital_inr=request.initial_capital_inr,
        final_equity_inr=final_equity,
        total_pnl_inr=total_pnl,
        total_pnl_pct=total_pnl_pct,
        total_fees_inr=total_fees,
        total_trades=len(closed),
        winning_trades=winning,
        losing_trades=losing,
        win_rate_pct=win_rate,
        max_drawdown_pct=max_drawdown_pct,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        fee_rates_version=fee_rates_version or "n/a",
    )

    events.append(event_row(
        session_id=session_id,
        user_id=user_id,
        strategy_id=strategy.id,
        mode="backtest",
        type_="backtest_run_completed",
        payload=summary.model_dump(mode="json"),
    ))
    flush_events(events)
    return summary


def _action_to_intent(
    action: dict,
    *,
    ticker: str,
    bar_date,  # noqa: ANN001
    pt: PositionTracker,
) -> OrderIntent | None:
    """Translate an evaluator action dict to an OrderIntent (or None)."""
    t = action.get("type")
    if t == "buy":
        qty = action["qty"].get("shares") or 0
        if qty <= 0:
            return None
        return OrderIntent(
            ticker=ticker, side="BUY", qty=int(qty),
            intent_emitted_at=bar_date,
        )
    if t == "sell":
        qty_spec = action["qty"]
        if qty_spec.get("all"):
            existing = pt.open_positions().get(ticker)
            if not existing:
                return None
            return OrderIntent(
                ticker=ticker, side="SELL", qty=existing.qty,
                intent_emitted_at=bar_date,
            )
        qty = qty_spec.get("shares") or 0
        if qty <= 0:
            return None
        return OrderIntent(
            ticker=ticker, side="SELL", qty=int(qty),
            intent_emitted_at=bar_date,
        )
    if t == "exit":
        existing = pt.open_positions().get(ticker)
        if not existing:
            return None
        return OrderIntent(
            ticker=ticker, side="SELL", qty=existing.qty,
            intent_emitted_at=bar_date,
        )
    # set_target_weight + hold = no-op in 7a (weight resolution
    # lands in 7b alongside the universe sizer).
    return None
```

- [ ] **Step 3: End-to-end test**

```python
# backend/algo/tests/test_backtest_runner.py
"""End-to-end backtest on a 30-bar synthetic ticker.

Covers the full pipeline: data_source ↔ evaluator ↔ sim_broker
↔ position_tracker ↔ event_writer. Mocks the OHLCV fetch and
the Iceberg flush so the test runs in-process without DuckDB
or PyIceberg roundtrips.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import BacktestRequest, BarData
from backend.algo.strategy.ast import parse_strategy


def _gen_bars(ticker: str) -> list[BarData]:
    base = date(2024, 1, 1)
    bars: list[BarData] = []
    for i in range(30):
        d = base + timedelta(days=i)
        # Trending up: open == prev close + 1.
        openp = Decimal("100") + Decimal(i)
        close = openp + Decimal("2")
        bars.append(BarData(
            ticker=ticker, date=d,
            open=openp, high=close + 1, low=openp - 1,
            close=close, volume=10_000,
        ))
    return bars


def _strategy_payload() -> dict:
    return {
        "id": str(uuid4()),
        "name": "Buy on day 1, hold, sell on day 25",
        "universe": {
            "type": "scope", "scope": "watchlist",
            "filter": {"ticker_type": ["stock"], "market": "india"},
        },
        "schedule": {
            "type": "bar_close", "interval": "1d", "time": "15:25 IST",
        },
        "rebalance": {"type": "daily", "max_positions": 1},
        # Always buy 5 shares — runner has no entry guards in v1
        # so the position keeps stacking on each bar; that's fine
        # for this end-to-end smoke (we only assert "ran without
        # errors and produced a summary").
        "root": {"type": "buy", "qty": {"shares": 5}},
        "risk": {
            "per_trade": {"stop_loss_pct": 5, "max_qty": 100},
            "portfolio": {
                "max_exposure_pct": 80,
                "max_concentration_pct": 25,
            },
            "daily": {"max_loss_pct": 2, "max_open_positions": 10},
        },
    }


@pytest.fixture
def patches():
    bars = {"FAKE.NS": _gen_bars("FAKE.NS")}
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value=bars,
    ), patch(
        "backend.algo.backtest.runner.flush_events",
    ) as flush_mock:
        yield flush_mock


def test_runner_produces_summary(patches):
    strategy = parse_strategy(_strategy_payload())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 30),
        initial_capital_inr=Decimal("100000.00"),
    )
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    assert summary.run_id is not None
    assert summary.total_trades >= 0
    assert summary.fee_rates_version  # stamped at least once on a fill
    # Every bar buys 5 shares; with 30 bars and BUY-only strategy,
    # accumulated qty > 0.
    assert summary.total_fees_inr > Decimal("0")


def test_runner_flushes_events(patches):
    strategy = parse_strategy(_strategy_payload())
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 30),
    )
    run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    # flush_events called exactly once at end
    patches.assert_called_once()
    rows = patches.call_args.args[0]
    assert any(r["type"] == "backtest_run_started" for r in rows)
    assert any(r["type"] == "backtest_run_completed" for r in rows)


def test_runner_handles_empty_universe(patches):
    # Override the patch to return empty bars for all tickers.
    with patch(
        "backend.algo.backtest.runner.load_ohlcv_window",
        return_value={},
    ), patch(
        "backend.algo.backtest.runner.flush_events",
    ):
        strategy = parse_strategy(_strategy_payload())
        request = BacktestRequest(
            strategy_id=strategy.id,
            period_start=date(2024, 1, 1),
            period_end=date(2024, 1, 30),
        )
        summary = run_backtest(
            strategy=strategy,
            request=request,
            user_id=uuid4(),
            universe=[],
        )
        assert summary.total_trades == 0
        assert summary.total_pnl_inr == Decimal("0")


def test_runner_strategy_with_hold_root_zero_trades(patches):
    payload = _strategy_payload()
    payload["root"] = {"type": "hold"}
    strategy = parse_strategy(payload)
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 30),
    )
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=uuid4(),
        universe=["FAKE.NS"],
    )
    assert summary.total_trades == 0
    assert summary.total_fees_inr == Decimal("0")
```

- [ ] **Step 4: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_runner.py -v 2>&1 | tail -10

git add backend/algo/backtest/event_writer.py backend/algo/backtest/runner.py backend/algo/tests/test_backtest_runner.py
git commit -m "$(cat <<'EOF'
feat(algo): backtest runner — end-to-end orchestration

Slice 7a of the Algo Trading epic. run_backtest() walks daily
bars, evaluates the AST per (ticker, bar), routes action results
to SimBroker, accumulates positions + equity curve + drawdown,
flushes a single algo.events Iceberg batch at end. 4 end-to-end
tests cover happy path, event-flush, empty universe, hold-root.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Backtest routes (POST /run, GET /runs/{id})

**Files:**
- Create: `backend/algo/routes/backtest.py`
- Create: `backend/algo/tests/test_backtest_routes.py`
- Modify: `backend/algo/routes/__init__.py` (re-export)
- Modify: `backend/routes.py` (register router)

- [ ] **Step 1: Implement route**

```python
# backend/algo/routes/backtest.py
"""POST /v1/algo/backtest/run — synchronous v1.

v1 runs the backtest inline (small data, ~1-2s for 30 bars on
~10 tickers) and returns the summary directly. Slice 7b adds an
async-job wrapper that returns ``run_id`` immediately and lets
the UI poll ``GET /runs/{id}``.

GET /v1/algo/backtest/runs/{run_id} returns the persisted
summary from algo.runs. v1 stores summaries in-memory keyed
on run_id (a tiny module-level dict) — Slice 7b promotes to
algo.runs persistence + MinIO artifact upload.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.backtest.runner import run_backtest
from backend.algo.backtest.types import (
    BacktestRequest,
    BacktestSummary,
)
from backend.algo.strategy.ast import parse_strategy
from backend.algo.strategy.repo import get_strategy

_logger = logging.getLogger(__name__)

# v1 in-memory store keyed on run_id. Slice 7b moves this to
# algo.runs PG row + MinIO artifact bundle.
_RUNS: dict[UUID, BacktestSummary] = {}


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def create_backtest_router() -> APIRouter:
    router = APIRouter(prefix="/algo/backtest", tags=["algo-trading"])

    @router.post("/run", response_model=BacktestSummary)
    async def run_endpoint(
        body: BacktestRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> BacktestSummary:
        factory = _get_session_factory()
        async with factory() as session:
            strategy = await get_strategy(
                session, UUID(user.user_id), body.strategy_id,
            )
        if strategy is None:
            raise HTTPException(
                status_code=404, detail="Strategy not found",
            )

        # v1 universe = the strategy's stored universe.scope as
        # an opaque list. Slice 7b resolves to the user's actual
        # watchlist ∪ holdings via the existing _scoped_tickers
        # helper from insights_routes.
        universe: list[str] = []  # Resolved by caller in v2

        try:
            summary = run_backtest(
                strategy=strategy,
                request=body,
                user_id=UUID(user.user_id),
                universe=universe,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            _logger.exception("backtest run failed: %s", exc)
            raise HTTPException(
                status_code=500, detail="Backtest run failed",
            )

        _RUNS[summary.run_id] = summary
        return summary

    @router.get(
        "/runs/{run_id}", response_model=BacktestSummary,
    )
    async def get_run(
        run_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ) -> BacktestSummary:
        summary = _RUNS.get(run_id)
        if summary is None:
            raise HTTPException(
                status_code=404, detail="Run not found",
            )
        return summary

    return router
```

- [ ] **Step 2: Re-export**

In `backend/algo/routes/__init__.py`, add to the imports + `__all__`:

```python
from backend.algo.routes.backtest import create_backtest_router
# ...
__all__ = [
    "create_backtest_router",
    "create_broker_router",
    "create_fees_router",
    "create_instruments_router",
    "create_strategies_router",
]
```

In `backend/routes.py`, add `app.include_router(create_backtest_router(), prefix="/v1")` alongside the other algo includes.

- [ ] **Step 3: Tests**

```python
# backend/algo/tests/test_backtest_routes.py
"""Endpoint smokes for /v1/algo/backtest/{run,runs/{id}}."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.backtest.types import BacktestSummary
from backend.algo.routes.backtest import create_backtest_router


def _make_summary(strategy_id, run_id=None) -> BacktestSummary:
    return BacktestSummary(
        run_id=run_id or uuid4(),
        strategy_id=strategy_id,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 30),
        initial_capital_inr=Decimal("100000.00"),
        final_equity_inr=Decimal("105000.00"),
        total_pnl_inr=Decimal("5000.00"),
        total_pnl_pct=Decimal("5.00"),
        total_fees_inr=Decimal("100.00"),
        total_trades=2,
        winning_trades=1,
        losing_trades=1,
        win_rate_pct=Decimal("50.00"),
        max_drawdown_pct=Decimal("3.20"),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        fee_rates_version="2026-04-01",
    )


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(create_backtest_router(), prefix="/v1")
    app.dependency_overrides[pro_or_superuser] = lambda: UserContext(
        user_id="00000000-0000-0000-0000-000000000001",
        email="t@t",
        role="superuser",
    )

    class _Stub:
        async def execute(self, *a, **kw):
            class _Res:
                def mappings(self):
                    return self
                def first(self):
                    return None
            return _Res()
        async def commit(self):
            return None

    class _Factory:
        def __call__(self):
            return self
        async def __aenter__(self):
            return _Stub()
        async def __aexit__(self, *args):
            return None

    import backend.algo.routes.backtest as bt
    monkeypatch.setattr(bt, "_get_session_factory", lambda: _Factory())
    return app


def test_run_returns_404_when_strategy_missing(app):
    client = TestClient(app)
    r = client.post(
        "/v1/algo/backtest/run",
        json={
            "strategy_id": str(uuid4()),
            "period_start": "2024-01-01",
            "period_end": "2024-01-30",
            "initial_capital_inr": "100000.00",
        },
    )
    assert r.status_code == 404


def test_run_succeeds_when_strategy_present(app):
    strategy_id = uuid4()
    summary = _make_summary(strategy_id)
    fake_strategy = type("S", (), {"id": strategy_id, "root": None})()
    with patch(
        "backend.algo.routes.backtest.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.routes.backtest.run_backtest",
        return_value=summary,
    ):
        client = TestClient(app)
        r = client.post(
            "/v1/algo/backtest/run",
            json={
                "strategy_id": str(strategy_id),
                "period_start": "2024-01-01",
                "period_end": "2024-01-30",
                "initial_capital_inr": "100000.00",
            },
        )
    assert r.status_code == 200
    assert r.json()["run_id"] == str(summary.run_id)


def test_get_runs_404_for_unknown(app):
    client = TestClient(app)
    r = client.get(f"/v1/algo/backtest/runs/{uuid4()}")
    assert r.status_code == 404


def test_get_runs_returns_persisted_summary(app):
    strategy_id = uuid4()
    summary = _make_summary(strategy_id)
    fake_strategy = type("S", (), {"id": strategy_id, "root": None})()
    with patch(
        "backend.algo.routes.backtest.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.routes.backtest.run_backtest",
        return_value=summary,
    ):
        client = TestClient(app)
        client.post(
            "/v1/algo/backtest/run",
            json={
                "strategy_id": str(strategy_id),
                "period_start": "2024-01-01",
                "period_end": "2024-01-30",
                "initial_capital_inr": "100000.00",
            },
        )
        r = client.get(f"/v1/algo/backtest/runs/{summary.run_id}")
    assert r.status_code == 200
    assert r.json()["total_pnl_inr"] == "5000.00"


def test_run_400_on_inverted_period(app):
    strategy_id = uuid4()
    fake_strategy = type("S", (), {"id": strategy_id, "root": None})()
    with patch(
        "backend.algo.routes.backtest.get_strategy",
        new=AsyncMock(return_value=fake_strategy),
    ), patch(
        "backend.algo.routes.backtest.run_backtest",
        side_effect=ValueError("period_start after period_end"),
    ):
        client = TestClient(app)
        r = client.post(
            "/v1/algo/backtest/run",
            json={
                "strategy_id": str(strategy_id),
                "period_start": "2024-06-01",
                "period_end": "2024-01-01",
                "initial_capital_inr": "100000.00",
            },
        )
    assert r.status_code == 400
```

- [ ] **Step 4: Restart backend, run + commit**

```bash
docker compose restart backend
sleep 6
docker compose exec backend python -m pytest backend/algo/tests/test_backtest_routes.py -v 2>&1 | tail -10

git add backend/algo/routes/backtest.py backend/algo/routes/__init__.py backend/routes.py backend/algo/tests/test_backtest_routes.py
git commit -m "$(cat <<'EOF'
feat(algo): /v1/algo/backtest/{run,runs/{id}} endpoints

Slice 7a of the Algo Trading epic. POST /run synchronously
executes the backtest engine and returns BacktestSummary;
GET /runs/{id} fetches a stored summary (in-memory v1; PG-
backed in 7b). pro_or_superuser guard. ValidationError → 400;
unknown strategy → 404; unexpected → 500. 5 endpoint smokes.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: PROGRESS.md + push

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: Insert + commit + push**

Prepend after the `---` separator:

```markdown
## 2026-05-08 (later 4) — Algo Trading Slice 7a: backtest engine (headless)

**Branch:** `feature/algo-trading-session-4-backtest-engine` (built off Session 3's tip)
**Epic:** Algo Trading Platform v1
**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md`
**Plan:** `docs/superpowers/plans/2026-05-08-algo-trading-session-4-backtest-engine.md`

**Shipped (Slice 7a — headless engine only):**
- Pydantic types for the engine boundary (BacktestRequest / BarData / OrderIntent / Fill / Position / BacktestSummary).
- `load_ohlcv_window()` over DuckDB with look-ahead guard (period_end > today raises BackedFutureBarError).
- `SimBroker` filling intents at NEXT bar's open (T+1) with full IndianFeeModel fee accounting + rates_version stamp.
- `PositionTracker` long-only with weighted-avg cost basis + FIFO realised P&L + mark-to-market unrealised.
- `Evaluator` per-bar AST dispatch (compare/and/or/not/if) with action passthrough.
- `runner.run_backtest()` end-to-end orchestrator: bar walk → eval → SimBroker → positions → equity curve + drawdown + summary.
- `algo.events` event_writer with single end-of-run Iceberg commit (no per-event hot loop).
- `POST /v1/algo/backtest/run` + `GET /v1/algo/backtest/runs/{id}` endpoints.

**Tests:** 3 lookahead-guard + 6 sim-broker + 7 positions + 10 evaluator + 4 runner + 5 routes = **35 new tests, all green**. Total algo backend tests: 89 + 35 = **124 passing**.

**Deferred to Session 5 (Slice 7b):**
- Backtest tab UI with equity-curve ECharts + trade table + summary metric cards.
- PG-backed `algo.runs` persistence (replaces in-memory _RUNS dict).
- MinIO artifact upload (PNG equity curve + JSONL events bundle + CSV trade list).
- Universe resolution from strategy.universe.scope via _scoped_tickers.
- Async-job wrapper so /run returns run_id immediately and the UI polls /runs/{id}.

**Deferred to v2:**
- crossover / between / select_top_n / weighted node evaluation (currently no-op).
- set_target_weight resolver (needs portfolio sizer).
- Slippage modelling beyond next-open fills.
- Walk-forward CV harness (current impl is single-period).

---
```

```bash
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
docs(progress): log Algo Trading session 4 — Slice 7a

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
git push -u origin feature/algo-trading-session-4-backtest-engine 2>&1 | tail -5
```

---

## Self-Review (post-write)

**1. Spec coverage (§9 Slice 7 + §13 Risks):**
- Walk-forward CV harness → DEFERRED to v2 (single-period in 7a). Spec calls this out as risk #1; the look-ahead-guard tests (Task 2) at least block the most common failure mode. Documented in "Deferred to v2" section above.
- Slippage model → DEFERRED (current = next-open fill). Spec acceptable for v1.
- Walk-forward MASE / Sharpe metrics → DEFERRED to 7b along with the UI. Win rate + max DD are in the v1 summary.
- Equity curve PNG + trade CSV → DEFERRED to 7b (UI consumer).
- MinIO upload → DEFERRED to 7b.
- algo.runs PG persistence → DEFERRED to 7b. v1 uses an in-memory dict; runs survive only until backend restart (acceptable for a backend-only slice with no UI yet).

**2. Placeholder scan:**
- One inline comment in Task 7 noting that `universe` is opaque in v1 and resolved by 7b. Explicit, scoped — not a TBD.
- Two "v2" comments inline (`set_target_weight` and `crossover/between/select_top_n/weighted`) — explicit deferrals, not TODOs.

**3. Type consistency:**
- `BacktestRequest`/`BarData`/`OrderIntent`/`Fill`/`Position`/`BacktestSummary` consistent across Tasks 1, 3, 4, 6, 7.
- `EvalContext` consistent between evaluator (Task 5) and runner (Task 6).
- `SimBroker.execute()` returns `Fill | None` consistent across tests + runner.
- `event_row()` signature consistent between runner (Task 6) and event_writer (Task 6).
- Endpoint paths (`/v1/algo/backtest/run`, `/v1/algo/backtest/runs/{id}`) consistent between route impl + tests.

No gaps; no inconsistencies.

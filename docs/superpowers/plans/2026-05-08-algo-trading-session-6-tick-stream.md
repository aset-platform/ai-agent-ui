# Algo Trading — Session 6: Tick stream + bar resampler (Slice 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Slice 6 — a per-user Kite WebSocket tick stream that feeds a pure-Python resampler producing 1m + 5m OHLCV bars, persisted append-only to `algo.intraday_bars` (Iceberg). Ships with a replay-from-fixture mode so CI runs without real Kite credentials. No UI tab — Slice 6 is backend infra consumed by Slice 8 (paper runtime).

**Architecture:** Three pure layers + one service shell. (1) `Tick` / `Bar` Pydantic types share the boundary. (2) `Resampler` is a stateful but side-effect-free class — accepts ticks via `feed(tick)`, yields completed bars on minute boundaries via `pop_completed()`. (3) `IntradayBarsWriter` flushes accumulated bars to `algo.intraday_bars` in a single Iceberg commit (CLAUDE.md §4.1 #2). (4) `TickStreamService` orchestrates one of two `TickSource` implementations — `ReplayTickSource` (reads a JSONL fixture, emits at wall-clock pace or fast-forward) or `LiveTickSource` (`KiteTicker` WebSocket wrapper) — through the resampler and writer.

**Tech Stack:** Python 3.12 / asyncio / pyiceberg / kiteconnect.KiteTicker / pytest. No frontend touched. Reuses Slice 0's Iceberg init pattern, Slice 2's `KiteClient`, Slice 7a's writer pattern.

**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md` (§ 2.4 stack, § 8 data layer, § 9.1 slice 6, § 13 risk #6 "Kite WebSocket connection storm").

**Branch:** `feature/algo-trading-session-6-tick-stream` (cut off Session 5's tip `63de3db`).

**Conventions reminders:**
- Per epic spec § 13 risk #6: per-user WS only when paper is active; reconnect-on-bar-close not on tick. v1 wires the *plumbing* — actual lifecycle gating lands in Slice 8.
- Live WS is hard to test deterministically; replay-fixture mode is the canonical CI path (§ 10.1 row 6).
- Bar timestamp is **bar-open** (the start of the minute), per common OHLCV convention. Tick volume accumulates within a bar; close = last tick before the next minute boundary.
- Single Iceberg commit per service-shutdown / batch — never per-bar.
- `algo.intraday_bars` is append-only; we never update existing bars (matches `algo.events`).
- No frontend changes. Spec § 2.2 lists `tick stream` as "hidden under connect"; Slice 6 ships no UI surface.

---

## File Structure

### Backend (new)

- `backend/algo/stream/__init__.py` — package marker.
- `backend/algo/stream/types.py` — `Tick`, `Bar` Pydantic models + `IntervalSec` enum.
- `backend/algo/stream/resampler.py` — `Resampler` class (1m + 5m).
- `backend/algo/stream/bars_writer.py` — `IntradayBarsWriter` (Iceberg single-commit append).
- `backend/algo/stream/sources.py` — `TickSource` Protocol; `ReplayTickSource` (JSONL fixture); `LiveTickSource` (KiteTicker stub-friendly wrapper).
- `backend/algo/stream/service.py` — `TickStreamService` (orchestrator).

### Backend (modified)

- `backend/algo/iceberg_init.py` — add `algo.intraday_bars` schema + create.
- `backend/algo/broker/kite_client.py` — implement `stream_ticks()` against `KiteTicker`.

### Tests (new)

- `backend/algo/tests/test_stream_resampler.py` — pure resampler unit tests.
- `backend/algo/tests/test_stream_replay_source.py` — fixture replay round-trip.
- `backend/algo/tests/test_stream_service.py` — orchestrator end-to-end against a replay fixture.
- `backend/algo/tests/fixtures/ticks_sample.jsonl` — 30 ticks across 3 minutes for one ticker.

---

## Task 1: Iceberg `algo.intraday_bars` table

**Files:**
- Modify: `backend/algo/iceberg_init.py`

- [ ] **Step 1: Extend the iceberg init module**

In `backend/algo/iceberg_init.py`, add after `_events_*` definitions:

```python
_INTRADAY_BARS_TABLE = f"{_NAMESPACE}.intraday_bars"


def _intraday_bars_schema() -> Schema:
    """Schema for ``algo.intraday_bars`` — append-only resampled
    OHLCV bars from the live tick stream.

    Bar open is the start of the bar's interval (e.g. for a 1m
    bar at 09:15:00, the bar holds ticks from [09:15:00, 09:16:00)).
    """
    return Schema(
        NestedField(
            field_id=1, name="ticker",
            field_type=StringType(), required=True,
        ),
        NestedField(
            field_id=2, name="bar_date",
            field_type=StringType(), required=True,
        ),
        NestedField(
            field_id=3, name="interval_sec",
            field_type=LongType(), required=True,
        ),
        NestedField(
            field_id=4, name="bar_open_ts_ns",
            field_type=LongType(), required=True,
        ),
        NestedField(
            field_id=5, name="open",
            field_type=DoubleType(), required=True,
        ),
        NestedField(
            field_id=6, name="high",
            field_type=DoubleType(), required=True,
        ),
        NestedField(
            field_id=7, name="low",
            field_type=DoubleType(), required=True,
        ),
        NestedField(
            field_id=8, name="close",
            field_type=DoubleType(), required=True,
        ),
        NestedField(
            field_id=9, name="volume",
            field_type=LongType(), required=True,
        ),
        NestedField(
            field_id=10, name="written_at",
            field_type=TimestampType(), required=True,
        ),
    )


def _intraday_bars_partition_spec() -> PartitionSpec:
    schema = _intraday_bars_schema()
    ticker_field = next(
        f for f in schema.fields if f.name == "ticker"
    )
    date_field = next(
        f for f in schema.fields if f.name == "bar_date"
    )
    return PartitionSpec(
        PartitionField(
            source_id=ticker_field.field_id,
            field_id=1000,
            transform=IdentityTransform(),
            name="ticker",
        ),
        PartitionField(
            source_id=date_field.field_id,
            field_id=1001,
            transform=IdentityTransform(),
            name="bar_date",
        ),
    )
```

Add `DoubleType` to the imports at top of file (it's not there yet).

In the existing `create_algo_tables()` function, after the existing `_create_table(... _EVENTS_TABLE ...)` call, add:

```python
    _create_table(
        catalog,
        _INTRADAY_BARS_TABLE,
        _intraday_bars_schema(),
        _intraday_bars_partition_spec(),
    )
```

- [ ] **Step 2: Restart backend; verify**

```bash
docker compose restart backend
sleep 6
docker compose exec backend python -c "
from pyiceberg.catalog.sql import SqlCatalog
from backend.algo.iceberg_init import create_algo_tables
create_algo_tables()
print('ok')
" 2>&1 | tail -5
```

Then verify the table is registered:

```bash
docker compose exec backend python -c "
from stocks.create_tables import _get_catalog
cat = _get_catalog()
print([str(t) for t in cat.list_tables('algo')])
" 2>&1 | tail -3
```

Expected output includes both `algo.events` and `algo.intraday_bars`.

- [ ] **Step 3: Commit**

```bash
git add backend/algo/iceberg_init.py
git commit -m "$(cat <<'EOF'
feat(algo): algo.intraday_bars Iceberg table

Slice 6. Append-only 1m + 5m OHLCV bars from the resampled live
tick stream. Partitioned by (ticker, bar_date) for tight DuckDB
scans. Idempotent — create_algo_tables short-circuits if table
already registered.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 2: Tick + Bar types

**Files:**
- Create: `backend/algo/stream/__init__.py`
- Create: `backend/algo/stream/types.py`

- [ ] **Step 1: Package marker**

```python
# backend/algo/stream/__init__.py
"""Tick stream + bar resampler — Slice 6 of the Algo Trading epic."""
```

- [ ] **Step 2: Types**

```python
# backend/algo/stream/types.py
"""Pydantic models shared across the tick-stream pipeline.

Tick = one wire message (from Kite WS or replay fixture).
Bar  = one resampled OHLCV row, written append-only to
       algo.intraday_bars at the close of its interval.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IntervalSec = Literal[60, 300]  # 1m, 5m


class Tick(BaseModel):
    """One quote message from Kite (or a replay fixture)."""
    model_config = ConfigDict(extra="forbid")

    ticker: str
    ts_ns: int = Field(ge=0)
    ltp: float = Field(gt=0)
    volume: int = Field(ge=0)


class Bar(BaseModel):
    """One resampled OHLCV bar."""
    model_config = ConfigDict(extra="forbid")

    ticker: str
    interval_sec: int
    bar_open_ts_ns: int = Field(ge=0)
    open: float
    high: float
    low: float
    close: float
    volume: int = Field(ge=0)
    written_at: datetime
```

- [ ] **Step 3: Smoke + commit**

```bash
docker compose exec backend python -c "
from backend.algo.stream.types import Tick, Bar, IntervalSec
print('ok')
" 2>&1 | tail -3

git add backend/algo/stream/__init__.py backend/algo/stream/types.py
git commit -m "$(cat <<'EOF'
feat(algo): tick stream types module

Slice 6. Tick + Bar Pydantic models shared across resampler /
sources / writer / service. IntervalSec literal restricts bars
to {60, 300} seconds (1m, 5m).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 3: Resampler

**Files:**
- Create: `backend/algo/stream/resampler.py`
- Create: `backend/algo/tests/test_stream_resampler.py`

- [ ] **Step 1: Failing tests**

```python
# backend/algo/tests/test_stream_resampler.py
"""Resampler unit tests — pure logic, no I/O."""
from __future__ import annotations

import pytest

from backend.algo.stream.resampler import Resampler
from backend.algo.stream.types import Tick


def _tick(ticker: str, ts_sec: int, ltp: float, vol: int) -> Tick:
    return Tick(
        ticker=ticker,
        ts_ns=ts_sec * 1_000_000_000,
        ltp=ltp,
        volume=vol,
    )


def test_single_minute_emits_one_1m_bar():
    r = Resampler(intervals=(60,))
    # 09:15:00 → 09:15:30 → 09:15:59 → 09:16:00 (boundary)
    r.feed(_tick("X", 0, 100.0, 10))
    r.feed(_tick("X", 30, 105.0, 5))
    r.feed(_tick("X", 59, 102.0, 3))
    r.feed(_tick("X", 60, 103.0, 1))  # rolls the 09:15 bar
    bars = r.pop_completed()
    assert len(bars) == 1
    bar = bars[0]
    assert bar.interval_sec == 60
    assert bar.open == 100.0
    assert bar.high == 105.0
    assert bar.low == 100.0
    assert bar.close == 102.0
    assert bar.volume == 18  # accumulated within the bar
    assert bar.bar_open_ts_ns == 0


def test_two_intervals_emit_per_minute_and_per_5m():
    r = Resampler(intervals=(60, 300))
    for sec in range(0, 300):
        r.feed(_tick("X", sec, 100.0 + (sec % 5), 1))
    # Boundary tick at 300 closes the 09:15-09:20 5m bar AND
    # rolls the final 1m bar (09:19).
    r.feed(_tick("X", 300, 110.0, 1))
    bars = r.pop_completed()
    one_m = [b for b in bars if b.interval_sec == 60]
    five_m = [b for b in bars if b.interval_sec == 300]
    assert len(one_m) == 5  # 09:15, 09:16, 09:17, 09:18, 09:19
    assert len(five_m) == 1
    assert five_m[0].volume == 300
    assert five_m[0].bar_open_ts_ns == 0


def test_pop_completed_drains():
    r = Resampler(intervals=(60,))
    r.feed(_tick("X", 0, 100.0, 1))
    r.feed(_tick("X", 60, 101.0, 1))  # closes 1st minute
    assert len(r.pop_completed()) == 1
    assert r.pop_completed() == []


def test_multiple_tickers_independent():
    r = Resampler(intervals=(60,))
    r.feed(_tick("A", 0, 100.0, 1))
    r.feed(_tick("B", 0, 200.0, 1))
    r.feed(_tick("A", 60, 105.0, 1))
    r.feed(_tick("B", 60, 210.0, 1))
    bars = r.pop_completed()
    assert {b.ticker for b in bars} == {"A", "B"}
    assert len(bars) == 2


def test_close_partial_bars_flushes_open_intervals():
    r = Resampler(intervals=(60, 300))
    r.feed(_tick("X", 0, 100.0, 5))
    r.feed(_tick("X", 30, 102.0, 2))
    # No boundary tick — caller signals shutdown.
    bars = r.close_partial_bars()
    assert len(bars) == 2  # one 1m + one 5m
    one_m = next(b for b in bars if b.interval_sec == 60)
    assert one_m.open == 100.0
    assert one_m.close == 102.0
    assert one_m.volume == 7
```

- [ ] **Step 2: Run — expect ImportError**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_stream_resampler.py -v 2>&1 | tail -8
```

- [ ] **Step 3: Implement**

```python
# backend/algo/stream/resampler.py
"""Pure tick → OHLCV bar resampler.

State: per (ticker, interval) we hold an in-progress bar with
``open``, ``high``, ``low``, ``last_ltp``, ``volume``,
``bar_open_ts_ns``. On every fed tick:

  1. For each configured interval, compute the bar-open ns that
     this tick belongs to (``ts_ns - (ts_ns % interval_ns)``).
  2. If we have an in-progress bar at a different bar-open, that
     bar has just closed — emit it into the pending queue and
     reset state for the new bar.
  3. Update high / low / last_ltp / volume on the current bar.

``pop_completed()`` drains the queue. ``close_partial_bars()``
forces all in-progress bars to be emitted (used at shutdown).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from backend.algo.stream.types import Bar, Tick

_logger = logging.getLogger(__name__)


class Resampler:
    def __init__(self, intervals: Iterable[int] = (60, 300)) -> None:
        self._intervals = tuple(intervals)
        # key = (ticker, interval) → in-progress bar dict.
        self._open: dict[tuple[str, int], dict] = {}
        self._completed: list[Bar] = []

    @staticmethod
    def _bar_open(ts_ns: int, interval_sec: int) -> int:
        interval_ns = interval_sec * 1_000_000_000
        return ts_ns - (ts_ns % interval_ns)

    def feed(self, tick: Tick) -> None:
        for interval_sec in self._intervals:
            self._feed_one(tick, interval_sec)

    def _feed_one(self, tick: Tick, interval_sec: int) -> None:
        key = (tick.ticker, interval_sec)
        bar_open = self._bar_open(tick.ts_ns, interval_sec)
        existing = self._open.get(key)

        if existing is not None and existing["bar_open"] != bar_open:
            # The new tick belongs to a later bar — close the open one.
            self._completed.append(self._finalize(
                tick.ticker, interval_sec, existing,
            ))
            existing = None

        if existing is None:
            self._open[key] = {
                "bar_open": bar_open,
                "open": tick.ltp,
                "high": tick.ltp,
                "low": tick.ltp,
                "close": tick.ltp,
                "volume": tick.volume,
            }
            return

        if tick.ltp > existing["high"]:
            existing["high"] = tick.ltp
        if tick.ltp < existing["low"]:
            existing["low"] = tick.ltp
        existing["close"] = tick.ltp
        existing["volume"] += tick.volume

    def pop_completed(self) -> list[Bar]:
        out = self._completed
        self._completed = []
        return out

    def close_partial_bars(self) -> list[Bar]:
        """Force-emit any in-progress bars (e.g. at shutdown)."""
        flushed: list[Bar] = []
        for (ticker, interval_sec), state in list(self._open.items()):
            flushed.append(
                self._finalize(ticker, interval_sec, state),
            )
        self._open.clear()
        return flushed

    def _finalize(
        self, ticker: str, interval_sec: int, state: dict,
    ) -> Bar:
        return Bar(
            ticker=ticker,
            interval_sec=interval_sec,
            bar_open_ts_ns=state["bar_open"],
            open=state["open"],
            high=state["high"],
            low=state["low"],
            close=state["close"],
            volume=state["volume"],
            written_at=datetime.now(timezone.utc),
        )
```

- [ ] **Step 4: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_stream_resampler.py -v 2>&1 | tail -10

git add backend/algo/stream/resampler.py backend/algo/tests/test_stream_resampler.py
git commit -m "$(cat <<'EOF'
feat(algo): tick → bar resampler

Slice 6. Pure Resampler class — feed(tick) tracks an in-progress
bar per (ticker, interval); on cross-boundary tick, emits the
closed bar into pop_completed()'s queue. close_partial_bars()
force-flushes at shutdown. 5 unit tests cover single-minute,
multi-interval, drain semantics, multi-ticker, partial flush.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 4: Bars writer (single Iceberg commit)

**Files:**
- Create: `backend/algo/stream/bars_writer.py`

- [ ] **Step 1: Implement**

```python
# backend/algo/stream/bars_writer.py
"""Append-only writer for ``algo.intraday_bars``.

Single Iceberg commit on flush. Mirrors the pattern from
``backend/algo/backtest/event_writer.py``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa

from backend.algo.stream.types import Bar
from stocks.repository import StockRepository

_logger = logging.getLogger(__name__)


def _row(bar: Bar) -> dict[str, Any]:
    bar_date = datetime.fromtimestamp(
        bar.bar_open_ts_ns / 1_000_000_000, tz=timezone.utc,
    ).date().isoformat()
    return {
        "ticker": bar.ticker,
        "bar_date": bar_date,
        "interval_sec": bar.interval_sec,
        "bar_open_ts_ns": bar.bar_open_ts_ns,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": int(bar.volume),
        "written_at": bar.written_at.replace(tzinfo=None),
    }


def flush_bars(bars: list[Bar]) -> None:
    """Single Iceberg commit. No-op on empty list."""
    if not bars:
        return
    repo = StockRepository()
    arrow = pa.Table.from_pylist([_row(b) for b in bars])
    repo._retry_commit(  # noqa: SLF001
        "algo.intraday_bars", "append", arrow,
    )
    _logger.info("flushed %d intraday_bars rows", len(bars))
```

- [ ] **Step 2: Commit**

```bash
git add backend/algo/stream/bars_writer.py
git commit -m "$(cat <<'EOF'
feat(algo): intraday_bars writer — single Iceberg commit

Slice 6. flush_bars(bars) bulk-appends to algo.intraday_bars in
one commit (CLAUDE.md §4.1 #2 — never per-bar). Strips tz from
written_at to match Iceberg's tz-naive TimestampType.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 5: Replay tick source + fixture

**Files:**
- Create: `backend/algo/stream/sources.py` (initial — Replay only; Live added in Task 7)
- Create: `backend/algo/tests/fixtures/ticks_sample.jsonl`
- Create: `backend/algo/tests/test_stream_replay_source.py`

- [ ] **Step 1: Fixture (3 minutes, 30 ticks for ticker FAKE.NS)**

```jsonl
# backend/algo/tests/fixtures/ticks_sample.jsonl
{"ticker": "FAKE.NS", "ts_ns": 0, "ltp": 100.0, "volume": 5}
{"ticker": "FAKE.NS", "ts_ns": 6000000000, "ltp": 100.5, "volume": 3}
{"ticker": "FAKE.NS", "ts_ns": 12000000000, "ltp": 101.0, "volume": 2}
{"ticker": "FAKE.NS", "ts_ns": 18000000000, "ltp": 100.8, "volume": 4}
{"ticker": "FAKE.NS", "ts_ns": 24000000000, "ltp": 100.6, "volume": 1}
{"ticker": "FAKE.NS", "ts_ns": 30000000000, "ltp": 100.4, "volume": 2}
{"ticker": "FAKE.NS", "ts_ns": 36000000000, "ltp": 100.7, "volume": 3}
{"ticker": "FAKE.NS", "ts_ns": 42000000000, "ltp": 100.9, "volume": 1}
{"ticker": "FAKE.NS", "ts_ns": 48000000000, "ltp": 101.2, "volume": 4}
{"ticker": "FAKE.NS", "ts_ns": 54000000000, "ltp": 101.5, "volume": 2}
{"ticker": "FAKE.NS", "ts_ns": 60000000000, "ltp": 101.3, "volume": 5}
{"ticker": "FAKE.NS", "ts_ns": 66000000000, "ltp": 101.4, "volume": 3}
{"ticker": "FAKE.NS", "ts_ns": 72000000000, "ltp": 101.6, "volume": 2}
{"ticker": "FAKE.NS", "ts_ns": 78000000000, "ltp": 101.8, "volume": 4}
{"ticker": "FAKE.NS", "ts_ns": 84000000000, "ltp": 102.0, "volume": 1}
{"ticker": "FAKE.NS", "ts_ns": 90000000000, "ltp": 101.9, "volume": 2}
{"ticker": "FAKE.NS", "ts_ns": 96000000000, "ltp": 102.1, "volume": 3}
{"ticker": "FAKE.NS", "ts_ns": 102000000000, "ltp": 102.3, "volume": 1}
{"ticker": "FAKE.NS", "ts_ns": 108000000000, "ltp": 102.5, "volume": 4}
{"ticker": "FAKE.NS", "ts_ns": 114000000000, "ltp": 102.4, "volume": 2}
{"ticker": "FAKE.NS", "ts_ns": 120000000000, "ltp": 102.6, "volume": 5}
{"ticker": "FAKE.NS", "ts_ns": 126000000000, "ltp": 102.8, "volume": 3}
{"ticker": "FAKE.NS", "ts_ns": 132000000000, "ltp": 103.0, "volume": 2}
{"ticker": "FAKE.NS", "ts_ns": 138000000000, "ltp": 102.9, "volume": 4}
{"ticker": "FAKE.NS", "ts_ns": 144000000000, "ltp": 103.1, "volume": 1}
{"ticker": "FAKE.NS", "ts_ns": 150000000000, "ltp": 103.2, "volume": 2}
{"ticker": "FAKE.NS", "ts_ns": 156000000000, "ltp": 103.4, "volume": 3}
{"ticker": "FAKE.NS", "ts_ns": 162000000000, "ltp": 103.5, "volume": 1}
{"ticker": "FAKE.NS", "ts_ns": 168000000000, "ltp": 103.6, "volume": 4}
{"ticker": "FAKE.NS", "ts_ns": 180000000000, "ltp": 103.8, "volume": 2}
```

(Note: lines beginning with `#` are comments — strip them at parse time. Or store them as plain JSONL; the JSONL loader skips empty/`#`-prefixed lines.)

- [ ] **Step 2: Implement source**

```python
# backend/algo/stream/sources.py
"""Tick sources — Replay (CI fixture) and Live (KiteTicker, Task 7).

A TickSource is an async iterator yielding Tick. Implementations
encapsulate where ticks come from; the resampler doesn't care.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator, Protocol

from backend.algo.stream.types import Tick

_logger = logging.getLogger(__name__)


class TickSource(Protocol):
    def __aiter__(self) -> AsyncIterator[Tick]: ...


class ReplayTickSource:
    """Stream ticks from a JSONL fixture.

    Lines beginning with ``#`` or empty lines are skipped, so the
    fixture file can carry inline comments for clarity.

    ``pace`` controls emit rate:
      - ``"fast"`` → emit immediately (CI default)
      - ``"realtime"`` → sleep based on tick ts_ns deltas (manual demo)
    """

    def __init__(
        self, path: Path, pace: str = "fast",
    ) -> None:
        self._path = Path(path)
        self._pace = pace

    async def __aiter__(self) -> AsyncIterator[Tick]:
        prev_ts_ns: int | None = None
        with self._path.open(encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                payload = json.loads(stripped)
                tick = Tick.model_validate(payload)
                if self._pace == "realtime" and prev_ts_ns is not None:
                    delay_s = max(
                        0.0,
                        (tick.ts_ns - prev_ts_ns) / 1_000_000_000,
                    )
                    await asyncio.sleep(delay_s)
                prev_ts_ns = tick.ts_ns
                yield tick
```

- [ ] **Step 3: Tests**

```python
# backend/algo/tests/test_stream_replay_source.py
"""Replay-from-fixture roundtrip."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.algo.stream.sources import ReplayTickSource

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "ticks_sample.jsonl"
)


@pytest.mark.asyncio
async def test_replay_yields_all_30_ticks():
    src = ReplayTickSource(_FIXTURE, pace="fast")
    ticks = [t async for t in src]
    assert len(ticks) == 30
    assert ticks[0].ticker == "FAKE.NS"
    assert ticks[0].ts_ns == 0
    assert ticks[0].ltp == 100.0
    assert ticks[-1].ts_ns == 180_000_000_000  # 180s


@pytest.mark.asyncio
async def test_replay_skips_blank_and_comment_lines(tmp_path):
    fp = tmp_path / "f.jsonl"
    fp.write_text(
        "# header comment\n"
        "\n"
        '{"ticker":"X","ts_ns":0,"ltp":1.0,"volume":1}\n'
        "# trailing comment\n"
        '{"ticker":"X","ts_ns":1000000000,"ltp":2.0,"volume":1}\n',
        encoding="utf-8",
    )
    ticks = [t async for t in ReplayTickSource(fp, pace="fast")]
    assert len(ticks) == 2
```

- [ ] **Step 4: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_stream_replay_source.py -v 2>&1 | tail -8

git add backend/algo/stream/sources.py \
        backend/algo/tests/fixtures/ticks_sample.jsonl \
        backend/algo/tests/test_stream_replay_source.py
git commit -m "$(cat <<'EOF'
feat(algo): replay tick source + fixture

Slice 6. ReplayTickSource streams ticks from a JSONL fixture with
two pacing modes: "fast" (CI default — no sleeps) and "realtime"
(demo mode that sleeps between ticks based on ts_ns deltas).
Skips blank + comment lines so fixtures can carry inline notes.
30-tick FAKE.NS fixture covering 3 minutes for the orchestrator
test in Task 6. 2 unit tests.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 6: TickStreamService orchestrator

**Files:**
- Create: `backend/algo/stream/service.py`
- Create: `backend/algo/tests/test_stream_service.py`

- [ ] **Step 1: Implement service**

```python
# backend/algo/stream/service.py
"""TickStreamService — orchestrates a TickSource through the
Resampler and persists completed bars.

v1 = single-source-per-instance. Slice 8 (paper) will spawn one
service per active strategy. Multi-tenancy concerns (one Kite WS
per user fan-out across strategies) live there, not here.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Iterable

from backend.algo.stream.bars_writer import flush_bars
from backend.algo.stream.resampler import Resampler
from backend.algo.stream.sources import TickSource
from backend.algo.stream.types import Bar

_logger = logging.getLogger(__name__)

# Type alias for the persistence hook so tests can substitute.
BarFlushFn = Callable[[list[Bar]], None]


class TickStreamService:
    def __init__(
        self,
        source: TickSource,
        intervals: Iterable[int] = (60, 300),
        flush: BarFlushFn = flush_bars,
        flush_threshold: int = 100,
    ) -> None:
        self._source = source
        self._resampler = Resampler(intervals=intervals)
        self._flush = flush
        self._threshold = flush_threshold
        self._buffer: list[Bar] = []

    async def run(self) -> int:
        """Drain the source through the resampler. Returns the
        total number of bars persisted.
        """
        try:
            async for tick in self._source:
                self._resampler.feed(tick)
                self._buffer.extend(self._resampler.pop_completed())
                if len(self._buffer) >= self._threshold:
                    self._flush(self._buffer)
                    _logger.info(
                        "flushed batch (%d bars)", len(self._buffer),
                    )
                    self._buffer = []
        finally:
            # Force-emit any in-flight bars on shutdown.
            self._buffer.extend(
                self._resampler.close_partial_bars(),
            )
            if self._buffer:
                self._flush(self._buffer)
                count = len(self._buffer)
                self._buffer = []
                return count
        return 0
```

- [ ] **Step 2: Tests**

```python
# backend/algo/tests/test_stream_service.py
"""End-to-end orchestrator over the replay fixture."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.algo.stream.service import TickStreamService
from backend.algo.stream.sources import ReplayTickSource

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "ticks_sample.jsonl"
)


@pytest.mark.asyncio
async def test_service_resamples_fixture_and_flushes_bars():
    flush = MagicMock()
    src = ReplayTickSource(_FIXTURE, pace="fast")
    svc = TickStreamService(
        source=src, intervals=(60, 300), flush=flush,
    )
    await svc.run()
    flush.assert_called()
    # Concatenate all flushed bars across calls.
    all_bars = [b for c in flush.call_args_list for b in c.args[0]]
    one_m = [b for b in all_bars if b.interval_sec == 60]
    five_m = [b for b in all_bars if b.interval_sec == 300]
    # 30 ticks across 3m + a final boundary tick at 180s →
    # full 1m bars at 0s, 60s, 120s; 5m closes at 300s+ but
    # close_partial_bars flushes the partial 0-300 bar.
    assert len(one_m) >= 3
    assert len(five_m) >= 1
    # Open of the very first 1m bar must equal the first tick LTP.
    first_one_m = sorted(one_m, key=lambda b: b.bar_open_ts_ns)[0]
    assert first_one_m.open == 100.0


@pytest.mark.asyncio
async def test_service_no_flush_on_empty_source(tmp_path):
    fp = tmp_path / "empty.jsonl"
    fp.write_text("", encoding="utf-8")
    flush = MagicMock()
    svc = TickStreamService(
        source=ReplayTickSource(fp, pace="fast"),
        intervals=(60,),
        flush=flush,
    )
    await svc.run()
    flush.assert_not_called()
```

- [ ] **Step 3: Run + commit**

```bash
docker compose exec backend python -m pytest backend/algo/tests/test_stream_service.py -v 2>&1 | tail -8

git add backend/algo/stream/service.py backend/algo/tests/test_stream_service.py
git commit -m "$(cat <<'EOF'
feat(algo): TickStreamService — orchestrator

Slice 6. Drains a TickSource through the Resampler, batches
completed bars, flushes via flush_bars (or a substituted hook).
Force-emits in-flight bars on shutdown via close_partial_bars.
2 end-to-end tests cover the full pipeline with the replay
fixture + an empty source no-op.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 7: Live tick source (`KiteTicker` wrapper) + KiteClient.stream_ticks

**Files:**
- Modify: `backend/algo/stream/sources.py` (add LiveTickSource)
- Modify: `backend/algo/broker/kite_client.py` (replace stream_ticks stub)

- [ ] **Step 1: Add LiveTickSource to sources.py**

Append to `backend/algo/stream/sources.py`:

```python
class LiveTickSource:
    """KiteTicker WebSocket → Tick stream.

    The kiteconnect.KiteTicker library is callback-based; we adapt
    it into an async iterator by accumulating ticks into an asyncio
    queue inside the on_ticks callback and yielding from there.

    Connection lifecycle is owned by the caller: pass an
    instrument_tokens list at construction; when ``__aiter__``
    starts, we ``connect()`` (non-blocking) and unwind on the
    first exception in the queue or explicit ``close()``.
    """

    def __init__(
        self,
        api_key: str,
        access_token: str,
        instrument_tokens: list[int],
        token_to_ticker: dict[int, str],
    ) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._instrument_tokens = instrument_tokens
        self._token_to_ticker = token_to_ticker
        self._queue: asyncio.Queue[Tick | None] = asyncio.Queue()
        self._kt = None  # KiteTicker, lazy-imported

    def _build_ticker(self):
        from kiteconnect import KiteTicker
        kt = KiteTicker(
            self._api_key, self._access_token,
        )
        loop = asyncio.get_running_loop()

        def on_ticks(_ws, ticks):
            now_ns = int(
                __import__("time").time() * 1_000_000_000,
            )
            for raw in ticks:
                tok = raw.get("instrument_token")
                ticker = self._token_to_ticker.get(tok)
                if not ticker:
                    continue
                tick = Tick(
                    ticker=ticker,
                    ts_ns=now_ns,
                    ltp=float(raw.get("last_price", 0) or 0),
                    volume=int(raw.get("last_traded_quantity", 0) or 0),
                )
                loop.call_soon_threadsafe(
                    self._queue.put_nowait, tick,
                )

        def on_connect(ws, _resp):
            ws.subscribe(self._instrument_tokens)
            ws.set_mode(ws.MODE_LTP, self._instrument_tokens)

        def on_close(_ws, _code, _reason):
            loop.call_soon_threadsafe(
                self._queue.put_nowait, None,
            )

        kt.on_ticks = on_ticks
        kt.on_connect = on_connect
        kt.on_close = on_close
        return kt

    async def __aiter__(self) -> AsyncIterator[Tick]:
        if self._kt is None:
            self._kt = self._build_ticker()
            self._kt.connect(threaded=True)
        while True:
            tick = await self._queue.get()
            if tick is None:
                return
            yield tick

    def close(self) -> None:
        if self._kt is not None:
            try:
                self._kt.close()
            except Exception:  # noqa: BLE001
                _logger.exception("KiteTicker close failed")
```

- [ ] **Step 2: Replace `stream_ticks` stub in `kite_client.py`**

```python
    async def stream_ticks(
        self,
        instrument_tokens: list[int],
        token_to_ticker: dict[int, str],
    ) -> "AsyncIterator[Tick]":  # type: ignore[name-defined]
        """Slice 6: live KiteTicker WebSocket → Tick stream.

        Caller owns the lifecycle — wrap in `async for` and
        break / cancel to stop.
        """
        from backend.algo.stream.sources import LiveTickSource
        from backend.algo.stream.types import Tick  # noqa: F401

        if self._access_token is None:
            raise RuntimeError(
                "stream_ticks requires an access_token; "
                "complete the OAuth handshake first.",
            )
        src = LiveTickSource(
            api_key=self._api_key,
            access_token=self._access_token,
            instrument_tokens=instrument_tokens,
            token_to_ticker=token_to_ticker,
        )
        async for tick in src:
            yield tick
```

Also update the `__init__` to capture `_access_token` (currently calls `self._kc.set_access_token` but doesn't keep the value):

```python
    def __init__(
        self,
        api_key: str,
        access_token: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._kc = KiteConnect(api_key=api_key)
        if access_token:
            self._kc.set_access_token(access_token)
```

- [ ] **Step 3: Smoke + commit**

```bash
docker compose exec backend python -c "
from backend.algo.stream.sources import LiveTickSource
print('ok')
" 2>&1 | tail -3

# Confirm KiteClient still imports cleanly.
docker compose exec backend python -c "
from backend.algo.broker.kite_client import KiteClient
print('ok')
" 2>&1 | tail -3

git add backend/algo/stream/sources.py backend/algo/broker/kite_client.py
git commit -m "$(cat <<'EOF'
feat(algo): LiveTickSource + KiteClient.stream_ticks

Slice 6. LiveTickSource adapts kiteconnect.KiteTicker (callback-
based) into an async iterator via an asyncio.Queue + thread-safe
put_nowait from the on_ticks callback. KiteClient.stream_ticks
delegates to it and yields Ticks. on_close enqueues a sentinel
None to terminate the iterator gracefully. KiteClient now
captures _access_token so the wrapper can rebuild a KiteTicker
connection (set_access_token alone is insufficient since
KiteTicker takes the token at construction).

No automated test for the live WS path itself — exercised via
replay-fixture mode in the orchestrator tests. Real WS smoke
deferred to manual paper-trading verification (Slice 8).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Task 8: PROGRESS + push

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: Insert PROGRESS entry**

Prepend after the `# PROGRESS.md` header + `---`:

```markdown
## 2026-05-08 (later 8) — Algo Trading Slice 6: tick stream + bar resampler

**Branch:** `feature/algo-trading-session-6-tick-stream` (built off Session 5's tip)
**Epic:** Algo Trading Platform v1
**Spec:** `docs/superpowers/specs/2026-05-08-algo-trading-platform-design.md`
**Plan:** `docs/superpowers/plans/2026-05-08-algo-trading-session-6-tick-stream.md`

**Shipped (Slice 6 — backend infra only, no UI):**
- `algo.intraday_bars` Iceberg table (partitioned by ticker + bar_date).
- `Tick` + `Bar` Pydantic types.
- `Resampler` — pure tick → 1m + 5m OHLCV bars; close_partial_bars on shutdown.
- `IntradayBarsWriter.flush_bars()` — single Iceberg commit per batch.
- `ReplayTickSource` — JSONL fixture for CI; "fast" + "realtime" pacing.
- `LiveTickSource` — `KiteTicker` WebSocket adapter (callback → async iterator).
- `TickStreamService` — orchestrator: source → resampler → writer.
- `KiteClient.stream_ticks()` implemented (replaces Slice 2 stub).

**Tests:** 5 resampler + 2 replay source + 2 service + 30-tick FAKE.NS fixture = **9 new pytest cases**. Total algo backend tests: ~145 passing.

**Deferred:**
- Live WebSocket smoke test (requires real Kite credentials) → manual verification at Slice 8.
- Per-user multiplexing across strategies (one WS, many subscribers) → Slice 8.
- Reconnect-on-bar-close / backoff lifecycle gating → Slice 8.
- Tick stream consumer endpoint / observability counters → out of v1 scope.

---
```

- [ ] **Step 2: Commit + push**

```bash
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
docs(progress): log Algo Trading session 6 — Slice 6

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
git push -u origin feature/algo-trading-session-6-tick-stream 2>&1 | tail -5
```

---

## Self-Review (post-write)

**1. Spec coverage (§ 9.1 Slice 6 + § 10.1 row 6):**
- KiteAdapter WS per-user → Task 7 (`LiveTickSource` + `KiteClient.stream_ticks`).
- Resampler service → Tasks 3, 6.
- `algo.intraday_bars` Iceberg table → Task 1.
- Replay-from-fixture mode for CI → Task 5 (`ReplayTickSource`).
- Multiplexed connection (one WS per user, many subscribers) → DEFERRED to Slice 8 — the v1 service is single-source-per-instance; multi-tenancy lives in paper runtime where strategy lifecycle decides when to subscribe. Documented in service.py docstring.
- Reconnect-on-bar-close / backoff (§ 13 risk #6) → DEFERRED to Slice 8 — the v1 LiveTickSource uses kiteconnect's built-in reconnect (it handles `RECONNECT_MAX_TRIES` internally); custom bar-close gating belongs to the strategy lifecycle.

**2. Placeholder scan:**
- Fixture format note in Task 5 explicitly calls out comment-line skipping in the loader (matched in tests).
- LiveTickSource has `# noqa: BLE001` on the close()'s catch-all — explicit, scoped.
- No "TBD"/"TODO"/"implement later" anywhere.

**3. Type consistency:**
- `Tick` + `Bar` consistent across Tasks 2, 3, 4, 5, 6, 7.
- `IntervalSec` consistent between types and Resampler default `(60, 300)`.
- `BarFlushFn` callable shape consistent between service and tests.
- `LiveTickSource(api_key, access_token, instrument_tokens, token_to_ticker)` signature matches the call site in Task 7's `KiteClient.stream_ticks`.

**4. Adaptations expected during execution:**
- The fixture file Task 5 writes contains `#`-prefixed comment lines for clarity; the loader handles them. If pyiceberg or jsonl tooling rejects these, switch to plain JSONL (no comments) and inline the note in the test docstring.
- KiteTicker's import path is `from kiteconnect import KiteTicker`; if the SDK version differs across `kiteconnect==5.0.1` (Session 3 pin) vs newer, adapt the on_close callback signature (some versions pass `(ws, code, reason)`, others `(ws, code, reason, was_clean)`).
- `_access_token` capture in `__init__` is a behavior change — verify nothing else in the codebase relies on KiteClient NOT having that attribute (`grep -rn "kite_client._access_token"` should return nothing).

No gaps; type drift addressed; all placeholders are scoped to engineer-side substitution with explicit instructions.

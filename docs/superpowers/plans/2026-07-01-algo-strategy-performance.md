# Strategy Performance — Mode Filters + Trade-Level Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Strategies → Performance page so it shows mode-aware (Backtest / Walk-forward / Paper / Live), strategy-scoped, trade-level performance (win rate, biggest win/loss, profit factor) sourced from a new daily-batch-materialized `algo.closed_trades` table for Paper/Live, and from existing `algo.runs.summary_json` for Backtest/Walk-forward.

**Architecture:** A new daily scheduled job (`algo_closed_trades_rollup`) reads `algo.events` once per day (off-hours), FIFO-pairs BUY/SELL fills per (user, strategy, ticker) over a trailing 400-day window, and idempotently upserts closed trades into a new small Postgres table `algo.closed_trades`. A new endpoint `GET /v1/algo/performance/summary` reads from `algo.closed_trades` (paper/live) or `algo.runs.summary_json.trade_list` (backtest/walkforward) depending on the selected mode, and returns per-strategy trade statistics. The frontend gets a filter bar (mode/strategy/lookback+custom-range) and a rebuilt table layout.

**Tech Stack:** FastAPI + SQLAlchemy async (Postgres), PyIceberg/DuckDB (`query_iceberg_table`), Alembic migrations, Next.js/React + SWR, Vitest, pytest.

## Global Constraints

- Line length 79 chars (black/isort/flake8) — all new Python files.
- `X | None` not `Optional[X]` (PEP 604).
- No bare `print()` — use `_logger = logging.getLogger(__name__)`; caught exceptions in jobs MUST log with `exc_info=True`.
- Tools/routes: `@tool` functions return error strings; FastAPI routes `raise HTTPException`.
- Scheduler jobs MUST use `disposable_pg_session()` (NullPool, per-call) — never the cached `get_session_factory()` singleton from a bare `asyncio.run()` context.
- New Iceberg reads: no full-table scans — always filter by `ts_date` range and project only needed columns.
- Every new endpoint returning derived data: Redis cache with `TTL_STABLE` (300s), key prefix `cache:algo:perf:`.
- `apiFetch` not bare `fetch` on the frontend; SWR hooks in `frontend/hooks/`.
- New table/list frontend components with ≥8 columns: `useColumnSelection` + `ColumnSelector` + `DownloadCsvButton` (already satisfied by reusing `TradeLogTable`, see Task 8).
- Branch off `dev`; never commit directly on `dev`/`qa`/`release`/`main`. All work in this plan happens on `feature/algo-strategy-performance` (already created).
- Co-Authored-By: `Abhay Kumar Singh <asequitytrading@gmail.com>` on every commit.

**Design deviation from the spec, noted here explicitly:** the spec's rollout step 2 said "extract a shared FIFO-pairing helper from `routes/attribution.py`." On detailed design (Task 2 below), the safer choice is a **fresh, independent module** modeled on `attribution.py`'s pairing logic rather than refactoring the existing, already-tested (5 regression tests covering subtle historical bugs like ASETPLTFRM-381 panic-close pairing) single-day endpoint. The new module buckets by `(strategy_id, ticker)` instead of `ticker`-only (a correctness improvement for multi-strategy data) and additionally captures `dry_run` + `event_id` fields that the daily rollup needs but the single-day attribution view doesn't. `routes/attribution.py` is **not modified** by this plan.

---

## File Structure

```
backend/db/migrations/versions/
  2026_07_01_algo_closed_trades.py          [create] table migration

backend/algo/attribution/
  trade_pairing.py                          [create] FIFO fill-pairing (fresh module, see deviation note)

backend/algo/jobs/
  closed_trades_rollup.py                   [create] daily rollup job (core logic + entrypoint)

backend/jobs/executor.py                    [modify] register "algo_closed_trades_rollup"

scripts/
  seed_closed_trades_rollup.py              [create] scheduled_jobs row seeder
  backfill_closed_trades.py                 [create] one-time full-history backfill

backend/algo/routes/performance.py          [modify] add GET /summary

backend/algo/tests/
  test_trade_pairing.py                     [create]
  test_closed_trades_rollup.py              [create]
  test_performance_routes.py                [modify] add /summary tests

frontend/hooks/
  useStrategyPerformance.ts                 [create] SWR hook for /algo/performance/summary

frontend/components/algo-trading/
  TradeLogTable.tsx                         [create, was BacktestTradeTable.tsx]
  BacktestTradeTable.tsx                    [delete, superseded by TradeLogTable.tsx]
  BacktestTab.tsx                           [modify] import update only
  PerformanceTab.tsx                        [modify] full rewrite
  __tests__/PerformanceTab.test.tsx         [create]

PROGRESS.md                                 [modify] session entry
```

---

### Task 1: Migration — `algo.closed_trades` table

**Files:**
- Create: `backend/db/migrations/versions/2026_07_01_algo_closed_trades.py`

**Interfaces:**
- Produces: table `algo.closed_trades` with columns `id, user_id, strategy_id, mode, ticker, qty, avg_price, fill_price, opened_at, closed_at, opened_at_ts_ns, closed_at_ts_ns, realised_pnl_inr, return_pct, exit_reason, dry_run, buy_event_id, sell_event_id, computed_at`, unique constraint on `(buy_event_id, sell_event_id)`, index `ix_closed_trades_lookup` on `(user_id, strategy_id, mode, closed_at DESC)`. Every later task that reads/writes this table depends on these exact column names.

- [ ] **Step 1: Confirm current alembic head**

Run: `cd backend/db/migrations/versions && grep -ho "^revision = \"[^\"]*\"" *.py | sed 's/revision = //;s/"//g' | sort > /tmp/rev.txt && grep -ho "^down_revision = \"[^\"]*\"" *.py | sed 's/down_revision = //;s/"//g' | sort > /tmp/down.txt && comm -23 /tmp/rev.txt /tmp/down.txt`

Expected output: `2026_06_23_gtt_headroom` (this is the current head; if a different value prints, use that value as `down_revision` in Step 2 instead).

- [ ] **Step 2: Write the migration**

```python
"""Add algo.closed_trades — materialized closed-trade rollup for
the Strategy Performance page (Paper/Live modes).

Revision ID: 2026_07_01_closed_trades
Revises: 2026_06_23_gtt_headroom
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_07_01_closed_trades"
down_revision = "2026_06_23_gtt_headroom"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "closed_trades",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "strategy_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "algo.strategies.id", ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("avg_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("fill_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("opened_at", sa.Date, nullable=False),
        sa.Column("closed_at", sa.Date, nullable=False),
        sa.Column("opened_at_ts_ns", sa.BigInteger, nullable=True),
        sa.Column("closed_at_ts_ns", sa.BigInteger, nullable=True),
        sa.Column(
            "realised_pnl_inr", sa.Numeric(14, 2), nullable=False,
        ),
        sa.Column("return_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column(
            "exit_reason", sa.String(32), nullable=False,
            server_default="signal",
        ),
        sa.Column(
            "dry_run", sa.Boolean, nullable=False,
            server_default="false",
        ),
        sa.Column("buy_event_id", sa.String(64), nullable=False),
        sa.Column("sell_event_id", sa.String(64), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "buy_event_id", "sell_event_id",
            name="uq_closed_trades_fill_pair",
        ),
        schema="algo",
    )
    op.create_index(
        "ix_closed_trades_lookup",
        "closed_trades",
        ["user_id", "strategy_id", "mode", "closed_at"],
        schema="algo",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_closed_trades_lookup",
        table_name="closed_trades",
        schema="algo",
    )
    op.drop_table("closed_trades", schema="algo")
```

- [ ] **Step 3: Run the migration**

Run: `PYTHONPATH=. alembic upgrade head`
Expected: no errors; final log line shows upgrade to `2026_07_01_closed_trades`.

- [ ] **Step 4: Verify the table exists**

Run: `docker compose exec postgres psql -U postgres -d ai_agent_ui -c "\d algo.closed_trades"`
Expected: column list matching Step 2, plus the unique constraint and index.

- [ ] **Step 5: Commit**

```bash
git add backend/db/migrations/versions/2026_07_01_algo_closed_trades.py
git commit -m "$(cat <<'EOF'
feat(algo): add algo.closed_trades table for Strategy Performance

New materialized table for the daily closed-trades rollup job
(Paper/Live modes). Idempotent unique constraint on
(buy_event_id, sell_event_id) so the daily job and the one-time
backfill can both upsert safely.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 2: FIFO fill-pairing module

**Files:**
- Create: `backend/algo/attribution/trade_pairing.py`
- Test: `backend/algo/tests/test_trade_pairing.py`

**Interfaces:**
- Consumes: raw `algo.events` rows as `list[dict]` with keys `event_id, strategy_id, type, payload_json, ts_ns` (shape returned by `backend.db.duckdb_engine.query_iceberg_table`).
- Produces: `pair_fills_by_strategy_and_ticker(events: list[dict]) -> list[dict]`, each output dict has keys: `strategy_id, ticker, qty, avg_price, fill_price, opened_at (date), closed_at (date), opened_at_ts_ns, closed_at_ts_ns, realised_pnl_inr (float), return_pct (float), exit_reason (str), dry_run (bool), buy_event_id, sell_event_id`. Task 3 (rollup job) and Task 6 (endpoint tests) both depend on these exact key names.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the FIFO buy/sell fill-pairing helper used by the
Strategy Performance closed-trades rollup job."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.algo.attribution.trade_pairing import (
    pair_fills_by_strategy_and_ticker,
)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def _fill(
    *, strategy_id, event_id, symbol, side, qty, fill_price, ts,
    event_type="order_filled", exit_reason=None, dry_run=False,
):
    payload = {
        "symbol": symbol, "side": side, "qty": qty,
        "fill_price": fill_price, "dry_run": dry_run,
    }
    if exit_reason is not None:
        payload["exit_reason"] = exit_reason
    return {
        "event_id": event_id,
        "strategy_id": strategy_id,
        "type": event_type,
        "payload_json": json.dumps(payload),
        "ts_ns": _ts(ts),
    }


def test_pairs_single_buy_sell_fifo():
    events = [
        _fill(
            strategy_id="s1", event_id="e1", symbol="ITC",
            side="BUY", qty=10, fill_price=300.0,
            ts=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="e2", symbol="ITC",
            side="SELL", qty=10, fill_price=310.0,
            ts=datetime(2026, 6, 5, tzinfo=timezone.utc),
            exit_reason="signal",
        ),
    ]
    trades = pair_fills_by_strategy_and_ticker(events)
    assert len(trades) == 1
    t = trades[0]
    assert t["ticker"] == "ITC"
    assert t["strategy_id"] == "s1"
    assert t["qty"] == 10
    assert t["avg_price"] == 300.0
    assert t["fill_price"] == 310.0
    assert t["realised_pnl_inr"] == 100.0
    assert round(t["return_pct"], 4) == round(10 / 300 * 100, 4)
    assert t["exit_reason"] == "signal"
    assert t["dry_run"] is False
    assert t["buy_event_id"] == "e1"
    assert t["sell_event_id"] == "e2"
    assert t["opened_at"].isoformat() == "2026-06-01"
    assert t["closed_at"].isoformat() == "2026-06-05"


def test_does_not_mix_two_strategies_on_same_ticker():
    """Two different strategies both trading ITC on the same day
    must NOT be cross-paired (strategy A's buy with strategy B's
    sell)."""
    events = [
        _fill(
            strategy_id="s1", event_id="e1", symbol="ITC",
            side="BUY", qty=10, fill_price=300.0,
            ts=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s2", event_id="e2", symbol="ITC",
            side="BUY", qty=5, fill_price=305.0,
            ts=datetime(2026, 6, 1, 1, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="e3", symbol="ITC",
            side="SELL", qty=10, fill_price=310.0,
            ts=datetime(2026, 6, 5, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s2", event_id="e4", symbol="ITC",
            side="SELL", qty=5, fill_price=308.0,
            ts=datetime(2026, 6, 6, tzinfo=timezone.utc),
        ),
    ]
    trades = pair_fills_by_strategy_and_ticker(events)
    assert len(trades) == 2
    by_strategy = {t["strategy_id"]: t for t in trades}
    assert by_strategy["s1"]["buy_event_id"] == "e1"
    assert by_strategy["s1"]["sell_event_id"] == "e3"
    assert by_strategy["s2"]["buy_event_id"] == "e2"
    assert by_strategy["s2"]["sell_event_id"] == "e4"


def test_unmatched_open_position_is_skipped():
    events = [
        _fill(
            strategy_id="s1", event_id="e1", symbol="TCS",
            side="BUY", qty=1, fill_price=4000.0,
            ts=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ),
    ]
    assert pair_fills_by_strategy_and_ticker(events) == []


def test_strips_ns_suffix_and_ignores_non_fill_events():
    events = [
        {
            "event_id": "sig1", "strategy_id": "s1",
            "type": "signal_generated",
            "payload_json": '{"ticker": "ITC.NS", "side": "BUY"}',
            "ts_ns": _ts(datetime(2026, 6, 1, tzinfo=timezone.utc)),
        },
        _fill(
            strategy_id="s1", event_id="e1", symbol="ITC",
            side="BUY", qty=2, fill_price=300.0,
            ts=datetime(2026, 6, 1, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="e2", symbol="ITC",
            side="SELL", qty=2, fill_price=290.0,
            event_type="order_filled_live",
            ts=datetime(2026, 6, 2, tzinfo=timezone.utc),
        ),
    ]
    trades = pair_fills_by_strategy_and_ticker(events)
    assert len(trades) == 1
    assert trades[0]["ticker"] == "ITC"
    assert trades[0]["realised_pnl_inr"] == -20.0
    # No explicit exit_reason on the SELL payload -> defaults.
    assert trades[0]["exit_reason"] == "signal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/algo/tests/test_trade_pairing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.algo.attribution.trade_pairing'`.

- [ ] **Step 3: Write the implementation**

```python
"""FIFO buy/sell fill-pairing for closed-trade reconstruction.

Fresh implementation modeled on the single-day pairing logic in
``routes/attribution.py``, adapted for the Strategy Performance
closed-trades rollup job (backend/algo/jobs/closed_trades_rollup.py):

* Buckets by ``(strategy_id, ticker)`` instead of ``ticker``-only,
  so two strategies trading the same ticker never cross-pair.
* Captures ``dry_run`` and both fill ``event_id``s (needed for the
  idempotent upsert key into ``algo.closed_trades``).
* Does not enrich with signal/regime context — that's specific to
  the single-day attribution view and out of scope here.

See ``docs/superpowers/specs/2026-07-01-algo-strategy-performance-design.md``
for why this is a fresh module rather than an extraction from
``routes/attribution.py``.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

_logger = logging.getLogger(__name__)

_FILL_TYPES = ("order_filled", "order_filled_live")


def pair_fills_by_strategy_and_ticker(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """FIFO-pair BUY/SELL fills into closed trades.

    ``events`` rows must carry: ``event_id``, ``strategy_id``,
    ``type``, ``payload_json``, ``ts_ns``. Only
    ``order_filled`` / ``order_filled_live`` rows are considered;
    everything else (signals, GTT events, etc.) is ignored.

    Fill payloads carry ``symbol`` (no ``.NS`` suffix, e.g.
    ``"ITC"``); some legacy rows may carry ``ticker`` instead
    (with the suffix) — both are normalised to the same canonical
    symbol.

    Unmatched fills (an open position with no closing SELL yet)
    are skipped — they are not "closed trades".
    """
    fills_by_key: dict[tuple[str, str], list[dict]] = {}
    for ev in events:
        if ev.get("type") not in _FILL_TYPES:
            continue
        try:
            payload = json.loads(ev.get("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            _logger.warning(
                "trade_pairing: unparseable payload_json for "
                "event_id=%s", ev.get("event_id"),
            )
            continue
        raw_sym = payload.get("symbol") or payload.get("ticker") or ""
        sym = str(raw_sym).upper().removesuffix(".NS")
        if not sym:
            continue
        strategy_id = str(ev.get("strategy_id") or "")
        key = (strategy_id, sym)
        fills_by_key.setdefault(key, []).append(
            {**ev, "_payload": payload},
        )

    out: list[dict[str, Any]] = []
    for (strategy_id, sym), fills in fills_by_key.items():
        buys = sorted(
            (f for f in fills if f["_payload"].get("side") == "BUY"),
            key=lambda f: int(f.get("ts_ns") or 0),
        )
        sells = sorted(
            (f for f in fills if f["_payload"].get("side") == "SELL"),
            key=lambda f: int(f.get("ts_ns") or 0),
        )
        for i in range(min(len(buys), len(sells))):
            buy_fill, sell_fill = buys[i], sells[i]
            avg_price = float(
                buy_fill["_payload"].get("fill_price") or 0,
            )
            fill_price = float(
                sell_fill["_payload"].get("fill_price") or 0,
            )
            qty = int(buy_fill["_payload"].get("qty") or 0)
            realised_pnl_inr = (fill_price - avg_price) * qty
            return_pct = (
                (fill_price - avg_price) / avg_price * 100
                if avg_price else 0.0
            )
            out.append({
                "strategy_id": strategy_id or None,
                "ticker": sym,
                "qty": qty,
                "avg_price": avg_price,
                "fill_price": fill_price,
                "opened_at": _ts_ns_to_date(int(buy_fill["ts_ns"])),
                "closed_at": _ts_ns_to_date(int(sell_fill["ts_ns"])),
                "opened_at_ts_ns": int(buy_fill["ts_ns"]),
                "closed_at_ts_ns": int(sell_fill["ts_ns"]),
                "realised_pnl_inr": realised_pnl_inr,
                "return_pct": return_pct,
                "exit_reason": (
                    sell_fill["_payload"].get("exit_reason")
                    or "signal"
                ),
                "dry_run": bool(
                    sell_fill["_payload"].get("dry_run", False),
                ),
                "buy_event_id": buy_fill.get("event_id"),
                "sell_event_id": sell_fill.get("event_id"),
            })
    return out


def _ts_ns_to_date(ts_ns: int) -> date:
    return datetime.fromtimestamp(
        ts_ns / 1_000_000_000, tz=timezone.utc,
    ).date()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/algo/tests/test_trade_pairing.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/attribution/trade_pairing.py backend/algo/tests/test_trade_pairing.py
git commit -m "$(cat <<'EOF'
feat(algo): add FIFO fill-pairing helper for closed-trade rollup

Fresh module (not an attribution.py extraction — see plan's
Global Constraints deviation note) that buckets fills by
(strategy_id, ticker) instead of ticker-only, avoiding
cross-strategy pairing on shared tickers.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 3: Daily rollup job

**Files:**
- Create: `backend/algo/jobs/closed_trades_rollup.py`
- Modify: `backend/jobs/executor.py` (add registration near the other `algo_*` jobs, after line ~3810)
- Test: `backend/algo/tests/test_closed_trades_rollup.py`

**Interfaces:**
- Consumes: `pair_fills_by_strategy_and_ticker` from Task 2.
- Produces: `run_closed_trades_rollup_job(payload: dict | None = None) -> dict` (sync entrypoint, mirrors `run_budget_reservations_retention_job`). Payload keys: `window_days` (int, default 400; the Task 5 backfill script passes a larger bounded value, `3650` — no unbounded scan, per the plan's own "no full-table scans" constraint), `today` (ISO date override for testing), `dry_run` (bool). Task 4 (seed script) and Task 5 (backfill script) both call this function.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the algo_closed_trades_rollup daily job."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.algo.jobs.closed_trades_rollup import (
    run_closed_trades_rollup_job,
)


def _ts(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def _fill_row(strategy_id, event_id, symbol, side, qty, price, ts, mode):
    return {
        "event_id": event_id,
        "strategy_id": strategy_id,
        "user_id": str(uuid4()),
        "mode": mode,
        "type": "order_filled" if mode == "paper" else "order_filled_live",
        "payload_json": json.dumps({
            "symbol": symbol, "side": side, "qty": qty,
            "fill_price": price,
        }),
        "ts_ns": _ts(ts),
    }


@pytest.fixture
def fake_session():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


def _disposable_session_cm(session):
    class _CM:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *a):
            return None

    return lambda: _CM()


def test_upserts_paired_trades(fake_session):
    sid = str(uuid4())
    uid = str(uuid4())
    events = [
        {**_fill_row(
            sid, "e1", "ITC", "BUY", 10, 300.0,
            datetime(2026, 6, 1, tzinfo=timezone.utc), "paper",
        ), "user_id": uid},
        {**_fill_row(
            sid, "e2", "ITC", "SELL", 10, 310.0,
            datetime(2026, 6, 5, tzinfo=timezone.utc), "paper",
        ), "user_id": uid},
    ]
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=events,
    ), patch(
        "backend.db.engine.disposable_pg_session",
        _disposable_session_cm(fake_session),
    ), patch("cache.get_cache") as mock_cache:
        mock_cache.return_value = MagicMock()
        result = run_closed_trades_rollup_job(
            {"today": "2026-06-06"},
        )
    assert result["status"] == "ok"
    assert result["trades_upserted"] == 1
    # One INSERT ... ON CONFLICT executed against algo.closed_trades.
    assert fake_session.execute.await_count == 1
    call_sql = str(fake_session.execute.await_args_list[0].args[0])
    assert "algo.closed_trades" in call_sql
    assert "ON CONFLICT" in call_sql


def test_no_events_returns_zero_upserted(fake_session):
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[],
    ), patch(
        "backend.db.engine.disposable_pg_session",
        _disposable_session_cm(fake_session),
    ), patch("cache.get_cache") as mock_cache:
        mock_cache.return_value = MagicMock()
        result = run_closed_trades_rollup_job(
            {"today": "2026-06-06"},
        )
    assert result["status"] == "ok"
    assert result["trades_upserted"] == 0
    assert fake_session.execute.await_count == 0


def test_dry_run_does_not_write(fake_session):
    events = [
        _fill_row(
            str(uuid4()), "e1", "ITC", "BUY", 10, 300.0,
            datetime(2026, 6, 1, tzinfo=timezone.utc), "paper",
        ),
        _fill_row(
            str(uuid4()), "e2", "ITC", "SELL", 10, 310.0,
            datetime(2026, 6, 5, tzinfo=timezone.utc), "paper",
        ),
    ]
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=events,
    ), patch(
        "backend.db.engine.disposable_pg_session",
        _disposable_session_cm(fake_session),
    ):
        result = run_closed_trades_rollup_job(
            {"today": "2026-06-06", "dry_run": True},
        )
    assert result["status"] == "dry_run"
    assert fake_session.execute.await_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/algo/tests/test_closed_trades_rollup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.algo.jobs.closed_trades_rollup'`.

- [ ] **Step 3: Write the implementation**

```python
"""Daily rollup job — reads algo.events (paper + live fills) and
materializes closed trades into algo.closed_trades.

Runs at 16:30 IST Mon-Fri (see scripts/seed_closed_trades_rollup.py),
well after market close (15:30 IST) and after the 15:45 IST budget
reconciliation job so any late postback fills have settled.

Per CLAUDE.md §5.1: reads Iceberg via query_iceberg_table (no
market-hours load — this never runs during trading hours), writes
Postgres via disposable_pg_session (NullPool, per-call).

Re-derives trades from a trailing window every run (default 400
days, matching the OHLCV warmup convention) rather than carrying
forward state, because a position can open on day N and close on
day N+40. The idempotent unique key (buy_event_id, sell_event_id)
on algo.closed_trades makes re-running safe — ON CONFLICT DO
NOTHING skips trades already materialized.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from backend.algo.attribution.trade_pairing import (
    pair_fills_by_strategy_and_ticker,
)

_logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_DAYS = 400
_ROLLUP_MODES = ("paper", "live")


def _ist_today() -> date:
    return (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()


def run_closed_trades_rollup_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sync entrypoint (scheduler / seed / backfill callers).

    Payload keys (all optional):
      - ``window_days``: trailing lookback for the Iceberg scan.
        Default 400. The one-time backfill script (Task 5) passes
        a larger bounded value (3650, ~10 years) — never
        unbounded, per the "no full-table scans" constraint.
      - ``today``: ISO date override (testing). Default IST-today.
      - ``dry_run``: compute + log counts, skip the PG upsert.
    """
    import asyncio

    return asyncio.run(_run(payload or {}))


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.db.engine import disposable_pg_session

    today = (
        date.fromisoformat(payload["today"])
        if payload.get("today")
        else _ist_today()
    )
    window_days = payload.get("window_days", _DEFAULT_WINDOW_DAYS)
    dry_run = bool(payload.get("dry_run", False))

    events = _fetch_fill_events(today, window_days)
    trades = pair_fills_by_strategy_and_ticker(events)
    # Attach user_id/mode back onto each trade — the pairing helper
    # only tracks strategy_id/ticker, so recover them from the
    # source events keyed by buy_event_id.
    meta_by_event_id = {
        ev["event_id"]: (ev.get("user_id"), ev.get("mode"))
        for ev in events
    }
    for t in trades:
        user_id, mode = meta_by_event_id.get(
            t["buy_event_id"], (None, None),
        )
        t["user_id"] = user_id
        t["mode"] = mode
    trades = [t for t in trades if t["user_id"] and t["mode"]]

    _logger.info(
        "closed-trades-rollup: today=%s window_days=%s "
        "events=%d trades=%d dry_run=%s",
        today.isoformat(), window_days, len(events),
        len(trades), dry_run,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "today": today.isoformat(),
            "events_scanned": len(events),
            "trades_computed": len(trades),
        }

    upserted = 0
    if trades:
        async with disposable_pg_session() as session:
            for t in trades:
                result = await session.execute(
                    text(
                        "INSERT INTO algo.closed_trades "
                        "(user_id, strategy_id, mode, ticker, qty, "
                        " avg_price, fill_price, opened_at, "
                        " closed_at, opened_at_ts_ns, "
                        " closed_at_ts_ns, realised_pnl_inr, "
                        " return_pct, exit_reason, dry_run, "
                        " buy_event_id, sell_event_id) "
                        "VALUES (:user_id, :strategy_id, :mode, "
                        " :ticker, :qty, :avg_price, :fill_price, "
                        " :opened_at, :closed_at, "
                        " :opened_at_ts_ns, :closed_at_ts_ns, "
                        " :realised_pnl_inr, :return_pct, "
                        " :exit_reason, :dry_run, :buy_event_id, "
                        " :sell_event_id) "
                        "ON CONFLICT (buy_event_id, sell_event_id) "
                        "DO NOTHING"
                    ),
                    {
                        "user_id": t["user_id"],
                        "strategy_id": t["strategy_id"],
                        "mode": t["mode"],
                        "ticker": t["ticker"],
                        "qty": t["qty"],
                        "avg_price": t["avg_price"],
                        "fill_price": t["fill_price"],
                        "opened_at": t["opened_at"],
                        "closed_at": t["closed_at"],
                        "opened_at_ts_ns": t["opened_at_ts_ns"],
                        "closed_at_ts_ns": t["closed_at_ts_ns"],
                        "realised_pnl_inr": t["realised_pnl_inr"],
                        "return_pct": t["return_pct"],
                        "exit_reason": t["exit_reason"],
                        "dry_run": t["dry_run"],
                        "buy_event_id": t["buy_event_id"],
                        "sell_event_id": t["sell_event_id"],
                    },
                )
                if result.rowcount:
                    upserted += 1
            await session.commit()

    if upserted:
        try:
            from cache import get_cache
            get_cache().invalidate("cache:algo:perf:*")
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "closed-trades-rollup: cache invalidate "
                "failed (non-fatal): %s", exc,
            )

    return {
        "status": "ok",
        "today": today.isoformat(),
        "events_scanned": len(events),
        "trades_computed": len(trades),
        "trades_upserted": upserted,
    }


def _fetch_fill_events(
    today: date, window_days: int,
) -> list[dict[str, Any]]:
    """Pull paper + live order_filled(_live) events across every
    user for the trailing window. Column-projected, date-filtered
    — never a full-table scan, including the backfill caller
    (Task 5), which passes a large but bounded ``window_days``
    rather than an unbounded scan."""
    from backend.db.duckdb_engine import query_iceberg_table

    modes_clause = " OR ".join(
        "mode = ?" for _ in _ROLLUP_MODES
    )
    start = today - timedelta(days=window_days)
    sql = (
        "SELECT event_id, user_id, strategy_id, mode, type, "
        "       payload_json, ts_ns "
        "FROM events "
        f"WHERE ({modes_clause}) "
        "  AND type IN ('order_filled', 'order_filled_live') "
        "  AND ts_date >= ? AND ts_date <= ? "
        "ORDER BY ts_ns"
    )
    params = [*_ROLLUP_MODES, start.isoformat(), today.isoformat()]

    try:
        return query_iceberg_table("algo.events", sql, params)
    except Exception:  # noqa: BLE001
        _logger.exception(
            "closed-trades-rollup: events query failed",
        )
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/algo/tests/test_closed_trades_rollup.py -v`
Expected: 3 passed.

- [ ] **Step 5: Register the job in executor.py**

Add after the `_job_algo_budget_reservations_retention` block (`backend/jobs/executor.py`, around line 3811):

```python
@register_job("algo_closed_trades_rollup")
def _job_algo_closed_trades_rollup(
    scope: str | None = None,
    run_id: str | None = None,
    repo=None,
    cancel_event=None,
    force: bool = False,
    payload: dict | None = None,
) -> dict:
    """Daily rollup of paper/live closed trades into
    algo.closed_trades, powering the Strategy Performance page.

    Reads algo.events (trailing 400-day window), FIFO-pairs BUY/
    SELL fills per (user, strategy, ticker), idempotently upserts.
    Runs 16:30 IST Mon-Fri, after market close and budget
    reconciliation.
    """
    from backend.algo.jobs.closed_trades_rollup import (
        run_closed_trades_rollup_job,
    )

    return run_closed_trades_rollup_job(payload or {})
```

- [ ] **Step 6: Run the full algo test suite to check for regressions**

Run: `python -m pytest backend/algo/tests/ -v -k "executor or closed_trades or trade_pairing"`
Expected: all pass, no import errors from the executor.py change.

- [ ] **Step 7: Commit**

```bash
git add backend/algo/jobs/closed_trades_rollup.py backend/jobs/executor.py backend/algo/tests/test_closed_trades_rollup.py
git commit -m "$(cat <<'EOF'
feat(algo): add daily algo_closed_trades_rollup job

Reads algo.events (paper+live fills, trailing 400-day window) off
market hours, FIFO-pairs BUY/SELL fills into closed trades, and
idempotently upserts into the new algo.closed_trades table.
Registered as a scheduled job type; cron row seeded separately.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 4: Seed script for the scheduled_jobs row

**Files:**
- Create: `scripts/seed_closed_trades_rollup.py`

**Interfaces:**
- Consumes: `scheduled_jobs` table (existing PG schema, `job_type = "algo_closed_trades_rollup"` from Task 3).
- Produces: nothing consumed by later tasks — this is an operator-run script.

- [ ] **Step 1: Write the script**

```python
"""Seed the scheduled_jobs row for the daily
``algo_closed_trades_rollup`` job.

Idempotent — uses ON CONFLICT (name) DO UPDATE so re-running
adjusts the schedule but doesn't duplicate.

Usage::

    docker compose exec backend python \
        scripts/seed_closed_trades_rollup.py
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import text

from db.engine import get_session_factory

_logger = logging.getLogger(__name__)

# Stable UUID namespace so re-runs target the same job_id row.
_NS = uuid.UUID("f3d4b5c6-e7f8-4a9b-b123-4567890abcde")

_JOB = {
    "name": "Algo Closed Trades Rollup - Daily",
    "job_type": "algo_closed_trades_rollup",
    # 16:30 IST, well after market close (15:30 IST) and the
    # 15:45 IST budget reconciliation job, so postback fills have
    # settled. Mon-Fri only (no trading on weekends).
    "cron_days": "mon,tue,wed,thu,fri",
    "cron_time": "16:30",
    "cron_dates": None,
    "scope": None,
}


async def seed() -> None:
    factory = get_session_factory()
    async with factory() as session:
        jid = str(uuid.uuid5(_NS, _JOB["name"]))
        await session.execute(
            text(
                "INSERT INTO scheduled_jobs "
                "(job_id, name, job_type, cron_days, cron_time, "
                " cron_dates, scope, enabled, force) "
                "VALUES (:jid, :name, :jt, :cd, :ct, :cdates, "
                "        :scope, TRUE, FALSE) "
                "ON CONFLICT (name) DO UPDATE SET "
                "  job_type = EXCLUDED.job_type, "
                "  cron_days = EXCLUDED.cron_days, "
                "  cron_time = EXCLUDED.cron_time, "
                "  cron_dates = EXCLUDED.cron_dates, "
                "  updated_at = NOW()"
            ),
            {
                "jid": jid,
                "name": _JOB["name"],
                "jt": _JOB["job_type"],
                "cd": _JOB["cron_days"],
                "ct": _JOB["cron_time"],
                "cdates": _JOB["cron_dates"],
                "scope": _JOB["scope"],
            },
        )
        await session.commit()
        _logger.info(
            "seeded %s -> %s (job_id=%s)",
            _JOB["name"], _JOB["job_type"], jid,
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the dev database**

Run: `docker compose exec backend python scripts/seed_closed_trades_rollup.py`
Expected: log line `seeded Algo Closed Trades Rollup - Daily -> algo_closed_trades_rollup (job_id=...)`.

- [ ] **Step 3: Verify the row (and restart backend per CLAUDE.md §6.2)**

Run: `docker compose exec postgres psql -U postgres -d ai_agent_ui -c "SELECT name, job_type, cron_days, cron_time, enabled FROM scheduled_jobs WHERE job_type = 'algo_closed_trades_rollup'"`
Expected: one row, `enabled = true`.

Direct PG writes to `scheduled_jobs` need a backend restart — `scheduler_service.list_pipelines()` reads in-memory state loaded at startup. **Ask the user before restarting** (CLAUDE.md: live-trading-sensitive restart rule) — do not restart automatically as part of this task; flag it as a manual follow-up instead.

- [ ] **Step 4: Commit**

```bash
git add scripts/seed_closed_trades_rollup.py
git commit -m "$(cat <<'EOF'
feat(algo): seed scheduled_jobs row for closed-trades rollup

Daily 16:30 IST Mon-Fri, after market close and budget
reconciliation.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 5: One-time backfill script

**Files:**
- Create: `scripts/backfill_closed_trades.py`

**Interfaces:**
- Consumes: `run_closed_trades_rollup_job` from Task 3 (same function, `window_days=3650`).

- [ ] **Step 1: Write the script**

```python
"""One-time backfill: populate algo.closed_trades with every
historical closed trade before the recurring daily rollup job
takes over.

Reuses the exact same pairing + idempotent-upsert code as the
daily job (backend/algo/jobs/closed_trades_rollup.py), just with
a much larger (but still bounded — no full-table scan) lookback
window: 3650 days (~10 years), comfortably covering this
platform's full trading history. Safe to re-run — the unique
(buy_event_id, sell_event_id) constraint means re-running finds
0 new rows on a second pass.

Usage::

    docker compose exec backend python \
        scripts/backfill_closed_trades.py
"""
from __future__ import annotations

import logging

from backend.algo.jobs.closed_trades_rollup import (
    run_closed_trades_rollup_job,
)

_logger = logging.getLogger(__name__)

_BACKFILL_WINDOW_DAYS = 3650


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    result = run_closed_trades_rollup_job(
        {"window_days": _BACKFILL_WINDOW_DAYS},
    )
    _logger.info("backfill result: %s", result)
    print(result)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the dev database**

Run: `docker compose exec backend python scripts/backfill_closed_trades.py`
Expected: a dict printed with `"status": "ok"` and `trades_upserted` equal to the total historical paper+live closed trades across all users.

- [ ] **Step 3: Verify idempotency by re-running**

Run: `docker compose exec backend python scripts/backfill_closed_trades.py` (again)
Expected: `"trades_upserted": 0` (all rows already exist — `ON CONFLICT DO NOTHING` skipped every one).

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_closed_trades.py
git commit -m "$(cat <<'EOF'
feat(algo): add one-time backfill script for algo.closed_trades

Populates full trade history before the daily rollup job takes
over. Reuses the daily job's core function with a 10-year bounded
window (no full-table scan); idempotent via the same unique
constraint.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 6: Backend endpoint — `GET /v1/algo/performance/summary`

**Files:**
- Modify: `backend/algo/routes/performance.py`
- Test: `backend/algo/tests/test_performance_routes.py` (add new test functions; existing 3 tests must stay green)

**Interfaces:**
- Produces: `GET /v1/algo/performance/summary?mode=&strategy_id=&lookback=&start=&end=` → JSON `{mode, window: {start, end}, strategies: [...], trades: [...] | []}`. Task 7 (frontend hook) depends on this exact response shape.
- Consumes: `algo.runs` (existing), `algo.closed_trades` (Task 1), `algo.strategies` (existing).

- [ ] **Step 1: Write the failing tests** (appended to the existing test file)

```python
# --- appended to backend/algo/tests/test_performance_routes.py ---


def test_summary_requires_valid_mode(app):
    a, _ = app
    client = TestClient(a)
    r = client.get("/v1/algo/performance/summary?mode=bogus")
    assert r.status_code == 422


def test_summary_backtest_mode_aggregates_trade_list(app, monkeypatch):
    a, fake_session = app
    sid = uuid4()
    row = {
        "strategy_id": sid,
        "strategy_name": "RSI(2) v5",
        "summary_json": {
            "max_drawdown_pct": "4.2",
            "trade_list": [
                {
                    "ticker": "ITC", "realised_pnl_inr": 500,
                    "closed_at": "2026-06-10",
                },
                {
                    "ticker": "TCS", "realised_pnl_inr": -200,
                    "closed_at": "2026-06-12",
                },
            ],
        },
    }

    class _Res:
        def mappings(self):
            return self

        def all(self):
            return [row]
    fake_session.execute = AsyncMock(return_value=_Res())

    import backend.algo.routes.performance as perf_mod
    monkeypatch.setattr(
        perf_mod, "get_cache",
        lambda: MagicMock(get=lambda k: None, set=lambda *a, **k: None),
    )

    client = TestClient(a)
    r = client.get(
        f"/v1/algo/performance/summary?mode=backtest"
        f"&strategy_id={sid}&lookback=all",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "backtest"
    assert len(body["strategies"]) == 1
    s = body["strategies"][0]
    assert s["total_trades"] == 2
    assert s["wins"] == 1
    assert s["losses"] == 1
    assert s["win_rate_pct"] == 50.0
    assert s["biggest_win"]["ticker"] == "ITC"
    assert s["biggest_loss"]["ticker"] == "TCS"
    assert s["max_drawdown_pct"] == 4.2
    assert len(body["trades"]) == 2


def test_summary_live_mode_reads_closed_trades(app, monkeypatch):
    a, fake_session = app
    sid = uuid4()
    row = {
        "strategy_id": sid,
        "strategy_name": "RSI(2) v5",
        "ticker": "SHAILY",
        "realised_pnl_inr": -890.0,
        "closed_at": date(2026, 6, 25),
        "qty": 3,
        "avg_price": 1200.0,
        "fill_price": 903.33,
        "opened_at": date(2026, 6, 20),
        "return_pct": -24.7,
        "exit_reason": "stop_loss",
        "opened_at_ts_ns": None,
        "closed_at_ts_ns": None,
    }

    class _Res:
        def mappings(self):
            return self

        def all(self):
            return [row]
    fake_session.execute = AsyncMock(return_value=_Res())

    import backend.algo.routes.performance as perf_mod
    monkeypatch.setattr(
        perf_mod, "get_cache",
        lambda: MagicMock(get=lambda k: None, set=lambda *a, **k: None),
    )

    client = TestClient(a)
    r = client.get(
        f"/v1/algo/performance/summary?mode=live"
        f"&strategy_id={sid}&lookback=30d",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    s = body["strategies"][0]
    assert s["total_trades"] == 1
    assert s["losses"] == 1
    assert s["max_drawdown_pct"] is None
    assert body["trades"][0]["ticker"] == "SHAILY"
    assert body["trades"][0]["holding_days"] == 5


def test_summary_no_strategy_id_omits_trades_list(app, monkeypatch):
    a, fake_session = app

    class _Res:
        def mappings(self):
            return self

        def all(self):
            return []
    fake_session.execute = AsyncMock(return_value=_Res())

    import backend.algo.routes.performance as perf_mod
    monkeypatch.setattr(
        perf_mod, "get_cache",
        lambda: MagicMock(get=lambda k: None, set=lambda *a, **k: None),
    )

    client = TestClient(a)
    r = client.get("/v1/algo/performance/summary?mode=live")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trades"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/algo/tests/test_performance_routes.py -v`
Expected: FAIL — `/summary` route doesn't exist yet (404s).

- [ ] **Step 3: Write the implementation** — replace the full content of `backend/algo/routes/performance.py`

```python
"""GET /v1/algo/performance/runs — strategy-vs-strategy aggregate
(legacy, kept for backward compatibility with any direct callers).

GET /v1/algo/performance/summary — mode-aware, strategy-scoped,
trade-level performance. Backtest/Walk-forward read
algo.runs.summary_json.trade_list (existing, already has a
capital-based equity curve + max_drawdown_pct). Paper/Live read
the new algo.closed_trades table (backend/algo/jobs/
closed_trades_rollup.py), which has no capital baseline, so
max_drawdown_pct is null for those two modes — the page instead
surfaces biggest_win / biggest_loss, which the user confirmed is
the metric they actually want ("which trade we book for max
loss").
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from cache import TTL_STABLE, get_cache

_logger = logging.getLogger(__name__)

_BACKTEST_FAMILY = ("backtest", "walkforward")
_TRADE_FAMILY = ("paper", "live")
_LOOKBACK_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def _ist_today() -> date:
    return (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()


def _resolve_window(
    lookback: str | None, start: date | None, end: date | None,
) -> tuple[date, date]:
    """Resolve the effective (start, end) date window.

    ``lookback`` wins if both a preset and a custom range are
    somehow present (client bug) — logged, not raised, so a
    malformed request doesn't break the page.
    """
    today = _ist_today()
    if lookback and (start or end):
        _logger.warning(
            "performance/summary: both lookback=%s and "
            "start/end given — lookback wins", lookback,
        )
        start = end = None
    if lookback:
        if lookback == "all":
            return date(2000, 1, 1), today
        days = _LOOKBACK_DAYS[lookback]
        return today - timedelta(days=days), today
    if start and end:
        return start, end
    # Default when nothing is specified.
    return today - timedelta(days=30), today


def _aggregate_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute win/loss/biggest-win/biggest-loss/profit-factor from
    a list of trade dicts. Shared across the backtest/walkforward
    (from summary_json.trade_list) and paper/live (from
    algo.closed_trades) sources — both are coerced to a common
    shape with at least ``ticker``, ``realised_pnl_inr``,
    ``closed_at`` before reaching this function."""
    n = len(trades)
    if n == 0:
        return {
            "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate_pct": None, "total_pnl_inr": 0.0,
            "biggest_win": None, "biggest_loss": None,
            "avg_win_inr": None, "avg_loss_inr": None,
            "profit_factor": None,
        }
    pnls = [float(t["realised_pnl_inr"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls)
    best_idx = max(range(n), key=lambda i: pnls[i])
    worst_idx = min(range(n), key=lambda i: pnls[i])
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    def _trade_ref(idx: int) -> dict[str, Any]:
        t = trades[idx]
        return {
            "ticker": t["ticker"],
            "pnl_inr": round(pnls[idx], 2),
            "closed_at": str(t["closed_at"]),
        }

    return {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / n * 100, 2),
        "total_pnl_inr": round(total_pnl, 2),
        "biggest_win": _trade_ref(best_idx) if pnls[best_idx] > 0 else None,
        "biggest_loss": (
            _trade_ref(worst_idx) if pnls[worst_idx] < 0 else None
        ),
        "avg_win_inr": (
            round(gross_win / len(wins), 2) if wins else None
        ),
        "avg_loss_inr": (
            round(sum(losses) / len(losses), 2) if losses else None
        ),
        "profit_factor": (
            round(gross_win / gross_loss, 2)
            if gross_loss > 0 else None
        ),
    }


def _holding_days(opened_at: Any, closed_at: Any) -> int:
    def _to_date(v: Any) -> date:
        if isinstance(v, date):
            return v
        return date.fromisoformat(str(v))
    return (_to_date(closed_at) - _to_date(opened_at)).days


def create_performance_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/performance", tags=["algo-trading"],
    )

    @router.get("/runs")
    async def list_runs(
        limit: int = Query(50, ge=1, le=200),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """Recent algo.runs rows for the caller (any mode), newest
        first. Legacy endpoint — kept for backward compatibility;
        the rebuilt Performance page uses /summary instead."""
        factory = _get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT r.id, r.strategy_id, "
                    "       s.name AS strategy_name, "
                    "       r.mode, r.status, "
                    "       r.period_start, r.period_end, "
                    "       r.started_at, r.completed_at, "
                    "       r.summary_json "
                    "FROM algo.runs r "
                    "LEFT JOIN algo.strategies s "
                    "  ON s.id = r.strategy_id "
                    "WHERE r.user_id = :uid "
                    "ORDER BY r.started_at DESC "
                    "LIMIT :lim"
                ),
                {"uid": UUID(user.user_id), "lim": limit},
            )
            rows = result.mappings().all()

        out: list[dict[str, Any]] = []
        for r in rows:
            sj: dict | None = r["summary_json"]
            out.append({
                "run_id": str(r["id"]),
                "strategy_id": str(r["strategy_id"]),
                "strategy_name": r["strategy_name"] or "Unknown",
                "mode": r["mode"],
                "status": r["status"],
                "period_start": (
                    r["period_start"].isoformat()
                    if r["period_start"] else None
                ),
                "period_end": (
                    r["period_end"].isoformat()
                    if r["period_end"] else None
                ),
                "started_at": r["started_at"].isoformat(),
                "completed_at": (
                    r["completed_at"].isoformat()
                    if r["completed_at"] else None
                ),
                "total_pnl_inr": (
                    str(Decimal(str(sj["total_pnl_inr"])))
                    if sj and "total_pnl_inr" in sj else None
                ),
                "total_pnl_pct": (
                    str(Decimal(str(sj["total_pnl_pct"])))
                    if sj and "total_pnl_pct" in sj else None
                ),
                "total_trades": (
                    int(sj["total_trades"])
                    if sj and "total_trades" in sj else None
                ),
                "win_rate_pct": (
                    str(Decimal(str(sj["win_rate_pct"])))
                    if sj and "win_rate_pct" in sj else None
                ),
                "max_drawdown_pct": (
                    str(Decimal(str(sj["max_drawdown_pct"])))
                    if sj and "max_drawdown_pct" in sj else None
                ),
            })
        return out

    @router.get("/summary")
    async def get_summary(
        mode: str = Query(
            ..., pattern="^(backtest|walkforward|paper|live)$",
        ),
        strategy_id: UUID | None = Query(None),
        lookback: str | None = Query(
            None, pattern="^(7d|30d|90d|all)$",
        ),
        start: date | None = Query(None),
        end: date | None = Query(None),
        user: UserContext = Depends(pro_or_superuser),
    ) -> JSONResponse:
        user_id = UUID(user.user_id)
        win_start, win_end = _resolve_window(lookback, start, end)

        cache = get_cache()
        cache_key = (
            f"cache:algo:perf:{user_id}:{mode}:"
            f"{strategy_id or 'all'}:"
            f"{lookback or f'{win_start}_{win_end}'}"
        )
        hit = cache.get(cache_key)
        if hit is not None:
            import json
            return JSONResponse(content=json.loads(hit))

        if mode in _BACKTEST_FAMILY:
            per_strategy_trades, names = await _load_backtest_trades(
                user_id, mode, strategy_id, win_start, win_end,
            )
        else:
            per_strategy_trades, names = await _load_closed_trades(
                user_id, mode, strategy_id, win_start, win_end,
            )

        strategies_out: list[dict[str, Any]] = []
        for sid, trades in per_strategy_trades.items():
            agg = _aggregate_trades(trades)
            max_dd = None
            if mode in _BACKTEST_FAMILY:
                dds = [
                    t["_max_drawdown_pct"] for t in trades
                    if t.get("_max_drawdown_pct") is not None
                ]
                max_dd = max(dds) if dds else None
            strategies_out.append({
                "strategy_id": sid,
                "strategy_name": names.get(sid, "Unknown"),
                "max_drawdown_pct": max_dd,
                **agg,
            })
        strategies_out.sort(
            key=lambda s: s["total_pnl_inr"], reverse=True,
        )

        trades_out: list[dict[str, Any]] = []
        if strategy_id is not None:
            sid_s = str(strategy_id)
            for t in per_strategy_trades.get(sid_s, []):
                trades_out.append({
                    "ticker": t["ticker"],
                    "qty": t.get("qty"),
                    "avg_price": t.get("avg_price"),
                    "fill_price": t.get("fill_price"),
                    "opened_at": str(t["opened_at"]),
                    "closed_at": str(t["closed_at"]),
                    "holding_days": _holding_days(
                        t["opened_at"], t["closed_at"],
                    ),
                    "realised_pnl_inr": t["realised_pnl_inr"],
                    "return_pct": t.get("return_pct"),
                    "exit_reason": t.get("exit_reason", "signal"),
                    "opened_at_ts_ns": t.get("opened_at_ts_ns"),
                    "closed_at_ts_ns": t.get("closed_at_ts_ns"),
                })
            trades_out.sort(
                key=lambda t: t["closed_at"], reverse=True,
            )

        body = {
            "mode": mode,
            "window": {
                "start": win_start.isoformat(),
                "end": win_end.isoformat(),
            },
            "strategies": strategies_out,
            "trades": trades_out,
        }
        import json
        cache.set(cache_key, json.dumps(body), ttl=TTL_STABLE)
        return JSONResponse(content=body)

    return router


async def _load_backtest_trades(
    user_id: UUID, mode: str, strategy_id: UUID | None,
    win_start: date, win_end: date,
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    from backend.db.engine import get_session_factory

    clauses = [
        "r.user_id = :uid", "r.mode = :mode",
        "r.status = 'completed'",
        "r.summary_json IS NOT NULL",
        "r.started_at::date >= :ws", "r.started_at::date <= :we",
    ]
    params: dict[str, Any] = {
        "uid": user_id, "mode": mode, "ws": win_start, "we": win_end,
    }
    if strategy_id is not None:
        clauses.append("r.strategy_id = :sid")
        params["sid"] = strategy_id
    where = " AND ".join(clauses)

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT r.strategy_id, s.name AS strategy_name, "
                "       r.summary_json "
                "FROM algo.runs r "
                "LEFT JOIN algo.strategies s ON s.id = r.strategy_id "
                f"WHERE {where}"
            ),
            params,
        )
        rows = result.mappings().all()

    per_strategy: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    for r in rows:
        sid = str(r["strategy_id"])
        names[sid] = r["strategy_name"] or "Unknown"
        sj = r["summary_json"] or {}
        max_dd = sj.get("max_drawdown_pct")
        for trade in sj.get("trade_list", []):
            per_strategy.setdefault(sid, []).append({
                **trade,
                "_max_drawdown_pct": (
                    float(max_dd) if max_dd is not None else None
                ),
            })
    return per_strategy, names


async def _load_closed_trades(
    user_id: UUID, mode: str, strategy_id: UUID | None,
    win_start: date, win_end: date,
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    from backend.db.engine import get_session_factory

    clauses = [
        "ct.user_id = :uid", "ct.mode = :mode",
        "ct.closed_at >= :ws", "ct.closed_at <= :we",
    ]
    params: dict[str, Any] = {
        "uid": user_id, "mode": mode, "ws": win_start, "we": win_end,
    }
    if mode == "live":
        clauses.append("ct.dry_run = false")
    if strategy_id is not None:
        clauses.append("ct.strategy_id = :sid")
        params["sid"] = strategy_id
    where = " AND ".join(clauses)

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT ct.strategy_id, s.name AS strategy_name, "
                "       ct.ticker, ct.qty, ct.avg_price, "
                "       ct.fill_price, ct.opened_at, ct.closed_at, "
                "       ct.opened_at_ts_ns, ct.closed_at_ts_ns, "
                "       ct.realised_pnl_inr, ct.return_pct, "
                "       ct.exit_reason "
                "FROM algo.closed_trades ct "
                "LEFT JOIN algo.strategies s ON s.id = ct.strategy_id "
                f"WHERE {where}"
            ),
            params,
        )
        rows = result.mappings().all()

    per_strategy: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    for r in rows:
        sid = str(r["strategy_id"])
        names[sid] = r["strategy_name"] or "Unknown"
        per_strategy.setdefault(sid, []).append({
            "ticker": r["ticker"],
            "qty": r["qty"],
            "avg_price": (
                float(r["avg_price"])
                if r["avg_price"] is not None else None
            ),
            "fill_price": (
                float(r["fill_price"])
                if r["fill_price"] is not None else None
            ),
            "opened_at": r["opened_at"],
            "closed_at": r["closed_at"],
            "opened_at_ts_ns": r["opened_at_ts_ns"],
            "closed_at_ts_ns": r["closed_at_ts_ns"],
            "realised_pnl_inr": float(r["realised_pnl_inr"]),
            "return_pct": (
                float(r["return_pct"])
                if r["return_pct"] is not None else None
            ),
            "exit_reason": r["exit_reason"],
        })
    return per_strategy, names
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/algo/tests/test_performance_routes.py -v`
Expected: 6 passed (3 original `/runs` tests + 3 new `/summary` tests... adjust count if Step 1 added more than 3 — verify exact count against the test functions written).

- [ ] **Step 5: Run flake8/black/isort on the changed file**

Run: `black backend/algo/routes/performance.py && isort backend/algo/routes/performance.py --profile black && flake8 backend/algo/routes/performance.py`
Expected: no diffs, no violations.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/routes/performance.py backend/algo/tests/test_performance_routes.py
git commit -m "$(cat <<'EOF'
feat(algo): add GET /algo/performance/summary — mode-aware metrics

Backtest/walk-forward read algo.runs.summary_json.trade_list
(existing, has true capital-based drawdown). Paper/live read the
new algo.closed_trades table (no capital baseline -> null
max_drawdown_pct, biggest_win/biggest_loss instead, per user
confirmation that trade-level extremes are what they actually
want measured). Legacy /runs endpoint kept unchanged.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 7: Frontend SWR hook

**Files:**
- Create: `frontend/hooks/useStrategyPerformance.ts`

**Interfaces:**
- Consumes: `GET /v1/algo/performance/summary` (Task 6).
- Produces: `useStrategyPerformance({mode, strategyId, lookback, start, end})` returning `{strategies, trades, window, loading, error}`. Task 9 (PerformanceTab rebuild) depends on this hook's exact return shape and the `PerformanceMode` / `StrategyPerfRow` / `PerfTradeRow` types it exports.

- [ ] **Step 1: Write the hook**

```typescript
"use client";
/**
 * SWR hook for GET /v1/algo/performance/summary.
 *
 * Powers the rebuilt Strategies -> Performance page: mode-aware
 * (backtest/walkforward/paper/live), strategy-scoped, trade-level
 * metrics.
 */

import useSWR from "swr";

import { apiFetch } from "@/lib/apiFetch";
import { API_URL } from "@/lib/config";
import type { TradeRow } from "@/hooks/useBacktestRuns";

export type PerformanceMode =
  | "backtest"
  | "walkforward"
  | "paper"
  | "live";

export type LookbackPreset = "7d" | "30d" | "90d" | "all";

export interface TradeExtreme {
  ticker: string;
  pnl_inr: number;
  closed_at: string;
}

export interface StrategyPerfRow {
  strategy_id: string;
  strategy_name: string;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  total_pnl_inr: number;
  biggest_win: TradeExtreme | null;
  biggest_loss: TradeExtreme | null;
  avg_win_inr: number | null;
  avg_loss_inr: number | null;
  profit_factor: number | null;
  max_drawdown_pct: number | null;
}

export interface PerformanceSummaryResponse {
  mode: PerformanceMode;
  window: { start: string; end: string };
  strategies: StrategyPerfRow[];
  trades: TradeRow[];
}

export interface UseStrategyPerformanceParams {
  mode: PerformanceMode;
  strategyId?: string | null;
  lookback?: LookbackPreset | null;
  start?: string | null;
  end?: string | null;
}

async function fetcher(url: string): Promise<PerformanceSummaryResponse> {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useStrategyPerformance(
  params: UseStrategyPerformanceParams,
) {
  const { mode, strategyId, lookback, start, end } = params;
  const qs = new URLSearchParams({ mode });
  if (strategyId) qs.set("strategy_id", strategyId);
  if (lookback && !start && !end) qs.set("lookback", lookback);
  if (start && end) {
    qs.set("start", start);
    qs.set("end", end);
  }
  const key = `${API_URL}/algo/performance/summary?${qs.toString()}`;

  const { data, error, isLoading } = useSWR<PerformanceSummaryResponse>(
    key,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );

  return {
    strategies: data?.strategies ?? [],
    trades: data?.trades ?? [],
    window: data?.window ?? null,
    loading: isLoading,
    error: error
      ? error instanceof Error
        ? error.message
        : "Failed to load performance data"
      : null,
  };
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors from this file.

- [ ] **Step 3: Commit**

```bash
git add frontend/hooks/useStrategyPerformance.ts
git commit -m "$(cat <<'EOF'
feat(algo): add useStrategyPerformance SWR hook

Wraps GET /algo/performance/summary; reuses the existing
TradeRow type from useBacktestRuns so the trade table component
is shared across backtest and paper/live.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 8: Generalize the trade table component

**Files:**
- Create: `frontend/components/algo-trading/TradeLogTable.tsx` (content = current `BacktestTradeTable.tsx` + two new optional props)
- Delete: `frontend/components/algo-trading/BacktestTradeTable.tsx`
- Modify: `frontend/components/algo-trading/BacktestTab.tsx:13,143` (import + usage rename)

**Interfaces:**
- Produces: `<TradeLogTable rows={TradeRow[]} filenamePrefix?: string; emptyMessage?: string />`. Task 9 depends on this component name and these two new optional props.

- [ ] **Step 1: Create `TradeLogTable.tsx`**

Copy the full current content of `BacktestTradeTable.tsx` (238 lines, already read in full during design) into the new file, with these changes:
1. Rename `interface Props` to add two optional fields and rename the exported function:

```typescript
interface Props {
  rows: TradeRow[];
  filenamePrefix?: string;
  emptyMessage?: string;
}

export function TradeLogTable({
  rows,
  filenamePrefix = "backtest",
  emptyMessage = "No closed trades yet — run a strategy that exits positions.",
}: Props) {
```

2. Update the localStorage column-selection key to be component-agnostic (was `"algo:backtest:trade-cols"`):

```typescript
  const [selected, setSelected, reset] = useColumnSelection(
    "algo:trade-log:trade-cols",
    DEFAULT_COLS,
    VALID_KEYS,
  );
```

3. Update the empty-state block to use `emptyMessage`:

```typescript
  if (rows.length === 0) {
    return (
      <div
        className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
        data-testid="trade-log-table-empty"
      >
        {emptyMessage}
      </div>
    );
  }
```

4. Update the root container `data-testid` and the CSV filename call:

```typescript
    <div
      className="space-y-2"
      data-testid="trade-log-table"
    >
```

```typescript
    downloadCsv(rows, csvCols, `${filenamePrefix}-trades`);
```

5. Keep every other line (`ALL_COLS`, `DEFAULT_COLS`, `VALID_KEYS`, `renderCell`, `formatInr`, `formatPct`, `formatTradeTime`, `ExitReasonBadge`) byte-identical to the original file.

- [ ] **Step 2: Delete the old file**

Run: `git rm frontend/components/algo-trading/BacktestTradeTable.tsx`

- [ ] **Step 3: Update `BacktestTab.tsx`**

Modify line 13:
```typescript
import { TradeLogTable } from "./TradeLogTable";
```

Modify line 143 (was `<BacktestTradeTable rows={run.trade_list} />`):
```typescript
              <TradeLogTable
                rows={run.trade_list}
                filenamePrefix="backtest"
              />
```

- [ ] **Step 4: Run existing frontend tests that exercise BacktestTab / the trade table**

Run: `cd frontend && npx vitest run -t "BacktestTab"`
Expected: pass (no test file specifically named for the old component per Task 6's research — if this command finds nothing, run `npx vitest run` for the full suite instead and confirm no failures reference the renamed component).

- [ ] **Step 5: Typecheck and lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint components/algo-trading/TradeLogTable.tsx components/algo-trading/BacktestTab.tsx --fix`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/algo-trading/TradeLogTable.tsx frontend/components/algo-trading/BacktestTab.tsx
git commit -m "$(cat <<'EOF'
refactor(algo): generalize BacktestTradeTable into TradeLogTable

Renamed with two additive optional props (filenamePrefix,
emptyMessage) so the Strategy Performance page's paper/live
trade drill-down can reuse the exact same component (column
selector + CSV export) instead of a parallel implementation.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 9: Rebuild `PerformanceTab.tsx`

**Files:**
- Modify: `frontend/components/algo-trading/PerformanceTab.tsx` (full rewrite)

**Interfaces:**
- Consumes: `useStrategyPerformance` (Task 7), `useStrategies` + `filterStrategiesByMode` (existing, `frontend/hooks/useStrategies.ts`), `TradeLogTable` (Task 8).

- [ ] **Step 1: Write the new component**

```typescript
"use client";

import { useMemo, useState } from "react";

import { TradeLogTable } from "./TradeLogTable";
import {
  filterStrategiesByMode,
  useStrategies,
  type StrategyMode,
} from "@/hooks/useStrategies";
import {
  useStrategyPerformance,
  type LookbackPreset,
  type PerformanceMode,
  type StrategyPerfRow,
} from "@/hooks/useStrategyPerformance";

const MODE_OPTIONS: { value: PerformanceMode; label: string }[] = [
  { value: "backtest", label: "Backtest" },
  { value: "walkforward", label: "Walk-forward" },
  { value: "paper", label: "Paper" },
  { value: "live", label: "Live" },
];

const LOOKBACK_OPTIONS: { value: LookbackPreset; label: string }[] = [
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "90d", label: "90d" },
  { value: "all", label: "All" },
];

// Strategy-dropdown scoping per mode. Backtest/Walk-forward show
// every strategy (matches the existing convention that those two
// pickers never filter by promotion mode). Paper shows strategies
// currently in paper OR live (a strategy graduated to live still
// keeps its paper history meaningful). Live shows only strategies
// currently promoted to live, per the literal 1a spec.
const STRATEGY_FILTER_FOR_MODE: Record<
  PerformanceMode, StrategyMode[] | null
> = {
  backtest: null,
  walkforward: null,
  paper: ["paper", "live"],
  live: ["live"],
};

function fmtInr(v: number | null): string {
  if (v === null) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(v);
}

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${v.toFixed(2)}%`;
}

export function PerformanceTab() {
  const [mode, setMode] = useState<PerformanceMode>("live");
  const [strategyId, setStrategyId] = useState<string>("all");
  const [lookback, setLookback] = useState<LookbackPreset>("30d");
  const [customRange, setCustomRange] = useState<{
    start: string; end: string;
  } | null>(null);

  const { strategies: allStrategies } = useStrategies();
  const scopedStrategies = useMemo(() => {
    const allowedModes = STRATEGY_FILTER_FOR_MODE[mode];
    return allowedModes
      ? filterStrategiesByMode(allStrategies, allowedModes)
      : allStrategies.filter((s) => s.archived_at == null);
  }, [allStrategies, mode]);

  const {
    strategies: perfRows,
    trades,
    loading,
    error,
  } = useStrategyPerformance({
    mode,
    strategyId: strategyId === "all" ? null : strategyId,
    lookback: customRange ? null : lookback,
    start: customRange?.start ?? null,
    end: customRange?.end ?? null,
  });

  const perTickerRows = useMemo(() => {
    const buckets = new Map<
      string, { ticker: string; trades: number; pnl: number; wins: number }
    >();
    for (const t of trades) {
      const b = buckets.get(t.ticker) ?? {
        ticker: t.ticker, trades: 0, pnl: 0, wins: 0,
      };
      b.trades += 1;
      b.pnl += Number(t.realised_pnl_inr);
      if (Number(t.realised_pnl_inr) > 0) b.wins += 1;
      buckets.set(t.ticker, b);
    }
    return Array.from(buckets.values()).sort((a, b) => a.pnl - b.pnl);
  }, [trades]);

  const handleModeChange = (m: PerformanceMode) => {
    setMode(m);
    setStrategyId("all");
  };

  return (
    <div className="space-y-4" data-testid="performance-tab">
      <div>
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          Performance
        </h2>
        <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
          Trade-level win rate, biggest win/loss, and profit
          factor per strategy.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div
          className="inline-flex rounded-md border border-slate-200 dark:border-slate-700 overflow-hidden"
          data-testid="performance-mode-pills"
        >
          {MODE_OPTIONS.map((m) => (
            <button
              key={m.value}
              type="button"
              data-testid={`performance-mode-${m.value}`}
              onClick={() => handleModeChange(m.value)}
              className={`px-3 py-1.5 text-xs font-medium ${
                mode === m.value
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "bg-white text-slate-600 dark:bg-slate-900 dark:text-slate-300"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>

        <select
          data-testid="performance-strategy-select"
          value={strategyId}
          onChange={(e) => setStrategyId(e.target.value)}
          className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs text-slate-700 dark:text-slate-300"
        >
          <option value="all">All strategies</option>
          {scopedStrategies.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>

        <div
          className="inline-flex rounded-md border border-slate-200 dark:border-slate-700 overflow-hidden"
          data-testid="performance-lookback-pills"
        >
          {LOOKBACK_OPTIONS.map((l) => (
            <button
              key={l.value}
              type="button"
              data-testid={`performance-lookback-${l.value}`}
              onClick={() => {
                setLookback(l.value);
                setCustomRange(null);
              }}
              className={`px-3 py-1.5 text-xs font-medium ${
                !customRange && lookback === l.value
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "bg-white text-slate-600 dark:bg-slate-900 dark:text-slate-300"
              }`}
            >
              {l.label}
            </button>
          ))}
          <button
            type="button"
            data-testid="performance-lookback-custom"
            onClick={() =>
              setCustomRange((c) => c ?? { start: "", end: "" })
            }
            className={`px-3 py-1.5 text-xs font-medium ${
              customRange
                ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                : "bg-white text-slate-600 dark:bg-slate-900 dark:text-slate-300"
            }`}
          >
            Custom
          </button>
        </div>

        {customRange && (
          <div className="flex items-center gap-2">
            <input
              type="date"
              data-testid="performance-range-start"
              value={customRange.start}
              onChange={(e) =>
                setCustomRange((c) => ({
                  start: e.target.value, end: c?.end ?? "",
                }))
              }
              className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs"
            />
            <span className="text-xs text-slate-500">to</span>
            <input
              type="date"
              data-testid="performance-range-end"
              value={customRange.end}
              onChange={(e) =>
                setCustomRange((c) => ({
                  start: c?.start ?? "", end: e.target.value,
                }))
              }
              className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs"
            />
          </div>
        )}
      </div>

      {error && (
        <div
          className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700"
          data-testid="performance-error"
        >
          {error}
        </div>
      )}

      {!error && !loading && perfRows.length === 0 && (
        <div
          className="rounded-md border border-slate-200 dark:border-slate-700 p-4 text-sm text-slate-500"
          data-testid="performance-empty"
        >
          No closed trades in this window for the selected mode.
        </div>
      )}

      {perfRows.length > 0 && (
        <StrategyComparisonTable rows={perfRows} />
      )}

      {strategyId !== "all" && (
        <>
          <TradeLogTable
            rows={trades}
            filenamePrefix={mode}
            emptyMessage="No closed trades in this window."
          />
          {perTickerRows.length > 0 && (
            <PerTickerBreakdown rows={perTickerRows} />
          )}
        </>
      )}
    </div>
  );
}

function StrategyComparisonTable({ rows }: { rows: StrategyPerfRow[] }) {
  return (
    <div
      className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700"
      data-testid="performance-strategy-comparison-table"
    >
      <table className="min-w-full text-sm">
        <thead className="bg-slate-50 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300">Strategy</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Trades</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Win rate</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Total PnL</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Biggest win</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Biggest loss</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Profit factor</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Max DD%</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.strategy_id}
              data-testid={`performance-strategy-row-${r.strategy_id}`}
              className="border-t border-slate-200 dark:border-slate-700"
            >
              <td className="px-3 py-1.5 font-medium text-slate-900 dark:text-slate-100">{r.strategy_name}</td>
              <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{r.total_trades}</td>
              <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{fmtPct(r.win_rate_pct)}</td>
              <td className={`px-3 py-1.5 text-right font-medium ${r.total_pnl_inr >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>{fmtInr(r.total_pnl_inr)}</td>
              <td className="px-3 py-1.5 text-right text-emerald-600 dark:text-emerald-400">{r.biggest_win ? `${r.biggest_win.ticker} ${fmtInr(r.biggest_win.pnl_inr)}` : "—"}</td>
              <td className="px-3 py-1.5 text-right text-rose-600 dark:text-rose-400">{r.biggest_loss ? `${r.biggest_loss.ticker} ${fmtInr(r.biggest_loss.pnl_inr)}` : "—"}</td>
              <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{r.profit_factor ?? "—"}</td>
              <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{fmtPct(r.max_drawdown_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PerTickerBreakdown({
  rows,
}: {
  rows: { ticker: string; trades: number; pnl: number; wins: number }[];
}) {
  return (
    <div data-testid="performance-per-ticker-breakdown" className="space-y-1.5">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
        Per-ticker breakdown
      </h3>
      <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-700">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 dark:bg-slate-800">
            <tr>
              <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300">Ticker</th>
              <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Trades</th>
              <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">Win rate</th>
              <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-300">PnL</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ticker} className="border-t border-slate-200 dark:border-slate-700">
                <td className="px-3 py-1.5 font-medium text-slate-900 dark:text-slate-100">{r.ticker}</td>
                <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{r.trades}</td>
                <td className="px-3 py-1.5 text-right text-slate-700 dark:text-slate-300">{fmtPct((r.wins / r.trades) * 100)}</td>
                <td className={`px-3 py-1.5 text-right font-medium ${r.pnl >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>{fmtInr(r.pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Lint**

Run: `cd frontend && npx eslint components/algo-trading/PerformanceTab.tsx --fix`
Expected: no violations.

- [ ] **Step 4: Manual smoke test**

Run: `docker compose up -d frontend backend` (if not already running), navigate to Algo Trading → Strategies → Performance tab in the browser. Verify: Mode defaults to Live, Strategy defaults to All, Lookback defaults to 30d; switching Mode to Paper changes the strategy dropdown options; picking a specific strategy reveals the trade table + per-ticker breakdown; picking "Custom" reveals the two date inputs.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/algo-trading/PerformanceTab.tsx
git commit -m "$(cat <<'EOF'
feat(algo): rebuild Strategy Performance tab with mode filters

Mode pills (default Live) + strategy dropdown (scoped per mode
via the existing filterStrategiesByMode helper, default All) +
lookback presets with a custom date-range option. Strategy
comparison table always shown; trade drill-down (TradeLogTable)
and per-ticker breakdown appear once a single strategy is
selected.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 10: Frontend tests for the rebuilt Performance tab

**Files:**
- Create: `frontend/components/algo-trading/__tests__/PerformanceTab.test.tsx`

**Interfaces:**
- Consumes: `PerformanceTab` (Task 9), mocks `swr`, `@/hooks/useStrategies`, `@/lib/apiFetch`, `@/lib/config` (mirroring `LiveActiveRunsPanel.test.tsx`'s established mocking convention).

- [ ] **Step 1: Write the failing tests**

```typescript
import {
  afterEach, describe, expect, it, vi,
} from "vitest";
import {
  cleanup, fireEvent, render, screen,
} from "@testing-library/react";

const swrData: Record<string, unknown> = {
  strategies: [
    { id: "s-live", name: "RSI(2) v5", mode: "live", archived_at: null },
    { id: "s-paper", name: "MACD Cross", mode: "paper", archived_at: null },
    { id: "s-draft", name: "Draft Idea", mode: "draft", archived_at: null },
  ],
  summary: {
    mode: "live",
    window: { start: "2026-06-01", end: "2026-07-01" },
    strategies: [
      {
        strategy_id: "s-live", strategy_name: "RSI(2) v5",
        total_trades: 6, wins: 4, losses: 2, win_rate_pct: 66.7,
        total_pnl_inr: 4210.5,
        biggest_win: { ticker: "ITC", pnl_inr: 2100, closed_at: "2026-06-30" },
        biggest_loss: { ticker: "SHAILY", pnl_inr: -890, closed_at: "2026-06-25" },
        avg_win_inr: 1350.2, avg_loss_inr: -610.4, profit_factor: 2.21,
        max_drawdown_pct: null,
      },
    ],
    trades: [],
  },
};

vi.mock("swr", () => ({
  default: (key: string) => {
    if (key?.includes("/algo/performance/summary")) {
      return { data: swrData["summary"], error: null, isLoading: false };
    }
    return { data: null, error: null, isLoading: false };
  },
  mutate: vi.fn(),
}));

vi.mock("@/hooks/useStrategies", async () => {
  const actual = await vi.importActual<
    typeof import("@/hooks/useStrategies")
  >("@/hooks/useStrategies");
  return {
    ...actual,
    useStrategies: () => ({
      strategies: swrData["strategies"],
      loading: false,
      error: null,
    }),
  };
});

vi.mock("@/lib/apiFetch", () => ({
  apiFetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }),
}));
vi.mock("@/lib/config", () => ({ API_URL: "http://test/api" }));

import { PerformanceTab } from "../PerformanceTab";

afterEach(() => cleanup());

describe("PerformanceTab", () => {
  it("defaults to Live mode and All strategies", () => {
    render(<PerformanceTab />);
    expect(
      screen.getByTestId("performance-mode-live"),
    ).toHaveClass("bg-slate-900");
    expect(
      (screen.getByTestId("performance-strategy-select") as HTMLSelectElement)
        .value,
    ).toBe("all");
  });

  it("scopes the strategy dropdown to live-mode strategies only when Live is selected", () => {
    render(<PerformanceTab />);
    const select = screen.getByTestId(
      "performance-strategy-select",
    ) as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toContain("s-live");
    expect(optionValues).not.toContain("s-paper");
    expect(optionValues).not.toContain("s-draft");
  });

  it("scopes the strategy dropdown to paper+live strategies when Paper is selected", () => {
    render(<PerformanceTab />);
    fireEvent.click(screen.getByTestId("performance-mode-paper"));
    const select = screen.getByTestId(
      "performance-strategy-select",
    ) as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toContain("s-live");
    expect(optionValues).toContain("s-paper");
    expect(optionValues).not.toContain("s-draft");
  });

  it("renders the strategy comparison table from the summary response", () => {
    render(<PerformanceTab />);
    expect(
      screen.getByTestId("performance-strategy-row-s-live"),
    ).toBeDefined();
    expect(screen.getByText("RSI(2) v5")).toBeDefined();
  });

  it("reveals custom date inputs when Custom is selected", () => {
    render(<PerformanceTab />);
    expect(
      screen.queryByTestId("performance-range-start"),
    ).toBeNull();
    fireEvent.click(screen.getByTestId("performance-lookback-custom"));
    expect(
      screen.getByTestId("performance-range-start"),
    ).toBeDefined();
    expect(
      screen.getByTestId("performance-range-end"),
    ).toBeDefined();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run components/algo-trading/__tests__/PerformanceTab.test.tsx`
Expected: FAIL initially if any test/implementation mismatch surfaces (e.g. class name assertions) — adjust the test's class-name assertions to match Task 9's actual rendered classes if they differ, then re-run.

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd frontend && npx vitest run components/algo-trading/__tests__/PerformanceTab.test.tsx`
Expected: 5 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/algo-trading/__tests__/PerformanceTab.test.tsx
git commit -m "$(cat <<'EOF'
test(algo): add PerformanceTab tests for mode/strategy scoping

Covers default mode/strategy, per-mode strategy-dropdown scoping
(live-only vs paper+live vs unfiltered), strategy comparison
table rendering, and the custom date-range reveal.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

### Task 11: Docs + progress

**Files:**
- Modify: `PROGRESS.md`
- Modify: `docs/` (mkdocs nav — only if the algo-trading docs page documents the Performance tab's endpoints; check first)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Check whether existing docs reference `/algo/performance/runs`**

Run: `grep -rn "algo/performance" docs/ 2>/dev/null`
Expected: either no hits (skip Step 2) or a doc page that needs a one-line addition for `/summary`.

- [ ] **Step 2: Update docs if Step 1 found references**

If a doc page documents `/algo/performance/runs`, add a matching one- or two-line entry for `GET /v1/algo/performance/summary` (mode/strategy_id/lookback/start/end params) immediately below it, following that page's existing format.

- [ ] **Step 3: Add a PROGRESS.md session entry**

Append a dated entry (today's date) under the appropriate section summarizing: new `algo.closed_trades` table + daily rollup job (`algo_closed_trades_rollup`, 16:30 IST Mon-Fri) + backfill script; new `GET /algo/performance/summary` endpoint; rebuilt Performance tab with mode/strategy/lookback filters, strategy comparison table, trade drill-down, per-ticker breakdown. Follow the exact format of the most recent existing entry in the file (read the last entry first to match heading level and bullet style).

- [ ] **Step 4: Run `mkdocs build` if any docs/ files changed**

Run: `mkdocs build` (only if Step 2 touched anything)
Expected: build passes (this also runs automatically as a pre-commit gate).

- [ ] **Step 5: Commit**

```bash
git add PROGRESS.md docs/
git commit -m "$(cat <<'EOF'
docs: update PROGRESS for Strategy Performance mode filters

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- §2.1 mode filter (4 pills, default Live) → Task 9.
- §2.2 strategy filter scoped per mode, default All → Task 9 (`STRATEGY_FILTER_FOR_MODE`), tested in Task 10.
- §2.3 lookback presets + custom range → Task 9, tested in Task 10.
- §2.4 trade-level metrics (win rate, biggest win/loss, avg win/loss, profit factor, mode-conditional max DD%) → Task 6 (`_aggregate_trades`).
- §2.5 trade drill-down + per-ticker breakdown → Task 9.
- §4 architecture (daily job, not live queries; no runtime hot-path changes) → Tasks 1-5; `live/runtime.py` and `paper/runtime.py` are not touched anywhere in this plan.
- §5 data model → Task 1, field names verified against Task 8/9's `TradeRow` consumer.
- §6 backfill → Task 5.
- §7 API contract (mutually exclusive lookback/start-end, cache key, response shape) → Task 6.
- §8 frontend UX (filter bar, comparison table, drill-down, per-ticker) → Task 9.
- §9 testing → Tasks 2, 3, 6, 10.
- §10 rollout sequence → matches the task order 1→2(deviated, see note)→3→4/5→6→7-10.
- User's added requirement (calendar date-range alongside presets) → Task 9 custom-range UI + Task 6 `start`/`end` query params.
- User's added requirement (one-time backfill) → Task 5.

**Placeholder scan:** no TBD/TODO; every code step has complete code; Task 11 Step 2 is conditional documentation work (genuinely unknown until Step 1 runs), not a placeholder — it has explicit instructions for what to do in both branches.

**Type consistency:** `TradeRow` (frontend, `useBacktestRuns.ts`) fields (`ticker, qty, avg_price, fill_price, opened_at, closed_at, holding_days, realised_pnl_inr, return_pct, exit_reason, opened_at_ts_ns, closed_at_ts_ns`) match the `trades_out` dict built in Task 6 Step 3 field-for-field. `StrategyPerfRow` (Task 7) fields match the `strategies_out` dict built in Task 6 Step 3 field-for-field (`biggest_win`/`biggest_loss` → `TradeExtreme{ticker, pnl_inr, closed_at}` matches `_trade_ref()`). `pair_fills_by_strategy_and_ticker` output keys (Task 2) match what Task 3's rollup job reads (`t["opened_at"]`, `t["strategy_id"]`, etc.) and what Task 3's INSERT statement binds. `PerformanceMode` (Task 7, 4 literal values) matches the Task 6 endpoint's `pattern="^(backtest|walkforward|paper|live)$"` and Task 9's `MODE_OPTIONS`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-01-algo-strategy-performance.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

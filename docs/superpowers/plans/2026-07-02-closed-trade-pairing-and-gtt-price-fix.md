# Closed-Trade Pairing + GTT Fill-Price Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two independent bugs found 2026-07-02 in closed-trade
reporting: (1) BUY/SELL fill pairing that ignores quantity, silently
producing wrong qty/price and dropping trades whenever a ticker's
fills aren't a clean 1:1 sequence; (2) GTT-triggered exits recording
an estimated price instead of Kite's true execution price.

**Architecture:** A new shared quantity-aware FIFO matcher
(`fifo_matcher.py`) replaces the index-based pairing loop in both
`trade_pairing.py` and `routes/attribution.py`. `live/runtime.py`'s
GTT-poll fallback (Piece A) gains a Kite order-history lookup for the
true fill price, tagging every GTT-triggered fill with its price
provenance. Both fixes are logic-only — no schema change — followed
by a one-off correction of today's 3 known-estimated GTT prices and
a full re-derive of `algo.closed_trades` from `algo.events`.

**Tech Stack:** Python 3.12, pytest, PyIceberg, DuckDB, Kite Connect SDK.

## Global Constraints

- Line length ≤79 chars (black/isort/flake8), per CLAUDE.md §4.2 #9.
- `X | None` not `Optional[X]` (PEP 604), CLAUDE.md §4.2 #11.
- Caught exceptions in long-running jobs MUST log with
  `exc_info=True`, CLAUDE.md §4.2 #10.
- Iceberg writes MUST propagate errors — never silence, CLAUDE.md
  §4.3 #17.
- Scoped deletes only — `In("event_id", batch)`, never an unscoped
  filter, CLAUDE.md §4.3 #18.
- `invalidate_metadata()` after every Iceberg write, CLAUDE.md §6.4.
- No bare `print()` — `_logger = logging.getLogger(__name__)`,
  CLAUDE.md §4.2 #10.
- `KiteClient` does not wrap `orders()` — use `self._kite._kc`
  (raw KiteConnect client), matching the existing precedent in
  `live/order_timeout.py::_fetch_orders`.

---

### Task 1: Shared quantity-aware FIFO matcher

**Files:**
- Create: `backend/algo/attribution/fifo_matcher.py`
- Test: `backend/algo/tests/test_fifo_matcher.py`

**Interfaces:**
- Produces: `match_fifo(buys: list[dict], sells: list[dict]) ->
  list[dict]`. Each input dict requires keys `event_id` (str),
  `qty` (int), `price` (float), `ts_ns` (int) — inputs need not be
  pre-sorted. Each output dict has keys `buy_event_id`,
  `sell_event_id`, `qty` (int, the quantity this specific lot
  closes), `buy_price` (float), `sell_price` (float), `buy_ts_ns`
  (int), `sell_ts_ns` (int).

- [ ] **Step 1: Write the failing tests**

Create `backend/algo/tests/test_fifo_matcher.py`:

```python
"""Tests for the shared quantity-aware FIFO fill matcher used by
both trade_pairing.py (closed-trades rollup) and
routes/attribution.py (single-day attribution view)."""
from __future__ import annotations

from backend.algo.attribution.fifo_matcher import match_fifo


def _buy(event_id, qty, price, ts_ns):
    return {
        "event_id": event_id, "qty": qty,
        "price": price, "ts_ns": ts_ns,
    }


def _sell(event_id, qty, price, ts_ns):
    return {
        "event_id": event_id, "qty": qty,
        "price": price, "ts_ns": ts_ns,
    }


def test_one_to_one_match():
    buys = [_buy("b1", 10, 300.0, 100)]
    sells = [_sell("s1", 10, 310.0, 200)]
    lots = match_fifo(buys, sells)
    assert len(lots) == 1
    lot = lots[0]
    assert lot["buy_event_id"] == "b1"
    assert lot["sell_event_id"] == "s1"
    assert lot["qty"] == 10
    assert lot["buy_price"] == 300.0
    assert lot["sell_price"] == 310.0
    assert lot["buy_ts_ns"] == 100
    assert lot["sell_ts_ns"] == 200


def test_one_buy_split_across_multiple_sells():
    """The KTKBANK shape: one BUY of 16, exited via three separate
    SELLs (1, 1, 14) at different prices and dates. Must produce
    three lots, each carrying its own sell's price/date, not one
    row with the full buy qty against only the first sell."""
    buys = [_buy("b1", 16, 266.3, 100)]
    sells = [
        _sell("s1", 1, 267.0, 200),
        _sell("s2", 1, 267.0, 201),
        _sell("s3", 14, 270.15, 300),
    ]
    lots = match_fifo(buys, sells)
    assert len(lots) == 3
    assert [lot["sell_event_id"] for lot in lots] == [
        "s1", "s2", "s3",
    ]
    assert [lot["qty"] for lot in lots] == [1, 1, 14]
    for lot in lots:
        assert lot["buy_event_id"] == "b1"
        assert lot["buy_price"] == 266.3
    assert lots[0]["sell_price"] == 267.0
    assert lots[2]["sell_price"] == 270.15
    assert lots[2]["sell_ts_ns"] == 300
    total_qty = sum(lot["qty"] for lot in lots)
    assert total_qty == 16


def test_one_sell_spans_multiple_buys():
    """Reverse shape: two separate BUY lots, closed by a single
    SELL that covers both."""
    buys = [
        _buy("b1", 5, 100.0, 100),
        _buy("b2", 5, 110.0, 150),
    ]
    sells = [_sell("s1", 10, 120.0, 200)]
    lots = match_fifo(buys, sells)
    assert len(lots) == 2
    assert lots[0]["buy_event_id"] == "b1"
    assert lots[0]["qty"] == 5
    assert lots[0]["buy_price"] == 100.0
    assert lots[1]["buy_event_id"] == "b2"
    assert lots[1]["qty"] == 5
    assert lots[1]["buy_price"] == 110.0
    for lot in lots:
        assert lot["sell_event_id"] == "s1"
        assert lot["sell_price"] == 120.0


def test_unconsumed_buy_quantity_excluded():
    """A buy larger than the available sell quantity leaves an
    open position -- excluded from the output, same as today's
    "unmatched fills are skipped" semantics."""
    buys = [_buy("b1", 10, 300.0, 100)]
    sells = [_sell("s1", 4, 310.0, 200)]
    lots = match_fifo(buys, sells)
    assert len(lots) == 1
    assert lots[0]["qty"] == 4


def test_sell_exceeding_available_buys_is_dropped_with_warning(
    caplog,
):
    """More sold than ever bought is a genuine data problem, not
    expected in practice -- must be dropped and logged, never
    fabricated into a phantom trade."""
    buys = [_buy("b1", 5, 300.0, 100)]
    sells = [_sell("s1", 8, 310.0, 200)]
    with caplog.at_level("WARNING"):
        lots = match_fifo(buys, sells)
    assert len(lots) == 1
    assert lots[0]["qty"] == 5
    assert any(
        "match_fifo" in rec.message for rec in caplog.records
    )


def test_no_buys_or_no_sells_returns_empty():
    assert match_fifo([], [_sell("s1", 5, 100.0, 100)]) == []
    assert match_fifo([_buy("b1", 5, 100.0, 100)], []) == []
    assert match_fifo([], []) == []


def test_inputs_need_not_be_pre_sorted():
    """FIFO order must be enforced internally by ts_ns, regardless
    of the order callers pass fills in."""
    buys = [
        _buy("b2", 5, 110.0, 150),
        _buy("b1", 5, 100.0, 100),
    ]
    sells = [_sell("s1", 10, 120.0, 200)]
    lots = match_fifo(buys, sells)
    assert lots[0]["buy_event_id"] == "b1"
    assert lots[1]["buy_event_id"] == "b2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_fifo_matcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named
'backend.algo.attribution.fifo_matcher'`

- [ ] **Step 3: Write the implementation**

Create `backend/algo/attribution/fifo_matcher.py`:

```python
"""Shared quantity-aware FIFO matcher for BUY/SELL fill pairing.

Used by both ``trade_pairing.py`` (the Strategy Performance
closed-trades rollup job) and ``routes/attribution.py`` (the
single-day attribution view). Both previously paired fills by list
*index* (``buys[i]`` with ``sells[i]``), which silently produced
wrong quantity/price and dropped trades whenever a ticker's fills
were not a clean 1:1 sequence -- found 2026-07-02 via KTKBANK: one
BUY of 16 shares exited via three separate SELLs (1, 1, 14) at
different prices on different days; the index-based pairing paired
the full 16-share buy against only the first 1-share sell and
silently dropped the other two sell events (including the 14-share,
₹270.15 exit).
"""
from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


def match_fifo(
    buys: list[dict[str, Any]],
    sells: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Quantity-aware FIFO match of BUY fills against SELL fills.

    Each input dict must carry: ``event_id`` (str), ``qty`` (int,
    positive), ``price`` (float), ``ts_ns`` (int). Inputs need not
    be pre-sorted -- this function sorts internally by ``ts_ns``.

    Returns a list of closed-lot records, oldest-sell-first, each:
    ``buy_event_id``, ``sell_event_id``, ``qty`` (the quantity this
    lot closes -- may be less than either fill's own qty when a buy
    or sell spans multiple lots), ``buy_price``, ``sell_price``,
    ``buy_ts_ns``, ``sell_ts_ns``.

    A single buy fill larger than the sell that (partially) closes
    it produces multiple output records sharing the same
    ``buy_event_id``, one per consuming sell. Symmetrically, a sell
    larger than the next available buy produces multiple records
    sharing the same ``sell_event_id`` against different buys.

    Unconsumed buy quantity (an open position with no closing sell
    yet) is excluded from the output -- matches the pre-existing
    "unmatched fills are skipped" semantics of both callers. Sell
    quantity left over after the buy queue is exhausted (more sold
    than ever bought -- a genuine data problem, not expected in
    practice) is dropped with a logged warning rather than silently
    fabricated into a phantom trade.
    """
    sorted_buys = sorted(buys, key=lambda b: int(b["ts_ns"]))
    sorted_sells = sorted(sells, key=lambda s: int(s["ts_ns"]))

    buy_queue: list[dict[str, Any]] = [
        {**b, "_remaining": int(b["qty"])} for b in sorted_buys
    ]
    buy_i = 0
    out: list[dict[str, Any]] = []

    for sell in sorted_sells:
        remaining_sell = int(sell["qty"])
        while remaining_sell > 0:
            if buy_i >= len(buy_queue):
                _logger.warning(
                    "match_fifo: sell event_id=%s has %d unit(s) "
                    "with no remaining buy quantity to match -- "
                    "dropped, not fabricated as a trade.",
                    sell["event_id"], remaining_sell,
                )
                break
            buy_lot = buy_queue[buy_i]
            if buy_lot["_remaining"] <= 0:
                buy_i += 1
                continue
            slice_qty = min(remaining_sell, buy_lot["_remaining"])
            out.append({
                "buy_event_id": buy_lot["event_id"],
                "sell_event_id": sell["event_id"],
                "qty": slice_qty,
                "buy_price": float(buy_lot["price"]),
                "sell_price": float(sell["price"]),
                "buy_ts_ns": int(buy_lot["ts_ns"]),
                "sell_ts_ns": int(sell["ts_ns"]),
            })
            buy_lot["_remaining"] -= slice_qty
            remaining_sell -= slice_qty
            if buy_lot["_remaining"] == 0:
                buy_i += 1
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_fifo_matcher.py -v`
Expected: PASS (8/8)

- [ ] **Step 5: Commit**

```bash
git add backend/algo/attribution/fifo_matcher.py backend/algo/tests/test_fifo_matcher.py
git commit -m "feat(algo): add shared quantity-aware FIFO fill matcher

Replaces the index-based BUY/SELL pairing shared by trade_pairing.py
and routes/attribution.py, which silently produced wrong qty/price
and dropped trades whenever a ticker's fills weren't a clean 1:1
sequence (found 2026-07-02 via KTKBANK: 1 buy of 16 exited via 3
separate sells, only the first was ever paired).

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 2: Integrate the matcher into trade_pairing.py

**Files:**
- Modify: `backend/algo/attribution/trade_pairing.py`
- Test: `backend/algo/tests/test_trade_pairing.py`

**Interfaces:**
- Consumes: `match_fifo(buys, sells) -> list[dict]` from Task 1
  (`backend/algo/attribution/fifo_matcher.py`).

- [ ] **Step 1: Write the failing test**

Add to `backend/algo/tests/test_trade_pairing.py` (after
`test_pairs_single_buy_sell_fifo`):

```python
def test_one_buy_split_across_multiple_sells():
    """The KTKBANK shape found 2026-07-02: one BUY of 16 exited via
    three separate SELLs (1, 1, 14) at different prices/dates. Must
    produce three closed-trade rows, not one row with the full buy
    qty against only the first sell (which also silently dropped
    the other two sells entirely)."""
    events = [
        _fill(
            strategy_id="s1", event_id="b1", symbol="KTKBANK",
            side="BUY", qty=16, fill_price=266.3,
            ts=datetime(2026, 6, 24, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="s1", symbol="KTKBANK",
            side="SELL", qty=1, fill_price=267.0,
            ts=datetime(2026, 6, 25, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="s2", symbol="KTKBANK",
            side="SELL", qty=1, fill_price=267.0,
            ts=datetime(2026, 6, 25, 0, 0, 1, tzinfo=timezone.utc),
        ),
        _fill(
            strategy_id="s1", event_id="s3", symbol="KTKBANK",
            side="SELL", qty=14, fill_price=270.15,
            ts=datetime(2026, 7, 2, tzinfo=timezone.utc),
        ),
    ]
    trades = pair_fills_by_strategy_and_ticker(events)
    assert len(trades) == 3
    total_qty = sum(t["qty"] for t in trades)
    assert total_qty == 16
    last = [t for t in trades if t["sell_event_id"] == "s3"][0]
    assert last["qty"] == 14
    assert last["fill_price"] == 270.15
    assert last["closed_at"].isoformat() == "2026-07-02"
    total_pnl = sum(t["realised_pnl_inr"] for t in trades)
    assert round(total_pnl, 2) == 55.3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_trade_pairing.py::test_one_buy_split_across_multiple_sells -v`
Expected: FAIL — `assert 1 == 3` (only 1 trade produced today, the
buggy index-based pairing).

- [ ] **Step 3: Replace the pairing loop**

In `backend/algo/attribution/trade_pairing.py`, add the import at
the top (after the existing `import` block, before `_logger =
logging.getLogger(__name__)`):

```python
from backend.algo.attribution.fifo_matcher import match_fifo
```

Replace the entire body from `for i in range(min(len(buys),
len(sells))):` through the `out.append({...})` block (current lines
80-124) with:

```python
        buy_lookup = {f["event_id"]: f for f in buys}
        sell_lookup = {f["event_id"]: f for f in sells}
        match_buys = [
            {
                "event_id": f["event_id"],
                "qty": int(f["_payload"].get("qty") or 0),
                "price": float(
                    f["_payload"].get("fill_price")
                    or f["_payload"].get("price")
                    or 0,
                ),
                "ts_ns": int(f["ts_ns"]),
            }
            for f in buys
        ]
        match_sells = [
            {
                "event_id": f["event_id"],
                "qty": int(f["_payload"].get("qty") or 0),
                "price": float(
                    f["_payload"].get("fill_price")
                    or f["_payload"].get("price")
                    or 0,
                ),
                "ts_ns": int(f["ts_ns"]),
            }
            for f in sells
        ]
        for lot in match_fifo(match_buys, match_sells):
            sell_fill = sell_lookup[lot["sell_event_id"]]
            realised_pnl_inr = (
                (lot["sell_price"] - lot["buy_price"]) * lot["qty"]
            )
            return_pct = (
                (lot["sell_price"] - lot["buy_price"])
                / lot["buy_price"] * 100
                if lot["buy_price"] else 0.0
            )
            out.append({
                "strategy_id": strategy_id or None,
                "ticker": sym,
                "qty": lot["qty"],
                "avg_price": lot["buy_price"],
                "fill_price": lot["sell_price"],
                "opened_at": _ts_ns_to_date(lot["buy_ts_ns"]),
                "closed_at": _ts_ns_to_date(lot["sell_ts_ns"]),
                "opened_at_ts_ns": lot["buy_ts_ns"],
                "closed_at_ts_ns": lot["sell_ts_ns"],
                "realised_pnl_inr": realised_pnl_inr,
                "return_pct": return_pct,
                "exit_reason": (
                    sell_fill["_payload"].get("exit_reason")
                    or "signal"
                ),
                "dry_run": bool(
                    sell_fill["_payload"].get("dry_run", False),
                ),
                "buy_event_id": lot["buy_event_id"],
                "sell_event_id": lot["sell_event_id"],
            })
```

`buy_lookup` is unused in this version (only `sell_lookup` is
needed, for `exit_reason`/`dry_run` enrichment) — remove the
`buy_lookup` line since an unused variable would fail flake8.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_trade_pairing.py -v`
Expected: PASS, all tests including the new one (6/6) — the
existing 1:1 tests (`test_pairs_single_buy_sell_fifo`,
`test_does_not_mix_two_strategies_on_same_ticker`,
`test_unmatched_open_position_is_skipped`,
`test_live_fills_use_price_key_not_fill_price`,
`test_strips_ns_suffix_and_ignores_non_fill_events`) must produce
byte-identical output to before this change.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/attribution/trade_pairing.py backend/algo/tests/test_trade_pairing.py
git commit -m "fix(algo): use quantity-aware FIFO matcher in trade_pairing.py

Was pairing BUY/SELL fills by list index -- silently wrong qty and
dropped trades for any ticker with an unequal buy/sell fill count.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 3: Integrate the matcher into routes/attribution.py

**Files:**
- Modify: `backend/algo/routes/attribution.py:317-408`
- Test: `backend/algo/tests/test_attribution_routes.py`

**Interfaces:**
- Consumes: `match_fifo(buys, sells) -> list[dict]` from Task 1.

- [ ] **Step 1: Write the failing test**

Add to `backend/algo/tests/test_attribution_routes.py` (after
`test_trades_live_fills_use_price_key_not_fill_price`):

```python
def test_trades_one_buy_split_across_multiple_sells(
    app, monkeypatch,
) -> None:
    """The KTKBANK shape found 2026-07-02: one BUY fill of 16
    exited via three separate SELL fills (1, 1, 14). Must surface
    as three trade rows, not one row with the full buy qty against
    only the first sell fill."""
    base_ts = int(
        datetime(2026, 6, 24, 6, 11, tzinfo=timezone.utc)
        .timestamp() * 1_000_000_000,
    )
    day_ns = 86_400 * 1_000_000_000
    fake_events = [
        {
            "user_id": USER_ID, "strategy_id": "ssss",
            "type": "order_filled_live", "ts_ns": base_ts,
            "payload_json": (
                '{"symbol": "KTKBANK", "side": "BUY", "qty": 16, '
                '"price": "266.3"}'
            ),
        },
        {
            "user_id": USER_ID, "strategy_id": "ssss",
            "type": "order_filled_live", "ts_ns": base_ts + day_ns,
            "payload_json": (
                '{"symbol": "KTKBANK", "side": "SELL", "qty": 1, '
                '"price": "267.0"}'
            ),
        },
        {
            "user_id": USER_ID, "strategy_id": "ssss",
            "type": "order_filled_live",
            "ts_ns": base_ts + day_ns + 1,
            "payload_json": (
                '{"symbol": "KTKBANK", "side": "SELL", "qty": 1, '
                '"price": "267.0"}'
            ),
        },
        {
            "user_id": USER_ID, "strategy_id": "ssss",
            "type": "order_filled_live",
            "ts_ns": base_ts + 8 * day_ns,
            "payload_json": (
                '{"symbol": "KTKBANK", "side": "SELL", "qty": 14, '
                '"price": "270.15"}'
            ),
        },
    ]
    monkeypatch.setattr(
        "backend.db.duckdb_engine.query_iceberg_table",
        lambda table, sql, params=None: fake_events,
    )
    client = TestClient(app)
    r = client.get("/v1/algo/attribution/trades")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    total_qty = sum(row["qty"] for row in body["rows"])
    assert total_qty == 16
    biggest = [row for row in body["rows"] if row["qty"] == 14][0]
    assert biggest["exit_price"] == 270.15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_attribution_routes.py::test_trades_one_buy_split_across_multiple_sells -v`
Expected: FAIL — `assert 1 == 3`.

- [ ] **Step 3: Replace the pairing loop**

In `backend/algo/routes/attribution.py`, add the import near the
top (alongside the other `backend.algo` imports — check the
existing import block at the top of the file for the right spot,
grouped with other same-package imports):

```python
from backend.algo.attribution.fifo_matcher import match_fifo
```

Replace the block from `for i in range(min(len(buy_fills),
len(sell_fills))):` through the closing `out.append({...})` (current
lines 346-408) with:

```python
            buy_idx_by_id = {
                f["event_id"]: i for i, f in enumerate(buy_fills)
            }
            sell_idx_by_id = {
                f["event_id"]: i for i, f in enumerate(sell_fills)
            }
            match_buys = [
                {
                    "event_id": f["event_id"],
                    "qty": int(f["_payload"].get("qty") or 0),
                    "price": float(
                        f["_payload"].get("fill_price")
                        or f["_payload"].get("price")
                        or 0,
                    ),
                    "ts_ns": int(f["ts_ns"]),
                }
                for f in buy_fills
            ]
            match_sells = [
                {
                    "event_id": f["event_id"],
                    "qty": int(f["_payload"].get("qty") or 0),
                    "price": float(
                        f["_payload"].get("fill_price")
                        or f["_payload"].get("price")
                        or 0,
                    ),
                    "ts_ns": int(f["ts_ns"]),
                }
                for f in sell_fills
            ]
            for lot in match_fifo(match_buys, match_sells):
                buy_idx = buy_idx_by_id[lot["buy_event_id"]]
                sell_idx = sell_idx_by_id[lot["sell_event_id"]]
                entry_event = (
                    buy_sigs[buy_idx]
                    if buy_idx < len(buy_sigs) else None
                )
                exit_event = (
                    sell_sigs[sell_idx]
                    if sell_idx < len(sell_sigs) else None
                )
                pnl_inr = (
                    (lot["sell_price"] - lot["buy_price"])
                    * lot["qty"]
                )
                trade = {
                    "ticker": sym,
                    "opened_at": _ts_ns_to_date(
                        lot["buy_ts_ns"],
                    ),
                    "closed_at": _ts_ns_to_date(
                        lot["sell_ts_ns"],
                    ),
                    "qty": lot["qty"],
                    "avg_entry_price": lot["buy_price"],
                    "avg_exit_price": lot["sell_price"],
                    "realised_pnl_inr": pnl_inr,
                }
                reason = build_trade_reason(
                    trade, entry_event, exit_event,
                )
                out.append({
                    "ticker": reason.ticker,
                    "opened_at": _iso_date(reason.opened_at),
                    "closed_at": _iso_date(reason.closed_at),
                    "qty": reason.qty,
                    "entry_price": reason.entry_price,
                    "exit_price": reason.exit_price,
                    "pnl_inr": reason.pnl_inr,
                    "pnl_pct": reason.pnl_pct,
                    "entry_regime": reason.entry_regime,
                    "stress_prob": reason.stress_prob,
                    "entry_factor_exposures": (
                        reason.entry_factor_exposures
                    ),
                    "exit_reason": reason.exit_reason,
                    "reason_text": reason.reason_text,
                })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_attribution_routes.py -v`
Expected: PASS, all tests including the new one — the existing 1:1
tests (`test_trades_pairs_buy_sell_signals_via_fills`,
`test_trades_panic_close_pairs_without_sell_signal`,
`test_trades_live_fills_use_price_key_not_fill_price`,
`test_trades_pairs_when_no_signals_at_all`,
`test_trades_returns_empty_when_no_events`) must produce
byte-identical output to before this change.

- [ ] **Step 5: Commit**

```bash
git add backend/algo/routes/attribution.py backend/algo/tests/test_attribution_routes.py
git commit -m "fix(algo): use quantity-aware FIFO matcher in attribution routes

Same fix as trade_pairing.py (this file's index-based pairing was
the original bug -- trade_pairing.py was modeled on it). Signal
enrichment (entry_regime, exit_reason from signal_generated events)
now looks up buy_sigs/sell_sigs by the matched fill's own list
index, preserving existing positional-enrichment behavior while
fixing the underlying fill-pairing quantity bug.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 4: GTT true-fill-price lookup in live/runtime.py

**Files:**
- Modify: `backend/algo/live/runtime.py:1494-1546`
- Test: `backend/algo/tests/test_ratchet_gtt_poll_emits_fill.py`

**Interfaces:**
- Produces: `order_filled_live` event payloads for GTT-triggered
  fills now carry a `price_source` field:
  `"kite_orders"` (confirmed real fill) or
  `"gtt_config_estimate"` (fallback, today's existing behavior).

- [ ] **Step 1: Write the failing test**

Add to `backend/algo/tests/test_ratchet_gtt_poll_emits_fill.py`
(after `test_gtt_poll_trigger_emits_order_filled_live_with_kite_qty`):

```python
def test_gtt_poll_uses_true_fill_price_from_kite_orders():
    """kite.get_gtts() only exposes the GTT's *configured* order
    price, not the actual post-trigger execution price -- found
    2026-07-02: three same-day GTT triggers (SKYGOLD, SOUTHBANK,
    ZENTEC) all recorded a price systematically lower than
    Zerodha's actual executed avg. When a matching COMPLETE SELL
    order is found in kite.orders() (today-scoped order history),
    its average_price must be used instead of the GTT's configured
    price, tagged price_source='kite_orders'."""
    from backend.algo.strategy.ast import RiskPerTrade
    from backend.algo.backtest.trailing_stop_manager import (
        TrailingStopManager,
    )

    rt = _make_runtime()
    ticker = "HSCL.NS"

    mgr = TrailingStopManager(
        risk=RiskPerTrade(stop_loss_pct=5.0, max_qty=1000),
        entry_price=642.6,
        atr=15.0,
        ticker=ticker,
    )
    rt._trailing_managers[ticker] = mgr
    rt._gtt_ids[ticker] = 325479574
    rt._ws_hwm[ticker] = 693.0
    rt._ticker_locked.add(ticker)
    rt._positions.open_positions = MagicMock(return_value={})

    rt._kite.get_gtts = MagicMock(return_value=[
        {
            "id": 325479574,
            "status": "triggered",
            "orders": [
                {
                    "transaction_type": "SELL",
                    "quantity": 4,
                    "price": 673.722,
                },
            ],
        },
    ])
    # The real order Kite actually executed -- a better (higher,
    # since this is a SELL) price than the GTT's configured order.
    rt._kite._kc.orders = MagicMock(return_value=[
        {
            "tradingsymbol": "HSCL",
            "transaction_type": "SELL",
            "status": "COMPLETE",
            "average_price": 675.10,
            "order_timestamp": "2026-07-02 11:00:00",
        },
    ])

    with patch.object(
        rt, "_sync_ticker_lock_to_redis",
    ), patch("backend.cache.get_cache"):
        rt._ratchet_all_gtts()

    fill_events = [
        e for e in rt._events if e["type"] == "order_filled_live"
    ]
    assert len(fill_events) == 1
    payload = json.loads(
        fill_events[0]["payload_json"],
    ) if "payload_json" in fill_events[0] else fill_events[0]["payload"]
    assert float(payload["price"]) == pytest.approx(675.10), (
        "must use Kite's real executed average_price, not the "
        "GTT's configured order price"
    )
    assert payload["price_source"] == "kite_orders"


def test_gtt_poll_falls_back_to_estimate_when_no_matching_order():
    """No matching COMPLETE SELL order in kite.orders() (API error,
    order not yet visible, or genuinely no match) -- must fall back
    to today's existing estimate unchanged, tagged
    price_source='gtt_config_estimate'."""
    from backend.algo.strategy.ast import RiskPerTrade
    from backend.algo.backtest.trailing_stop_manager import (
        TrailingStopManager,
    )

    rt = _make_runtime()
    ticker = "HSCL.NS"

    mgr = TrailingStopManager(
        risk=RiskPerTrade(stop_loss_pct=5.0, max_qty=1000),
        entry_price=642.6,
        atr=15.0,
        ticker=ticker,
    )
    rt._trailing_managers[ticker] = mgr
    rt._gtt_ids[ticker] = 325479574
    rt._ws_hwm[ticker] = 693.0
    rt._ticker_locked.add(ticker)
    rt._positions.open_positions = MagicMock(return_value={})

    rt._kite.get_gtts = MagicMock(return_value=[
        {
            "id": 325479574,
            "status": "triggered",
            "orders": [
                {
                    "transaction_type": "SELL",
                    "quantity": 4,
                    "price": 673.722,
                },
            ],
        },
    ])
    rt._kite._kc.orders = MagicMock(return_value=[])

    with patch.object(
        rt, "_sync_ticker_lock_to_redis",
    ), patch("backend.cache.get_cache"):
        rt._ratchet_all_gtts()

    fill_events = [
        e for e in rt._events if e["type"] == "order_filled_live"
    ]
    assert len(fill_events) == 1
    payload = json.loads(
        fill_events[0]["payload_json"],
    ) if "payload_json" in fill_events[0] else fill_events[0]["payload"]
    assert float(payload["price"]) == pytest.approx(673.722)
    assert payload["price_source"] == "gtt_config_estimate"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ratchet_gtt_poll_emits_fill.py -v`
Expected: `test_gtt_poll_uses_true_fill_price_from_kite_orders`
FAILs — `assert 673.722 == pytest.approx(675.1)` (still using the
estimate). `test_gtt_poll_falls_back_to_estimate_when_no_matching_order`
FAILs — `KeyError: 'price_source'` (field doesn't exist yet). The
original `test_gtt_poll_trigger_emits_order_filled_live_with_kite_qty`
still PASSes unchanged (no `_kc.orders` mock set, so `MagicMock()`'s
auto-generated `.orders()` return value won't match anything real
— verify this explicitly in the next step, not assumed).

- [ ] **Step 3: Add the Kite order-history lookup**

In `backend/algo/live/runtime.py`, replace lines 1504-1511 (the
`_stop_price`/`_fill_price` computation) with:

```python
                _stop_price = mgr.current_stop
                _true_price = _lookup_true_gtt_fill_price(
                    self._kite, ticker,
                )
                if _true_price is not None:
                    _fill_price = _true_price
                    _price_source = "kite_orders"
                else:
                    # The GTT's configured LIMIT price is closer to
                    # the true fill than our own stop-trigger price
                    # (fallback when Kite's order history doesn't
                    # yet have a matching COMPLETE order).
                    _fill_price = float(
                        _kite_order.get("price") or _stop_price,
                    )
                    _price_source = "gtt_config_estimate"
```

Then update the `payload` dict inside the `if _fill_qty > 0:`
block (current lines 1533-1546) to add the new field — the payload
dict becomes:

```python
                            payload={
                                "symbol": ticker.removesuffix(
                                    ".NS",
                                ).removesuffix(".BO"),
                                "side": "SELL",
                                "qty": _fill_qty,
                                "price": str(_fill_price),
                                "fees_inr": "0",
                                "reason": "gtt_triggered",
                                "product": getattr(
                                    self._strategy, "product", "CNC",
                                ),
                                "source": "gtt_poll",
                                "price_source": _price_source,
                            },
```

Add the new helper function above `class LiveRuntime` (find the
line `class LiveRuntime` near the top of the file and insert
immediately before it — check with `grep -n "^class LiveRuntime"
backend/algo/live/runtime.py` for the exact line):

```python
def _lookup_true_gtt_fill_price(
    kite: Any, ticker: str,
) -> float | None:
    """Look up the real executed average price for a just-
    triggered GTT SELL from Kite's today-scoped order history.

    ``kite.get_gtts()`` only exposes the GTT's *configured* order
    price, never the actual fill -- this queries the real order
    book (``kite._kc.orders()``, raw KiteConnect client; KiteClient
    does not wrap this method, same gotcha as its missing ``ltp()``
    wrapper) for a matching COMPLETE SELL order and returns its
    ``average_price``. Returns ``None`` on any failure (API error,
    no matching order found) so the caller can fall back to the
    GTT's configured price estimate -- never raises.
    """
    bare_symbol = ticker.removesuffix(".NS").removesuffix(".BO")
    try:
        kc = getattr(kite, "_kc", None)
        if kc is None:
            return None
        orders = kc.orders() or []
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "gtt true-price lookup: kite.orders() failed for "
            "%s: %s", ticker, exc,
        )
        return None
    matches = [
        o for o in orders
        if o.get("tradingsymbol") == bare_symbol
        and o.get("transaction_type") == "SELL"
        and str(o.get("status") or "").upper() == "COMPLETE"
    ]
    if not matches:
        return None
    # Most recent completed SELL for this symbol today -- the
    # GTT trigger is, by construction, recent.
    latest = max(
        matches,
        key=lambda o: str(o.get("order_timestamp") or ""),
    )
    avg_price = latest.get("average_price")
    if not avg_price:
        return None
    try:
        return float(avg_price)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_ratchet_gtt_poll_emits_fill.py -v`
Expected: PASS (3/3) — including the original
`test_gtt_poll_trigger_emits_order_filled_live_with_kite_qty`,
which doesn't set `rt._kite._kc.orders`, so `MagicMock()`'s
auto-mock returns a `MagicMock` from `.orders()`; `orders or []`
keeps the MagicMock (truthy), then `o.get(...)` on each "order"
(iterating a MagicMock raises `TypeError: object is not iterable`)
— catch this: if this test now fails, confirm the auto-mock
behavior with a quick `python -c "from unittest.mock import
MagicMock; list(MagicMock()() or [])"` check and, if it raises,
explicitly set `rt._kite._kc.orders = MagicMock(return_value=[])`
in `_make_runtime()`'s shared setup (in `test_ratchet_gtt_poll_
emits_fill.py`, inside `_make_runtime()`, right after `kite._kc =
kc_instance`) so every test in this file has a safe, empty default
unless it explicitly overrides it.

- [ ] **Step 5: Run the full GTT/live test suite for regressions**

Run: `docker compose exec backend python -m pytest backend/algo/tests/ -k "gtt or trailing or ratchet" -v`
Expected: all PASS, no regressions from the new helper function or
payload field addition.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/live/runtime.py backend/algo/tests/test_ratchet_gtt_poll_emits_fill.py
git commit -m "fix(algo): GTT Piece A uses Kite's true fill price, not estimate

kite.get_gtts() only exposes the GTT's configured order price, not
the actual post-trigger execution price. Found 2026-07-02: three
same-day GTT triggers (SKYGOLD, SOUTHBANK, ZENTEC) all recorded a
price systematically lower than Zerodha's actual executed avg.

Now looks up the real COMPLETE SELL order from kite._kc.orders()
(today-scoped order history) and uses its average_price when found,
falling back to the existing estimate otherwise. Every GTT-triggered
fill's payload now carries price_source ('kite_orders' | 'gtt_config_
estimate') so the data quality is visible without guessing.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 5: One-off GTT price correction script (SKYGOLD/SOUTHBANK/ZENTEC)

**Files:**
- Create: `backend/algo/jobs/gtt_price_correction.py`
- Create: `scripts/backfill_gtt_price_corrections.py`
- Test: `backend/algo/tests/test_gtt_price_correction.py`

**Interfaces:**
- Produces: `find_true_price(orders: list[dict], symbol: str) ->
  float | None` — pure function, reuses the same matching logic as
  Task 4's `_lookup_true_gtt_fill_price` but decoupled from the
  `KiteClient` object so it's independently unit-testable and
  reusable by the one-off script (which authenticates its own Kite
  session, not a `LiveRuntime`'s).

Only SKYGOLD, SOUTHBANK, ZENTEC are in scope — all three triggered
today (2026-07-02), so Kite's today-scoped order history still has
them. HSCL (2026-07-01) is not recoverable via this API and keeps
its documented estimate from ASETPLTFRM-466.

- [ ] **Step 1: Write the failing test**

Create `backend/algo/tests/test_gtt_price_correction.py`:

```python
"""Tests for the pure order-matching logic used by the one-off GTT
price correction script (scripts/backfill_gtt_price_corrections.py).
"""
from __future__ import annotations

from backend.algo.jobs.gtt_price_correction import find_true_price


def test_finds_matching_complete_sell():
    orders = [
        {
            "tradingsymbol": "SKYGOLD",
            "transaction_type": "SELL",
            "status": "COMPLETE",
            "average_price": 553.30,
            "order_timestamp": "2026-07-02 14:00:12",
        },
        {
            "tradingsymbol": "SKYGOLD",
            "transaction_type": "BUY",
            "status": "COMPLETE",
            "average_price": 498.70,
            "order_timestamp": "2026-06-24 11:42:00",
        },
    ]
    assert find_true_price(orders, "SKYGOLD") == 553.30


def test_ignores_non_complete_orders():
    orders = [
        {
            "tradingsymbol": "SKYGOLD",
            "transaction_type": "SELL",
            "status": "OPEN",
            "average_price": 0,
            "order_timestamp": "2026-07-02 14:00:12",
        },
    ]
    assert find_true_price(orders, "SKYGOLD") is None


def test_no_match_returns_none():
    assert find_true_price([], "ZENTEC") is None


def test_picks_most_recent_when_multiple_matches():
    orders = [
        {
            "tradingsymbol": "SOUTHBANK",
            "transaction_type": "SELL",
            "status": "COMPLETE",
            "average_price": 40.0,
            "order_timestamp": "2026-07-02 09:00:00",
        },
        {
            "tradingsymbol": "SOUTHBANK",
            "transaction_type": "SELL",
            "status": "COMPLETE",
            "average_price": 46.05,
            "order_timestamp": "2026-07-02 10:30:00",
        },
    ]
    assert find_true_price(orders, "SOUTHBANK") == 46.05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_gtt_price_correction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named
'backend.algo.jobs.gtt_price_correction'`

- [ ] **Step 3: Write the implementation**

Create `backend/algo/jobs/gtt_price_correction.py`:

```python
"""One-off GTT fill-price correction: patch already-written
order_filled_live events whose price came from the GTT's configured
order (an estimate) rather than Kite's true post-trigger execution
price, for triggers that happened today (order history is
today-scoped -- see docs/superpowers/specs/2026-07-02-closed-trade-
pairing-and-gtt-price-fix-design.md).

Usage::

    docker compose exec backend python \
        scripts/backfill_gtt_price_corrections.py
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pyiceberg.expressions import In

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.db.duckdb_engine import (
    invalidate_metadata,
    query_iceberg_table,
)
from backend.maintenance.backup import verify_or_backup

_logger = logging.getLogger(__name__)

ALGO_EVENTS_TABLE = "algo.events"


def find_true_price(
    orders: list[dict[str, Any]], symbol: str,
) -> float | None:
    """Find the real executed average price for a COMPLETE SELL
    order matching ``symbol`` in Kite's order history. Returns the
    most recent match's ``average_price``, or ``None`` if no
    COMPLETE SELL order matches."""
    matches = [
        o for o in orders
        if o.get("tradingsymbol") == symbol
        and o.get("transaction_type") == "SELL"
        and str(o.get("status") or "").upper() == "COMPLETE"
    ]
    if not matches:
        return None
    latest = max(
        matches,
        key=lambda o: str(o.get("order_timestamp") or ""),
    )
    avg_price = latest.get("average_price")
    if not avg_price:
        return None
    try:
        return float(avg_price)
    except (TypeError, ValueError):
        return None


def correct_gtt_event_prices(
    kite: Any, symbols: list[str], *, dry_run: bool = True,
) -> dict[str, Any]:
    """Find today's gtt_triggered order_filled_live events for
    ``symbols`` whose price_source is 'gtt_config_estimate' (or
    missing, for events written before Task 4 shipped), look up
    the true fill price via Kite's order history, and -- unless
    ``dry_run`` -- scoped-delete + re-append those specific event
    rows with the corrected price and price_source='kite_orders'.

    Iceberg is append-only; "patching" a row is delete-by-key then
    re-insert, never an in-place mutation.
    """
    kc = getattr(kite, "_kc", None)
    if kc is None:
        return {"status": "error", "error": "no _kc on kite client"}
    orders = kc.orders() or []

    sql = (
        "SELECT event_id, ts_ns, ts_date, session_id, user_id, "
        "strategy_id, mode, type, payload_json, written_at "
        "FROM events "
        "WHERE mode = 'live' AND type = 'order_filled_live'"
    )
    rows = query_iceberg_table(ALGO_EVENTS_TABLE, sql, [])

    to_correct: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get("reason") != "gtt_triggered":
            continue
        if payload.get("price_source") == "kite_orders":
            continue  # already corrected
        symbol = payload.get("symbol")
        if symbol not in symbols:
            continue
        true_price = find_true_price(orders, symbol)
        if true_price is None:
            _logger.warning(
                "gtt-price-correction: no matching COMPLETE SELL "
                "order found for %s -- leaving estimate as-is",
                symbol,
            )
            continue
        new_payload = {
            **payload,
            "price": str(true_price),
            "price_source": "kite_orders",
        }
        to_correct.append({**row, "_new_payload": new_payload})

    if dry_run or not to_correct:
        return {
            "status": "dry_run" if dry_run else "ok",
            "would_correct": [
                {
                    "event_id": r["event_id"],
                    "symbol": json.loads(
                        r["payload_json"],
                    ).get("symbol"),
                    "old_price": json.loads(
                        r["payload_json"],
                    ).get("price"),
                    "new_price": r["_new_payload"]["price"],
                }
                for r in to_correct
            ],
        }

    event_ids = [r["event_id"] for r in to_correct]
    verify_or_backup([ALGO_EVENTS_TABLE])

    import pyarrow as pa

    from backend.algo.backtest.event_writer import (
        _detect_ts_date_native_date,
    )

    native_date = _detect_ts_date_native_date()
    corrected_rows = []
    for r in to_correct:
        row = dict(r)
        row.pop("_new_payload")
        row["payload_json"] = json.dumps(r["_new_payload"])
        corrected_rows.append(row)

    def _do_correction() -> None:
        from stocks.create_tables import _get_catalog

        cat = _get_catalog()
        tbl = cat.load_table(ALGO_EVENTS_TABLE)
        tbl.delete(In("event_id", event_ids))
        schema = tbl.schema().as_arrow()
        arrow = pa.Table.from_pylist(corrected_rows, schema=schema)
        tbl.append(arrow)

    retry_iceberg_op(ALGO_EVENTS_TABLE, _do_correction)
    invalidate_metadata(ALGO_EVENTS_TABLE)
    _logger.info(
        "gtt-price-correction: corrected %d event(s): %s",
        len(to_correct), event_ids,
    )
    return {
        "status": "ok",
        "corrected": [
            {
                "event_id": r["event_id"],
                "symbol": r["_new_payload"]["symbol"],
                "new_price": r["_new_payload"]["price"],
            }
            for r in to_correct
        ],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec backend python -m pytest backend/algo/tests/test_gtt_price_correction.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Write the runner script**

Create `scripts/backfill_gtt_price_corrections.py`:

```python
"""One-off: correct today's GTT-triggered order_filled_live events
for SKYGOLD, SOUTHBANK, ZENTEC with Kite's true post-trigger
execution price, replacing the GTT's configured-order-price
estimate. See backend/algo/jobs/gtt_price_correction.py for the
implementation and docs/superpowers/specs/2026-07-02-closed-trade-
pairing-and-gtt-price-fix-design.md for why HSCL is out of scope
(Kite's order history is today-scoped; HSCL triggered 2026-07-01).

Run dry-run first (default), inspect the output, then re-run with
--apply.

Usage::

    docker compose exec backend python \
        scripts/backfill_gtt_price_corrections.py --user-id <uuid>
    docker compose exec backend python \
        scripts/backfill_gtt_price_corrections.py --user-id <uuid> --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from uuid import UUID

from backend.algo.jobs.gtt_price_correction import (
    correct_gtt_event_prices,
)

_logger = logging.getLogger(__name__)

_SYMBOLS = ["SKYGOLD", "SOUTHBANK", "ZENTEC"]


async def _run(user_id: UUID, apply: bool) -> None:
    from backend.algo.routes.live import (
        _build_kite_client_for_user,
    )

    kite = await _build_kite_client_for_user(user_id)
    result = correct_gtt_event_prices(
        kite, _SYMBOLS, dry_run=not apply,
    )
    _logger.info("gtt price correction result: %s", result)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write the correction (default: dry-run).",
    )
    parser.add_argument(
        "--user-id", required=True, type=UUID,
        help="User id whose Kite session to use.",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.user_id, args.apply))


if __name__ == "__main__":
    main()
```

`_build_kite_client_for_user` verified against
`backend/algo/routes/live.py:442`
(`async def _build_kite_client_for_user(user_id: UUID) ->
KiteClient`) — it's `async`, hence the `asyncio.run()` wrapper in
`main()`; `budget.py::_build_kite_for_user` explicitly documents
itself as mirroring this exact function, confirming it's the
canonical one to use here rather than a route-local duplicate.

- [ ] **Step 6: Commit**

```bash
git add backend/algo/jobs/gtt_price_correction.py scripts/backfill_gtt_price_corrections.py backend/algo/tests/test_gtt_price_correction.py
git commit -m "feat(algo): one-off GTT price correction script

Corrects today's SKYGOLD/SOUTHBANK/ZENTEC order_filled_live events
(gtt_triggered, price_source=gtt_config_estimate) with Kite's true
execution price from order history, while it's still available
(today-scoped). HSCL (yesterday's trigger) is out of scope --
documented as a permanent estimate in ASETPLTFRM-466.

Co-Authored-By: Abhay Kumar Singh <asequitytrading@gmail.com>"
```

---

### Task 6: Run the corrections and full backfill

This task is operational, not code — run after Tasks 1-5 are merged
and the backend has restarted with the new code (backend restart
required for the runtime.py change to take effect; confirm with the
user before restarting per the standing "always ask before backend
restart" rule).

- [ ] **Step 1: Dry-run the GTT price correction**

```bash
docker compose exec backend python scripts/backfill_gtt_price_corrections.py --user-id <the live trading user's id>
```

Inspect the `would_correct` list in the output — confirm it shows
exactly SKYGOLD, SOUTHBANK, ZENTEC with sensible `new_price` values
(SKYGOLD ≈ 553.30, SOUTHBANK ≈ 46.05, ZENTEC ≈ 1760.70, matching the
Zerodha screenshot values from the original investigation).

- [ ] **Step 2: Apply the GTT price correction**

```bash
docker compose exec backend python scripts/backfill_gtt_price_corrections.py --user-id <the live trading user's id> --apply
```

Verify: `result["status"] == "ok"` and `result["corrected"]` lists
3 events.

- [ ] **Step 3: Full re-derive of algo.closed_trades**

```bash
docker compose exec -T postgres psql -U app -d aiagent -c "TRUNCATE algo.closed_trades"
docker compose exec -e PYTHONPATH=.:backend backend python scripts/backfill_closed_trades.py
```

Expected log line: `closed-trades-rollup: today=<date>
window_days=3650 events=<N> trades=<M> dry_run=False` — `M` should
now include KTKBANK's 3-row split (previously 1) and the other 7
mismatched buckets' previously-dropped trades.

- [ ] **Step 4: Verify KTKBANK specifically**

```bash
docker compose exec -T postgres psql -U app -d aiagent -c "SELECT ticker, qty, avg_price, fill_price, closed_at, realised_pnl_inr FROM algo.closed_trades WHERE ticker='KTKBANK' ORDER BY closed_at"
```

Expected: 3 rows, quantities 1/1/14 (summing to 16), the third
row's `fill_price` = 270.15 and `closed_at` = 2026-07-02.

- [ ] **Step 5: Verify the GTT-triggered tickers**

```bash
docker compose exec -T postgres psql -U app -d aiagent -c "SELECT ticker, fill_price FROM algo.closed_trades WHERE ticker IN ('SKYGOLD','SOUTHBANK','ZENTEC') ORDER BY closed_at DESC LIMIT 3"
```

Expected: `fill_price` now matches Zerodha's actual executed avg
(SKYGOLD ≈ 553.30, SOUTHBANK ≈ 46.05, ZENTEC ≈ 1760.70), not the
old estimates.

- [ ] **Step 6: Spot-check the Performance page**

Load `/algo-trading/strategies?tab=performance`, Live mode, RSI(2)
Connors Daily v5 — confirm KTKBANK now shows as 3 separate rows (or
verify the aggregate "Total PnL" for the strategy increased by
≈₹44 from the previous ₹554, matching the corrected KTKBANK PnL of
₹55.30 vs. the old ₹11.20), and SKYGOLD/SOUTHBANK/ZENTEC's Fill
prices now match the Zerodha screenshot values.

---

## Self-Review Notes

- **Spec coverage:** Task 1 = shared matcher (spec "Shared
  quantity-aware FIFO matcher"). Tasks 2-3 = both call sites (spec
  same section). Task 4 = GTT price lookup (spec "GTT true-fill-
  price lookup"). Task 5 = GTT price backfill (spec "Backfill" item
  1). Task 6 = full re-derive (spec "Backfill" item 2) + manual
  verification. HSCL out-of-scope note carried into Task 5's
  docstring and Task 6 has no HSCL step. All spec sections covered.
- **Type consistency:** `match_fifo`'s output keys (`buy_event_id`,
  `sell_event_id`, `qty`, `buy_price`, `sell_price`, `buy_ts_ns`,
  `sell_ts_ns`) are used identically across Task 1's tests, Task 2's
  integration, and Task 3's integration — checked for consistency
  while writing.
- **Kite API surface:** `self._kite._kc.orders()` (not
  `get_orders()`) verified against the existing
  `live/order_timeout.py::_fetch_orders` precedent before writing
  Task 4 — this was wrong in an earlier draft of the design spec
  and was caught and corrected there first.
- **Every method/import referenced in this plan was verified by
  reading the actual source file before being written into a task**
  — including `_build_kite_client_for_user`
  (`backend/algo/routes/live.py:442`) in Task 5, Step 5, which an
  earlier draft had wrong (guessed `get_kite_client_for_user` in
  `credentials_repo.py`, which doesn't exist) and was caught and
  fixed during this self-review pass.

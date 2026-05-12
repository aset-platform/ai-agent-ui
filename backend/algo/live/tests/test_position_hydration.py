"""Tests for backend.algo.live.position_hydration.

ASETPLTFRM-376 — verifies LiveRuntime can hydrate its
PositionTracker from existing Kite positions + holdings so the
EXIT logic doesn't silently miss yesterday's overnight CNC or a
mid-session crash-recovered MIS leg.

The 5 cases cover:
  1. Empty Kite state              → no leg, no events.
  2. MIS position only             → source="positions".
  3. CNC holding only              → source="holdings", entry_ts
                                     resolved from a stub event.
  4. Symbol outside allowed list   → silently skipped.
  5. CNC holding with no matching  → entry_ts is None but the leg
     order_filled_live event         is still hydrated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from backend.algo.backtest.positions import PositionTracker
from backend.algo.live.position_hydration import (
    HydratedPosition,
    apply_hydrated_positions,
    hydrate,
    hydration_events,
)


UTC = timezone.utc


def _kite(
    *,
    positions_net: list[dict] | None = None,
    holdings: list[dict] | None = None,
) -> MagicMock:
    """Build a fake KiteClient whose ``_kc.positions()`` /
    ``_kc.holdings()`` return the canned shapes."""
    kc = MagicMock()
    kc.positions.return_value = {"net": positions_net or []}
    kc.holdings.return_value = holdings or []
    fake = SimpleNamespace(_kc=kc, dry_run=False)
    return fake


def _strategy() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), name="t")


# ---------------------------------------------------------------
# 1. Empty Kite state → no legs, no events.
# ---------------------------------------------------------------


def test_empty_kite_state_returns_empty_list() -> None:
    kite = _kite()
    out = hydrate(
        kite=kite,
        strategy=_strategy(),
        user_id=uuid4(),
        allowed_tickers=None,
        events_reader=lambda u, s: None,
    )
    assert out == []

    tracker = PositionTracker()
    apply_hydrated_positions(tracker, out)
    assert tracker.open_positions() == {}

    rows = hydration_events(
        session_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        hydrated=out,
        dry_run=False,
    )
    assert rows == []


# ---------------------------------------------------------------
# 2. MIS position only → source="positions".
# ---------------------------------------------------------------


def test_mis_position_hydrated_as_positions_source() -> None:
    kite = _kite(positions_net=[
        {
            "tradingsymbol": "RELIANCE",
            "quantity": 10,
            "product": "MIS",
            "average_price": 2500.50,
        },
    ])
    fixed_ts = int(datetime(
        2026, 5, 12, 4, 30, tzinfo=UTC,
    ).timestamp() * 1_000_000_000)
    out = hydrate(
        kite=kite,
        strategy=_strategy(),
        user_id=uuid4(),
        allowed_tickers=None,
        events_reader=lambda u, s: fixed_ts,
    )
    assert len(out) == 1
    leg = out[0]
    assert leg.symbol == "RELIANCE.NS"
    assert leg.qty == 10
    assert leg.avg_price == Decimal("2500.50")
    assert leg.source == "positions"
    assert leg.product == "MIS"
    assert leg.entry_ts is not None
    assert leg.entry_ts.tzinfo is not None

    tracker = PositionTracker()
    apply_hydrated_positions(tracker, out)
    assert "RELIANCE.NS" in tracker.open_positions()
    pos = tracker.open_positions()["RELIANCE.NS"]
    assert pos.qty == 10
    assert pos.avg_price == Decimal("2500.50")

    rows = hydration_events(
        session_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        hydrated=out,
        dry_run=False,
    )
    assert len(rows) == 1
    assert rows[0]["type"] == "position_hydrated"


# ---------------------------------------------------------------
# 3. CNC holding only → source="holdings", entry_ts resolved.
# ---------------------------------------------------------------


def test_cnc_holding_resolves_entry_ts_from_events() -> None:
    kite = _kite(holdings=[
        {
            "tradingsymbol": "ITC",
            "quantity": 8,
            "product": "CNC",
            "average_price": 307.33,
        },
    ])
    fixed_ts = int(datetime(
        2026, 5, 11, 10, 15, tzinfo=UTC,
    ).timestamp() * 1_000_000_000)
    captured: dict[str, list] = {"calls": []}

    def reader(uid: UUID, sym: str) -> int | None:
        captured["calls"].append((uid, sym))
        return fixed_ts if sym == "ITC.NS" else None

    out = hydrate(
        kite=kite,
        strategy=_strategy(),
        user_id=uuid4(),
        allowed_tickers=None,
        events_reader=reader,
    )
    assert len(out) == 1
    leg = out[0]
    assert leg.symbol == "ITC.NS"
    assert leg.qty == 8
    assert leg.source == "holdings"
    assert leg.product == "CNC"
    assert leg.entry_ts is not None
    assert leg.entry_ts.year == 2026
    assert leg.entry_ts.month == 5
    assert leg.entry_ts.day == 11
    assert captured["calls"], "events_reader should be invoked"


# ---------------------------------------------------------------
# 4. Symbol outside allowed_tickers → silently skipped.
# ---------------------------------------------------------------


def test_allowed_tickers_filters_out_unrelated_symbols() -> None:
    kite = _kite(
        positions_net=[
            {
                "tradingsymbol": "TCS",
                "quantity": 5,
                "product": "MIS",
                "average_price": 4000.0,
            },
        ],
        holdings=[
            {
                "tradingsymbol": "INFY",
                "quantity": 20,
                "product": "CNC",
                "average_price": 1500.0,
            },
            {
                "tradingsymbol": "RELIANCE",
                "quantity": 4,
                "product": "CNC",
                "average_price": 2500.0,
            },
        ],
    )
    out = hydrate(
        kite=kite,
        strategy=_strategy(),
        user_id=uuid4(),
        allowed_tickers=["RELIANCE.NS"],
        events_reader=lambda u, s: None,
    )
    assert len(out) == 1
    assert out[0].symbol == "RELIANCE.NS"
    assert out[0].source == "holdings"
    # TCS.NS and INFY.NS must be silently dropped.
    syms = {h.symbol for h in out}
    assert "TCS.NS" not in syms
    assert "INFY.NS" not in syms


# ---------------------------------------------------------------
# 5. CNC holding with no matching event → entry_ts None.
# ---------------------------------------------------------------


def test_holding_with_no_matching_event_has_none_entry_ts() -> None:
    kite = _kite(holdings=[
        {
            "tradingsymbol": "HDFCBANK",
            "quantity": 12,
            "product": "CNC",
            "average_price": 1620.0,
        },
    ])

    out = hydrate(
        kite=kite,
        strategy=_strategy(),
        user_id=uuid4(),
        allowed_tickers=None,
        events_reader=lambda u, s: None,
    )
    assert len(out) == 1
    leg = out[0]
    assert leg.symbol == "HDFCBANK.NS"
    assert leg.qty == 12
    assert leg.source == "holdings"
    assert leg.entry_ts is None

    rows = hydration_events(
        session_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        hydrated=out,
        dry_run=False,
    )
    assert len(rows) == 1
    import json as _json
    payload = _json.loads(rows[0]["payload_json"])
    assert payload["entry_ts"] is None
    assert payload["symbol"] == "HDFCBANK.NS"
    assert payload["source"] == "holdings"
    assert payload["product"] == "CNC"


# ---------------------------------------------------------------
# Bonus: zero-qty / non-MIS positions are skipped.
# ---------------------------------------------------------------


def test_zero_qty_positions_and_non_mis_are_skipped() -> None:
    kite = _kite(
        positions_net=[
            # qty=0 — closed intraday, must be dropped.
            {"tradingsymbol": "FOO", "quantity": 0,
             "product": "MIS", "average_price": 100.0},
            # product CNC inside the "net" list — Kite shouldn't,
            # but defence in depth.
            {"tradingsymbol": "BAR", "quantity": 3,
             "product": "CNC", "average_price": 200.0},
        ],
    )
    out = hydrate(
        kite=kite,
        strategy=_strategy(),
        user_id=uuid4(),
        allowed_tickers=None,
        events_reader=lambda u, s: None,
    )
    assert out == []


def test_hydrated_position_dataclass_is_frozen() -> None:
    """Defence: HydratedPosition mustn't mutate after construction."""
    leg = HydratedPosition(
        symbol="RELIANCE.NS",
        qty=10,
        avg_price=Decimal("2500"),
        source="positions",
        product="MIS",
        entry_ts=None,
    )
    import dataclasses
    assert dataclasses.is_dataclass(leg)
    try:
        leg.qty = 99  # type: ignore[misc]
    except (AttributeError, dataclasses.FrozenInstanceError):
        pass
    else:
        raise AssertionError(
            "HydratedPosition should be frozen",
        )

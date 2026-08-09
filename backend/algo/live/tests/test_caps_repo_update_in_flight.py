"""Regression tests for CapsRepo.update_in_flight's merge-safe write.

2026-08-05: LiveRuntime._submit_order calls update_in_flight with its
own in-memory in_flight snapshot on every order submission. That
snapshot never learns of a postback-driven status flip except via the
periodic fill-sync poll, so a blind overwrite from it could clobber a
terminal status (filled/rejected/cancelled) the Kite postback webhook
had just written directly to Postgres moments earlier — exactly what
happened to SFL.NS: its SELL fill was confirmed by Kite, then reverted
back to "submitted" in PG by an ARVIND.NS order submitted 4s later on
the same run, so the position tracker never saw the close and kept
retrying the SELL forever (harmlessly rejected by Kite each time).

update_in_flight now reads-under-lock and merges by kite_order_id
instead of blindly overwriting, and never downgrades a terminal entry.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from backend.algo.live.caps_repo import CapsRepo


def _fake_factory(current_in_flight: list[dict]):
    """Build a get_session_factory() replacement.

    Mirrors caps_repo's ``factory = get_session_factory(); async with
    factory() as session:`` — two calls deep, so the mock needs a
    session-maker layer whose OWN call returns the async-context-
    manager layer. The SELECT ... FOR UPDATE call returns
    ``current_in_flight`` as the row's current live_orders_in_flight
    value; the UPDATE call's payload is inspectable via
    ``session.execute.call_args_list[1]``.
    """
    session = MagicMock()
    select_result = MagicMock()
    select_result.one_or_none.return_value = (current_in_flight,)
    session.execute = AsyncMock(side_effect=[select_result, MagicMock()])
    session.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    session_maker = MagicMock(return_value=ctx)
    get_session_factory_mock = MagicMock(return_value=session_maker)
    return get_session_factory_mock, session


@pytest.mark.asyncio
async def test_stale_snapshot_never_downgrades_terminal_status(
    monkeypatch,
):
    """The exact SFL.NS bug: a stale 'submitted' in-memory entry must
    not revert a PG-side 'filled' entry back to 'submitted'."""
    run_id, user_id = uuid4(), uuid4()
    current = [
        {
            "kite_order_id": "ORD1",
            "symbol": "SFL",
            "side": "SELL",
            "status": "filled",
            "fill_qty": 5,
            "fill_price": "802.65",
        },
    ]
    factory, session = _fake_factory(current)
    monkeypatch.setattr(
        "backend.algo.live.caps_repo.get_session_factory", factory,
    )

    repo = CapsRepo()
    stale_in_memory = [
        {
            "kite_order_id": "ORD1",
            "symbol": "SFL",
            "side": "SELL",
            "status": "submitted",
        },
    ]
    await repo.update_in_flight(user_id, run_id, stale_in_memory)

    payload = json.loads(
        session.execute.call_args_list[1].args[1]["payload"]
    )
    assert payload[0]["status"] == "filled"
    assert payload[0]["fill_qty"] == 5
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_new_entry_is_appended_not_dropped(monkeypatch):
    """A genuinely new kite_order_id from the caller is merged in,
    alongside the untouched existing terminal entry."""
    run_id, user_id = uuid4(), uuid4()
    current = [
        {"kite_order_id": "ORD1", "symbol": "SFL", "status": "filled"},
    ]
    factory, session = _fake_factory(current)
    monkeypatch.setattr(
        "backend.algo.live.caps_repo.get_session_factory", factory,
    )

    repo = CapsRepo()
    incoming = [
        {"kite_order_id": "ORD1", "symbol": "SFL", "status": "filled"},
        {
            "kite_order_id": "ORD2",
            "symbol": "ARVIND",
            "status": "submitted",
        },
    ]
    await repo.update_in_flight(user_id, run_id, incoming)

    payload = json.loads(
        session.execute.call_args_list[1].args[1]["payload"]
    )
    ids = {e["kite_order_id"] for e in payload}
    assert ids == {"ORD1", "ORD2"}


@pytest.mark.asyncio
async def test_non_terminal_status_update_is_applied(monkeypatch):
    """A non-terminal → non-terminal transition (e.g. status refresh
    before any fill) still applies normally — only a downgrade FROM
    a terminal status is blocked."""
    run_id, user_id = uuid4(), uuid4()
    current = [
        {
            "kite_order_id": "ORD1",
            "symbol": "SFL",
            "status": "submitted",
        },
    ]
    factory, session = _fake_factory(current)
    monkeypatch.setattr(
        "backend.algo.live.caps_repo.get_session_factory", factory,
    )

    repo = CapsRepo()
    incoming = [
        {
            "kite_order_id": "ORD1",
            "symbol": "SFL",
            "status": "filled",
            "fill_qty": 5,
        },
    ]
    await repo.update_in_flight(user_id, run_id, incoming)

    payload = json.loads(
        session.execute.call_args_list[1].args[1]["payload"]
    )
    assert payload[0]["status"] == "filled"
    assert payload[0]["fill_qty"] == 5

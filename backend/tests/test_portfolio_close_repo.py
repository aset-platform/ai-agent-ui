"""Round-trip test for the closed-positions PG repo."""
import pytest

from auth.repo import portfolio_close_repo as repo


@pytest.mark.asyncio
async def test_add_and_list_round_trip(pg_session):
    row = await repo.add_closed_position(pg_session, {
        "user_id": "u1", "ticker": "DLF.NS", "quantity": 20,
        "buy_price": 746.26, "sell_price": 800.0,
        "sell_date": "2026-08-12",
        "fees": 0, "realized_pnl": 1074.8, "realized_pnl_pct": 0.036,
        "currency": "INR", "market": "india",
        "sell_transaction_id": "t1",
    })
    assert row["id"] and row["ticker"] == "DLF.NS"
    rows = await repo.list_closed_positions(pg_session, "u1")
    assert any(
        r["ticker"] == "DLF.NS"
        and float(r["realized_pnl"]) == 1074.8
        for r in rows
    )

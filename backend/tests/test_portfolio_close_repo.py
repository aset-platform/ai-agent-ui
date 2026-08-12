"""Round-trip test for the closed-positions PG repo."""
import pytest

from auth.repo import portfolio_close_repo as repo


@pytest.mark.asyncio
async def test_add_and_list_round_trip(pg_session):
    row = await repo.add_closed_position(pg_session, {
        "user_id": "u1", "ticker": "DLF.NS", "quantity": 20,
        "buy_price": 746.26, "sell_price": 800.0,
        "sell_date": "2026-08-12",
        "fees": 0, "realized_pnl": 1074.8, "realized_pnl_pct": 3.6,
        "currency": "INR", "market": "india",
        "sell_transaction_id": "t1",
    })
    assert row["id"] and row["ticker"] == "DLF.NS"
    # Numeric columns must serialize as float (not Decimal/str) so the
    # JSON API matches the frontend's `number` contract.
    assert isinstance(row["buy_price"], float)
    assert isinstance(row["realized_pnl"], float)
    assert isinstance(row["realized_pnl_pct"], float)
    rows = await repo.list_closed_positions(pg_session, "u1")
    match = next(r for r in rows if r["ticker"] == "DLF.NS")
    assert match["realized_pnl"] == 1074.8
    assert isinstance(match["buy_price"], float)

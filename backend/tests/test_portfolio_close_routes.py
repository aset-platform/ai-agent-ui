"""Tests for POST /portfolio/{ticker}/close + GET /portfolio/closed."""
import pandas as pd
import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock, patch


def _holdings_df():
    return pd.DataFrame([{
        "ticker": "DLF.NS", "quantity": 45.0, "avg_price": 746.26,
        "currency": "INR", "market": "india", "invested": 33581.7,
    }])


@pytest.mark.asyncio
async def test_close_full_position_records_realized_pnl():
    from auth.endpoints import ticker_routes as tr

    holdings = _holdings_df()
    stock_repo = MagicMock()
    stock_repo.get_portfolio_holdings.return_value = holdings
    added = {}

    def _add(txn):
        added.update(txn)
    stock_repo.add_portfolio_transaction.side_effect = _add

    closed_rows = []

    async def _add_closed(session, data):
        data = {**data, "id": "c1"}
        closed_rows.append(data)
        return data

    user = MagicMock(user_id="u1")
    close_repo_target = (
        "auth.repo.portfolio_close_repo.add_closed_position"
    )
    with patch.object(tr, "_get_stock_repo", return_value=stock_repo), \
         patch(close_repo_target, _add_closed), \
         patch.object(tr, "_close_session_scope") as scope, \
         patch.object(tr, "_invalidate_portfolio_caches") as inval:
        scope.return_value.__aenter__.return_value = MagicMock()
        resp = await tr.close_portfolio_position(
            "DLF.NS",
            tr.ClosePositionRequest(quantity=45, sell_price=800.0,
                                    sell_date="2026-08-12"),
            user=user,
        )

    assert added["side"] == "SELL" and added["quantity"] == 45
    # realized = (800 - 746.26) * 45 = 2418.30
    assert round(resp["realized_pnl"], 2) == 2418.30
    assert closed_rows[0]["buy_price"] == 746.26
    assert resp["closed_id"] == "c1"
    inval.assert_called_once_with("u1")


@pytest.mark.asyncio
async def test_close_partial_position_uses_requested_qty():
    from auth.endpoints import ticker_routes as tr

    holdings = _holdings_df()
    stock_repo = MagicMock()
    stock_repo.get_portfolio_holdings.return_value = holdings
    added = {}

    def _add(txn):
        added.update(txn)
    stock_repo.add_portfolio_transaction.side_effect = _add

    closed_rows = []

    async def _add_closed(session, data):
        data = {**data, "id": "c2"}
        closed_rows.append(data)
        return data

    user = MagicMock(user_id="u1")
    close_repo_target = (
        "auth.repo.portfolio_close_repo.add_closed_position"
    )
    with patch.object(tr, "_get_stock_repo", return_value=stock_repo), \
         patch(close_repo_target, _add_closed), \
         patch.object(tr, "_close_session_scope") as scope, \
         patch.object(tr, "_invalidate_portfolio_caches"):
        scope.return_value.__aenter__.return_value = MagicMock()
        resp = await tr.close_portfolio_position(
            "DLF.NS",
            tr.ClosePositionRequest(quantity=20, sell_price=800.0,
                                    sell_date="2026-08-12"),
            user=user,
        )

    assert added["side"] == "SELL" and added["quantity"] == 20
    # realized = (800 - 746.26) * 20 = 1074.80
    assert round(resp["realized_pnl"], 2) == 1074.80
    assert closed_rows[0]["quantity"] == 20


@pytest.mark.asyncio
async def test_close_position_zero_quantity_returns_400():
    from auth.endpoints import ticker_routes as tr

    holdings = _holdings_df()
    stock_repo = MagicMock()
    stock_repo.get_portfolio_holdings.return_value = holdings
    user = MagicMock(user_id="u1")

    with patch.object(tr, "_get_stock_repo", return_value=stock_repo):
        with pytest.raises(HTTPException) as exc_info:
            await tr.close_portfolio_position(
                "DLF.NS",
                tr.ClosePositionRequest(quantity=0, sell_price=800.0,
                                        sell_date="2026-08-12"),
                user=user,
            )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_close_position_over_open_qty_returns_400():
    from auth.endpoints import ticker_routes as tr

    holdings = _holdings_df()
    stock_repo = MagicMock()
    stock_repo.get_portfolio_holdings.return_value = holdings
    user = MagicMock(user_id="u1")

    with patch.object(tr, "_get_stock_repo", return_value=stock_repo):
        with pytest.raises(HTTPException) as exc_info:
            await tr.close_portfolio_position(
                "DLF.NS",
                tr.ClosePositionRequest(quantity=100, sell_price=800.0,
                                        sell_date="2026-08-12"),
                user=user,
            )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_close_position_unknown_ticker_returns_404():
    from auth.endpoints import ticker_routes as tr

    holdings = _holdings_df()
    stock_repo = MagicMock()
    stock_repo.get_portfolio_holdings.return_value = holdings
    user = MagicMock(user_id="u1")

    with patch.object(tr, "_get_stock_repo", return_value=stock_repo):
        with pytest.raises(HTTPException) as exc_info:
            await tr.close_portfolio_position(
                "NOPE.NS",
                tr.ClosePositionRequest(quantity=1, sell_price=800.0,
                                        sell_date="2026-08-12"),
                user=user,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_closed_positions_returns_rows_and_totals():
    from auth.endpoints import ticker_routes as tr

    rows = [
        {"id": "c1", "ticker": "DLF.NS", "realized_pnl": 2418.30},
        {"id": "c2", "ticker": "TCS.NS", "realized_pnl": -100.0},
    ]

    async def _list(session, user_id):
        return rows

    user = MagicMock(user_id="u1")
    with patch("auth.repo.portfolio_close_repo.list_closed_positions",
               _list), \
         patch.object(tr, "_close_session_scope") as scope:
        scope.return_value.__aenter__.return_value = MagicMock()
        resp = await tr.list_closed_positions(user=user)

    assert resp["closed"] == rows
    assert round(resp["totals"]["realized_pnl"], 2) == 2318.30

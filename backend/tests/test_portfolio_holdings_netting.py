"""Tests for BUY-SELL netting in get_portfolio_holdings."""

import pandas as pd
from unittest.mock import patch

from stocks.repository import StockRepository


def test_sell_reduces_net_quantity():
    rows = pd.DataFrame(
        [
            {
                "user_id": "u1",
                "ticker": "DLF.NS",
                "side": "BUY",
                "quantity": 45.0,
                "price": 746.26,
                "currency": "INR",
                "market": "india",
            },
            {
                "user_id": "u1",
                "ticker": "DLF.NS",
                "side": "SELL",
                "quantity": 20.0,
                "price": 800.0,
                "currency": "INR",
                "market": "india",
            },
        ]
    )
    repo = StockRepository.__new__(StockRepository)  # skip heavy __init__
    with patch.object(
        StockRepository, "_table_to_df", return_value=rows
    ), patch(
        "backend.db.duckdb_engine.query_iceberg_df",
        side_effect=Exception("force fallback"),
    ), patch.object(
        StockRepository, "_load_table", side_effect=Exception("force df path")
    ):
        out = repo.get_portfolio_holdings("u1")
    r = out[out["ticker"] == "DLF.NS"].iloc[0]
    assert float(r["quantity"]) == 25.0  # 45 - 20
    assert abs(float(r["avg_price"]) - 746.26) < 1e-6  # avg from BUY only


def test_buy_only_ticker_unchanged():
    rows = pd.DataFrame(
        [
            {
                "user_id": "u1",
                "ticker": "INFY.NS",
                "side": "BUY",
                "quantity": 10.0,
                "price": 1500.0,
                "currency": "INR",
                "market": "india",
            },
            {
                "user_id": "u1",
                "ticker": "INFY.NS",
                "side": "BUY",
                "quantity": 5.0,
                "price": 1600.0,
                "currency": "INR",
                "market": "india",
            },
        ]
    )
    repo = StockRepository.__new__(StockRepository)
    with patch.object(
        StockRepository, "_table_to_df", return_value=rows
    ), patch(
        "backend.db.duckdb_engine.query_iceberg_df",
        side_effect=Exception("force fallback"),
    ), patch.object(
        StockRepository, "_load_table", side_effect=Exception("force df path")
    ):
        out = repo.get_portfolio_holdings("u1")
    r = out[out["ticker"] == "INFY.NS"].iloc[0]
    expected_qty = 15.0
    expected_invested = 10.0 * 1500.0 + 5.0 * 1600.0
    expected_avg = expected_invested / expected_qty
    assert float(r["quantity"]) == expected_qty
    assert abs(float(r["avg_price"]) - expected_avg) < 1e-6
    assert abs(float(r["invested"]) - expected_invested) < 1e-6

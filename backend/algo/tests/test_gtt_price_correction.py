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

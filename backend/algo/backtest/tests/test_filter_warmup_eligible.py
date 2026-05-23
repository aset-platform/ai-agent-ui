"""Tests for filter_warmup_eligible (ASETPLTFRM-433)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from backend.algo.backtest.universe import filter_warmup_eligible


_FIRST_BARS = [
    {"ticker": "OLD.NS", "first_bar": date(2010, 1, 1)},
    {"ticker": "MED.NS", "first_bar": date(2024, 1, 1)},
    {"ticker": "NEW.NS", "first_bar": date(2026, 3, 1)},
    {"ticker": "FRESH.NS", "first_bar": date(2026, 5, 1)},
]


def test_filter_drops_tickers_with_insufficient_history():
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=_FIRST_BARS,
    ):
        kept = filter_warmup_eligible(
            ["OLD.NS", "MED.NS", "NEW.NS", "FRESH.NS"],
            period_start=date(2026, 5, 15),
            warmup_days=200,
        )
    # 2026-05-15 minus 200 calendar days = 2025-10-27.
    # OLD (2010) + MED (2024) are before that → kept.
    # NEW (2026-03) + FRESH (2026-05) are after → dropped.
    assert kept == ["OLD.NS", "MED.NS"]


def test_filter_no_op_when_warmup_zero():
    # Market-level / regime-only strategies have warmup=0;
    # filter must short-circuit (don't hit Iceberg, don't drop).
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
    ) as q:
        kept = filter_warmup_eligible(
            ["A.NS", "B.NS"],
            period_start=date(2026, 5, 15),
            warmup_days=0,
        )
    assert kept == ["A.NS", "B.NS"]
    assert q.call_count == 0


def test_filter_empty_tickers_returns_empty():
    kept = filter_warmup_eligible(
        [],
        period_start=date(2026, 5, 15),
        warmup_days=200,
    )
    assert kept == []


def test_filter_drops_tickers_absent_from_ohlcv():
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=_FIRST_BARS,
    ):
        kept = filter_warmup_eligible(
            ["OLD.NS", "GHOST.NS"],
            period_start=date(2026, 5, 15),
            warmup_days=200,
        )
    # GHOST.NS not in the first-bar map → dropped (no bars to feed).
    assert kept == ["OLD.NS"]


def test_filter_keeps_all_when_iceberg_empty():
    # Test isolation / fresh dev box — empty OHLCV table means
    # the filter logs a warning and passes all tickers through
    # rather than wiping the universe.
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[],
    ):
        kept = filter_warmup_eligible(
            ["A.NS", "B.NS"],
            period_start=date(2026, 5, 15),
            warmup_days=200,
        )
    assert kept == ["A.NS", "B.NS"]


def test_filter_at_exact_boundary_is_inclusive():
    # cutoff = 2026-05-15 - 200d = 2025-10-27.
    # Ticker first-bar exactly on the cutoff is eligible.
    with patch(
        "backend.db.duckdb_engine.query_iceberg_table",
        return_value=[
            {"ticker": "EDGE.NS", "first_bar": date(2025, 10, 27)},
        ],
    ):
        kept = filter_warmup_eligible(
            ["EDGE.NS"],
            period_start=date(2026, 5, 15),
            warmup_days=200,
        )
    assert kept == ["EDGE.NS"]

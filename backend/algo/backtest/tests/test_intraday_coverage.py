from datetime import date
from unittest.mock import patch

from backend.algo.backtest.coverage import (
    TickerCoverage,
    intraday_coverage,
)

_ROWS = [
    {"ticker": "A.NS", "interval_sec": 900, "min_d": date(2022, 6, 1),
     "max_d": date(2026, 6, 25), "days": 1008},
    {"ticker": "B.NS", "interval_sec": 900, "min_d": date(2024, 1, 1),
     "max_d": date(2026, 6, 25), "days": 600},
    {"ticker": "B.NS", "interval_sec": 300, "min_d": date(2025, 1, 1),
     "max_d": date(2026, 6, 25), "days": 300},
]


def test_finest_interval_and_absent_ticker():
    with patch(
        "backend.algo.backtest.coverage.query_iceberg_table",
        return_value=_ROWS,
    ) as q:
        cov = intraday_coverage(
            tickers=["A.NS", "B.NS", "C.NS"],
            period_start=date(2022, 1, 1),
            period_end=date(2026, 6, 30),
        )
    # A: only 15m
    assert cov["A.NS"].finest_interval_sec == 900
    assert cov["A.NS"].trading_days == 1008
    # B: has 5m AND 15m -> finest is 300
    assert cov["B.NS"].finest_interval_sec == 300
    # C: absent from table -> None coverage
    assert cov["C.NS"] == TickerCoverage("C.NS", None, None, None, 0)
    # batched single query (no per-ticker loop)
    assert q.call_count == 1


def test_empty_tickers_no_query():
    with patch(
        "backend.algo.backtest.coverage.query_iceberg_table",
    ) as q:
        assert intraday_coverage(
            tickers=[], period_start=date(2022, 1, 1),
            period_end=date(2022, 2, 1),
        ) == {}
        q.assert_not_called()

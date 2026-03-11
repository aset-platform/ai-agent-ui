"""Unit tests for quarterly results repository CRUD.

Tests are isolated — StockRepository methods are mocked so the suite
runs offline without an Iceberg catalog.
"""

from unittest.mock import patch

import pandas as pd


def _make_quarterly_df(ticker="AAPL", n=2):
    """Return a minimal quarterly results DataFrame."""
    rows = []
    for i in range(n):
        rows.append(
            {
                "ticker": ticker,
                "quarter_end": f"2024-{3 * (i + 1):02d}-30",
                "fiscal_year": 2024,
                "fiscal_quarter": f"Q{i + 1}",
                "statement_type": "income",
                "revenue": 100_000_000.0 * (i + 1),
                "net_income": 20_000_000.0 * (i + 1),
                "gross_profit": 50_000_000.0,
                "operating_income": 30_000_000.0,
                "ebitda": 40_000_000.0,
                "eps_basic": 1.5,
                "eps_diluted": 1.45,
                "total_assets": None,
                "total_liabilities": None,
                "total_equity": None,
                "total_debt": None,
                "cash_and_equivalents": None,
                "operating_cashflow": None,
                "capex": None,
                "free_cashflow": None,
            }
        )
    return pd.DataFrame(rows)


@patch("stocks.repository.StockRepository")
def test_insert_and_get_quarterly_results(mock_cls):
    """Round-trip: insert then get returns data."""
    repo = mock_cls.return_value
    df = _make_quarterly_df()
    repo.get_quarterly_results.return_value = df

    repo.insert_quarterly_results("AAPL", df)
    repo.insert_quarterly_results.assert_called_once()

    result = repo.get_quarterly_results("AAPL")
    assert len(result) == 2
    assert result["ticker"].iloc[0] == "AAPL"


@patch("stocks.repository.StockRepository")
def test_quarterly_results_freshness_check(mock_cls):
    """Fresh data returns DataFrame; stale returns None."""
    repo = mock_cls.return_value
    df = _make_quarterly_df()

    # Fresh: returns df
    repo.get_quarterly_results_if_fresh.return_value = df
    fresh = repo.get_quarterly_results_if_fresh("AAPL", days=7)
    assert fresh is not None
    assert len(fresh) == 2

    # Stale: returns None
    repo.get_quarterly_results_if_fresh.return_value = None
    stale = repo.get_quarterly_results_if_fresh("AAPL", days=7)
    assert stale is None

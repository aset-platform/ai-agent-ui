"""Unit tests for the fetch_quarterly_results backend tool.

yfinance and Iceberg are fully mocked for offline execution.
"""

from unittest.mock import MagicMock, patch

import pandas as pd


def _fake_income_stmt():
    """Return a minimal quarterly income statement."""
    dates = pd.to_datetime(["2024-03-31", "2024-06-30"])
    return pd.DataFrame(
        {
            dates[0]: [100e6, 20e6, 50e6],
            dates[1]: [110e6, 25e6, 55e6],
        },
        index=[
            "Total Revenue",
            "Net Income",
            "Gross Profit",
        ],
    )


def _fake_balance_sheet():
    """Return a minimal quarterly balance sheet."""
    dates = pd.to_datetime(["2024-03-31", "2024-06-30"])
    return pd.DataFrame(
        {
            dates[0]: [500e6, 300e6, 200e6],
            dates[1]: [520e6, 310e6, 210e6],
        },
        index=[
            "Total Assets",
            "Total Liabilities Net Minority Interest",
            "Stockholders Equity",
        ],
    )


@patch("tools.stock_data_tool._require_repo")
@patch("tools.stock_data_tool.yf")
def test_fetch_quarterly_results_success(mock_yf, mock_repo_fn):
    """Successful fetch: yfinance called, Iceberg insert called."""
    from tools.stock_data_tool import (
        fetch_quarterly_results,
    )

    repo = MagicMock()
    repo.get_quarterly_results_if_fresh.return_value = None
    mock_repo_fn.return_value = repo

    ticker_obj = MagicMock()
    ticker_obj.quarterly_income_stmt = _fake_income_stmt()
    ticker_obj.quarterly_balance_sheet = _fake_balance_sheet()
    ticker_obj.quarterly_cashflow = pd.DataFrame()
    ticker_obj.cashflow = pd.DataFrame()  # no annual either
    mock_yf.Ticker.return_value = ticker_obj

    result = fetch_quarterly_results.invoke({"ticker": "AAPL"})

    assert "AAPL" in result
    assert "Fetched" in result
    # Cashflow was empty => gap reported
    assert "cashflow (no data)" in result
    repo.insert_quarterly_results.assert_called_once()
    call_args = repo.insert_quarterly_results.call_args
    assert call_args[0][0] == "AAPL"
    assert len(call_args[0][1]) > 0


@patch("tools.stock_data_tool._require_repo")
@patch("tools.stock_data_tool.yf")
def test_fetch_quarterly_results_fresh_skip(mock_yf, mock_repo_fn):
    """When data is fresh, yfinance is not called."""
    from tools.stock_data_tool import (
        fetch_quarterly_results,
    )

    repo = MagicMock()
    repo.get_quarterly_results_if_fresh.return_value = pd.DataFrame(
        {"ticker": ["AAPL"]}
    )
    mock_repo_fn.return_value = repo

    result = fetch_quarterly_results.invoke({"ticker": "AAPL"})

    assert "up-to-date" in result
    mock_yf.Ticker.assert_not_called()


def _fake_all_null_balance_sheet():
    """Balance sheet where all mapped metrics are NaN."""
    import numpy as np

    dates = pd.to_datetime(["2025-09-30"])
    return pd.DataFrame(
        {dates[0]: [np.nan, np.nan, np.nan]},
        index=[
            "Total Assets",
            "Total Liabilities Net Minority Interest",
            "Stockholders Equity",
        ],
    )


def test_extract_statement_skips_all_null_rows():
    """Rows with all-null metrics are excluded."""
    from tools.stock_data_tool import (
        _BALANCE_MAP,
        _extract_statement,
    )

    rows = _extract_statement(
        _fake_all_null_balance_sheet(),
        _BALANCE_MAP,
        "balance",
        "TEST",
    )
    assert rows == [], "Expected empty list for all-null quarter"


def test_extract_statement_keeps_partial_rows():
    """Rows with at least one non-null metric are kept."""
    from tools.stock_data_tool import (
        _BALANCE_MAP,
        _extract_statement,
    )

    rows = _extract_statement(
        _fake_balance_sheet(),
        _BALANCE_MAP,
        "balance",
        "TEST",
    )
    assert len(rows) == 2
    assert rows[0]["total_assets"] == 500e6


def _fake_annual_cashflow():
    """Return a minimal annual cashflow statement."""
    dates = pd.to_datetime(["2025-03-31", "2024-03-31"])
    return pd.DataFrame(
        {
            dates[0]: [150e9, -50e9, 100e9],
            dates[1]: [120e9, -40e9, 80e9],
        },
        index=[
            "Operating Cash Flow",
            "Capital Expenditure",
            "Free Cash Flow",
        ],
    )


@patch("tools.stock_data_tool._require_repo")
@patch("tools.stock_data_tool.yf")
def test_fetch_reports_per_statement_counts(mock_yf, mock_repo_fn):
    """Return message includes per-statement counts."""
    from tools.stock_data_tool import (
        fetch_quarterly_results,
    )

    repo = MagicMock()
    repo.get_quarterly_results_if_fresh.return_value = None
    mock_repo_fn.return_value = repo

    ticker_obj = MagicMock()
    ticker_obj.quarterly_income_stmt = _fake_income_stmt()
    ticker_obj.quarterly_balance_sheet = _fake_balance_sheet()
    ticker_obj.quarterly_cashflow = pd.DataFrame()
    ticker_obj.cashflow = pd.DataFrame()  # no annual either
    mock_yf.Ticker.return_value = ticker_obj

    result = fetch_quarterly_results.invoke({"ticker": "RELIANCE.NS"})

    assert "income: 2q" in result
    assert "balance: 2q" in result
    assert "Gaps:" in result
    assert "cashflow (no data)" in result


@patch("tools.stock_data_tool._require_repo")
@patch("tools.stock_data_tool.yf")
def test_annual_cashflow_fallback(mock_yf, mock_repo_fn):
    """When quarterly cashflow is empty, annual is used."""
    from tools.stock_data_tool import (
        fetch_quarterly_results,
    )

    repo = MagicMock()
    repo.get_quarterly_results_if_fresh.return_value = None
    mock_repo_fn.return_value = repo

    ticker_obj = MagicMock()
    ticker_obj.quarterly_income_stmt = _fake_income_stmt()
    ticker_obj.quarterly_balance_sheet = _fake_balance_sheet()
    ticker_obj.quarterly_cashflow = pd.DataFrame()
    ticker_obj.cashflow = _fake_annual_cashflow()
    mock_yf.Ticker.return_value = ticker_obj

    result = fetch_quarterly_results.invoke({"ticker": "RELIANCE.NS"})

    assert "cashflow: 2q" in result
    assert "annual fallback" in result

    # Verify FY quarter label in stored data
    call_df = repo.insert_quarterly_results.call_args[0][1]
    cf_rows = call_df[call_df["statement_type"] == "cashflow"]
    assert len(cf_rows) == 2
    assert (cf_rows["fiscal_quarter"] == "FY").all()

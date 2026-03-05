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
    mock_yf.Ticker.return_value = ticker_obj

    result = fetch_quarterly_results.invoke({"ticker": "AAPL"})

    assert "AAPL" in result
    assert "Fetched" in result
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

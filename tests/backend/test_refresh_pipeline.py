"""Tests for the refresh pipeline wiring (ASETPLTFRM-201).

Covers:
- Market indices stored via insert_ohlcv (not market_indices)
- Sentiment step in run_full_refresh
- Regressors loaded from OHLCV table
"""

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd


def _make_ohlcv_df(
    rows: int = 50,
    ticker: str = "^VIX",
) -> pd.DataFrame:
    """Minimal OHLCV DataFrame with DatetimeIndex."""
    idx = pd.date_range(
        "2024-01-01",
        periods=rows,
        freq="B",
    )
    rng = np.random.default_rng(42)
    close = 20 + rng.standard_normal(rows).cumsum()
    df = pd.DataFrame(
        {
            "Open": close * 0.99,
            "High": close * 1.01,
            "Low": close * 0.98,
            "Close": close,
            "Adj Close": close,
            "Volume": rng.integers(1000, 5000, rows),
        },
        index=idx,
    )
    df.index.name = "Date"
    return df


def _make_iceberg_ohlcv(
    rows: int = 50,
    ticker: str = "^VIX",
) -> pd.DataFrame:
    """DataFrame shaped like repo.get_ohlcv() output."""
    idx = pd.date_range(
        "2024-01-01",
        periods=rows,
        freq="B",
    )
    rng = np.random.default_rng(42)
    close = 20 + rng.standard_normal(rows).cumsum()
    return pd.DataFrame(
        {
            "ticker": [ticker] * rows,
            "date": idx.date,
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "adj_close": close,
            "volume": rng.integers(1000, 5000, rows),
        }
    )


# ── refresh_market_indices ─────────────────────────────


@patch("tools._stock_shared._require_repo")
@patch("yfinance.Ticker")
def test_refresh_market_indices_uses_insert_ohlcv(
    mock_ticker_cls,
    mock_require,
):
    """Market indices should be inserted via insert_ohlcv,
    not _append_rows('stocks.market_indices')."""
    repo = MagicMock()
    repo.get_latest_ohlcv_date.return_value = None
    repo.insert_ohlcv.return_value = 10
    mock_require.return_value = repo

    hist = _make_ohlcv_df(rows=10, ticker="^VIX")
    mock_ticker_cls.return_value.history.return_value = hist

    from jobs.gap_filler import refresh_market_indices

    total = refresh_market_indices()

    assert total > 0
    # Must use insert_ohlcv, not _append_rows.
    repo.insert_ohlcv.assert_called()
    repo._append_rows.assert_not_called()
    # Must use get_latest_ohlcv_date, not
    # get_market_index_series.
    repo.get_latest_ohlcv_date.assert_called()
    repo.get_market_index_series.assert_not_called()


@patch("tools._stock_shared._require_repo")
@patch("yfinance.Ticker")
def test_refresh_market_indices_empty_history(
    mock_ticker_cls,
    mock_require,
):
    """Returns 0 when yfinance returns empty data."""
    repo = MagicMock()
    repo.get_latest_ohlcv_date.return_value = None
    mock_require.return_value = repo

    mock_ticker_cls.return_value.history.return_value = pd.DataFrame()

    from jobs.gap_filler import refresh_market_indices

    total = refresh_market_indices()
    assert total == 0


# ── _load_regressors_from_iceberg ──────────────────────


@patch("tools._forecast_shared._require_repo")
def test_load_regressors_reads_ohlcv(mock_require):
    """Regressors should be loaded from get_ohlcv,
    not get_market_index_series."""
    repo = MagicMock()
    vix = _make_iceberg_ohlcv(50, "^VIX")
    gspc = _make_iceberg_ohlcv(50, "^GSPC")
    repo.get_ohlcv.side_effect = lambda t, **kw: (vix if t == "^VIX" else gspc)
    repo.get_sentiment_series.return_value = pd.DataFrame()
    mock_require.return_value = repo

    prophet_df = pd.DataFrame(
        {
            "ds": pd.date_range(
                "2024-01-01",
                periods=50,
                freq="B",
            ),
            "y": range(50),
        }
    )

    from tools._forecast_shared import (
        _load_regressors_from_iceberg,
    )

    result = _load_regressors_from_iceberg(
        "AAPL",
        prophet_df,
    )

    assert result is not None
    assert "vix" in result.columns
    assert "index_return" in result.columns
    # Must NOT call the old market_indices method.
    repo.get_market_index_series.assert_not_called()


# ── refresh pipeline integration ───────────────────────


@patch("tools._stock_shared._require_repo")
def test_sentiment_failure_noncritical(mock_repo_fn):
    """Sentiment failure must not abort the pipeline."""
    repo = MagicMock()
    repo.get_latest_ohlcv_date.return_value = date.today()
    repo.get_latest_forecast_run.return_value = {
        "run_date": date.today(),
        "mae": 1.0,
        "rmse": 2.0,
        "mape": 5.0,
        "sentiment": "Bullish",
        "current_price_at_run": 100.0,
    }
    mock_repo_fn.return_value = repo

    with (
        patch(
            "jobs.gap_filler.refresh_sentiment",
            side_effect=RuntimeError("LLM down"),
        ),
        patch(
            "jobs.gap_filler.refresh_market_indices",
            return_value=5,
        ),
    ):
        from dashboard.services.stock_refresh import (
            run_full_refresh,
        )

        result = run_full_refresh("AAPL")

    # Pipeline should still succeed (forecast cached).
    assert result.success is True
    step_names = [s["name"] for s in result.steps]
    assert "Sentiment" in step_names
    sent_step = next(s for s in result.steps if s["name"] == "Sentiment")
    assert sent_step["ok"] is False


@patch("tools._stock_shared._require_repo")
def test_full_refresh_includes_new_steps(mock_repo_fn):
    """run_full_refresh should include Market indices
    and Sentiment steps."""
    repo = MagicMock()
    repo.get_latest_ohlcv_date.return_value = date.today()
    repo.get_latest_forecast_run.return_value = {
        "run_date": date.today(),
        "mae": 1.0,
        "rmse": 2.0,
        "mape": 5.0,
        "sentiment": "Neutral",
        "current_price_at_run": 100.0,
    }
    mock_repo_fn.return_value = repo

    with (
        patch(
            "jobs.gap_filler.refresh_sentiment",
            return_value=0.35,
        ),
        patch(
            "jobs.gap_filler.refresh_market_indices",
            return_value=8,
        ),
    ):
        from dashboard.services.stock_refresh import (
            run_full_refresh,
        )

        result = run_full_refresh("AAPL")

    step_names = [s["name"] for s in result.steps]
    assert "Market indices" in step_names
    assert "Sentiment" in step_names

    mi_step = next(s for s in result.steps if s["name"] == "Market indices")
    assert mi_step["ok"] is True

    sent_step = next(s for s in result.steps if s["name"] == "Sentiment")
    assert sent_step["ok"] is True
    assert "0.35" in sent_step["message"]


# ── macro regressors (Phase 3) ────────────────────────


@patch("tools._stock_shared._require_repo")
@patch("yfinance.Ticker")
def test_refresh_market_indices_includes_macro(
    mock_ticker_cls,
    mock_require,
):
    """refresh_market_indices should fetch macro symbols
    (^TNX, ^IRX, CL=F, DX-Y.NYB) alongside indices."""
    repo = MagicMock()
    repo.get_latest_ohlcv_date.return_value = None
    repo.insert_ohlcv.return_value = 5
    mock_require.return_value = repo

    hist = _make_ohlcv_df(rows=5)
    mock_ticker_cls.return_value.history.return_value = hist

    # Reset daily flag so it actually runs.
    import jobs.gap_filler as gf

    gf._indices_last_refresh = None

    from jobs.gap_filler import refresh_market_indices

    refresh_market_indices()

    # Collect all tickers passed to insert_ohlcv.
    called_tickers = [c.args[0] for c in repo.insert_ohlcv.call_args_list]
    assert "^TNX" in called_tickers
    assert "^IRX" in called_tickers
    assert "CL=F" in called_tickers
    assert "DX-Y.NYB" in called_tickers


@patch("tools._forecast_shared._require_repo")
def test_load_regressors_includes_macro(mock_require):
    """Regressors should include macro columns when
    macro data exists in OHLCV."""
    repo = MagicMock()

    def _get_ohlcv(ticker, **kw):
        if ticker in (
            "^VIX",
            "^GSPC",
            "^TNX",
            "^IRX",
            "CL=F",
            "DX-Y.NYB",
        ):
            return _make_iceberg_ohlcv(50, ticker)
        return pd.DataFrame()

    repo.get_ohlcv.side_effect = _get_ohlcv
    repo.get_sentiment_series.return_value = pd.DataFrame()
    mock_require.return_value = repo

    prophet_df = pd.DataFrame(
        {
            "ds": pd.date_range(
                "2024-01-01",
                periods=50,
                freq="B",
            ),
            "y": range(50),
        }
    )

    from tools._forecast_shared import (
        _load_regressors_from_iceberg,
    )

    result = _load_regressors_from_iceberg(
        "AAPL",
        prophet_df,
    )

    assert result is not None
    # Phase 2 columns.
    assert "vix" in result.columns
    assert "index_return" in result.columns
    # Phase 3 macro columns.
    assert "treasury_10y" in result.columns
    assert "yield_spread" in result.columns
    assert "oil_price" in result.columns
    assert "dollar_index" in result.columns

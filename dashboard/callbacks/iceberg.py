"""Iceberg repository singleton and cached helpers for the Dashboard.

Provides a lazy-initialised singleton accessor for the
:class:`~stocks.repository.StockRepository` used by all dashboard callbacks.
Also provides TTL-cached helpers for OHLCV, forecast, analysis summary, and
company info reads so that multiple callbacks sharing the same data within a
refresh cycle do not duplicate Iceberg scans.

Example::

    from dashboard.callbacks.iceberg import _get_iceberg_repo, _get_ohlcv_cached
    repo = _get_iceberg_repo()
    df = _get_ohlcv_cached(repo, "AAPL")
"""

import logging
import os
import sys
import time as _time
from pathlib import Path
from typing import Optional

import pandas as pd

# Module-level logger — must remain module-level for use outside any class scope
_logger = logging.getLogger(__name__)

# Ensure project root on sys.path before stocks import
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Ensure PyIceberg can find the catalog regardless of CWD.
# PyIceberg's _ENV_CONFIG singleton reads env vars once at import time,
# so these must be set BEFORE any pyiceberg import.
os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__URI",
    f"sqlite:///{_PROJECT_ROOT.resolve()}/data/iceberg/catalog.db",
)
os.environ.setdefault(
    "PYICEBERG_CATALOG__LOCAL__WAREHOUSE",
    f"file:///{_PROJECT_ROOT.resolve()}/data/iceberg/warehouse",
)

# Fix #10: TTL-based singleton — re-initialises after 1 h to survive Iceberg restarts
_DASH_REPO = None
_DASH_REPO_EXPIRY: float = 0.0
_DASH_REPO_TTL = 3600  # 1 hour

# Fix #6: TTL caches for expensive shared Iceberg reads (5-min TTL)
_SHARED_TTL = 300
_SUMMARY_CACHE: dict = {"data": None, "expiry": 0.0}
_COMPANY_CACHE: dict = {"data": None, "expiry": 0.0}
_FILLED_SUMMARY_CACHE: dict = {"data": None, "expiry": 0.0}
_OHLCV_CACHE: dict = {}  # {ticker: (df, expiry_monotonic)}
_FORECAST_CACHE: dict = {}  # {(ticker, horizon): (df, expiry_monotonic)}
_DIVIDENDS_CACHE: dict = {}  # {ticker: (df, expiry_monotonic)}


def _get_iceberg_repo() -> Optional[object]:
    """Return the module-level :class:`~stocks.repository.StockRepository` singleton.

    Re-initialised after ``_DASH_REPO_TTL`` seconds so the dashboard can
    recover automatically after an Iceberg catalog restart without requiring
    a full process restart.

    Returns:
        :class:`~stocks.repository.StockRepository` instance or ``None``.
    """
    global _DASH_REPO, _DASH_REPO_EXPIRY
    now = _time.monotonic()
    if _DASH_REPO is not None and now < _DASH_REPO_EXPIRY:
        return _DASH_REPO
    try:
        from stocks.repository import StockRepository  # noqa: PLC0415

        _DASH_REPO = StockRepository()
        _DASH_REPO_EXPIRY = now + _DASH_REPO_TTL
        _logger.debug("StockRepository initialised for dashboard")
    except Exception as _e:
        _logger.warning("StockRepository unavailable in dashboard: %s", _e)
        _DASH_REPO = None
    return _DASH_REPO


# ------------------------------------------------------------------
# OHLCV cached helper
# ------------------------------------------------------------------


def _get_ohlcv_cached(repo: object, ticker: str) -> Optional[pd.DataFrame]:
    """Return OHLCV data for *ticker* from Iceberg, cached for ``_SHARED_TTL`` seconds.

    The returned DataFrame has a DatetimeIndex and columns ``Open``, ``High``,
    ``Low``, ``Close``, ``Adj Close``, ``Volume`` — matching the shape produced
    by ``pd.read_parquet(data/raw/{TICKER}_raw.parquet)`` so that all existing
    consumers work unchanged.

    Args:
        repo: Active :class:`~stocks.repository.StockRepository` instance.
        ticker: Uppercase ticker symbol (e.g. ``"AAPL"``).

    Returns:
        DataFrame with DatetimeIndex and OHLCV columns, or ``None`` if no data.
    """
    now = _time.monotonic()
    entry = _OHLCV_CACHE.get(ticker)
    if entry and entry[1] > now:
        return entry[0]

    df = repo.get_ohlcv(ticker)
    if df.empty:
        _OHLCV_CACHE[ticker] = (None, now + _SHARED_TTL)
        return None

    # Reshape Iceberg columns to match parquet format
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    result = pd.DataFrame(
        {
            "Open": df["open"],
            "High": df["high"],
            "Low": df["low"],
            "Close": df["close"],
            "Adj Close": (
                df["adj_close"]
                if (
                    "adj_close" in df.columns
                    and df["adj_close"].notna().mean() > 0.5
                )
                else df["close"]
            ),
            "Volume": df["volume"],
        }
    )
    result.index.name = "Date"

    _OHLCV_CACHE[ticker] = (result, now + _SHARED_TTL)
    return result


# ------------------------------------------------------------------
# Forecast cached helper
# ------------------------------------------------------------------


def _get_forecast_cached(
    repo: object,
    ticker: str,
    horizon_months: int,
) -> Optional[pd.DataFrame]:
    """Return the latest forecast series for *ticker* from Iceberg.

    Cached for ``_SHARED_TTL`` seconds.  The returned DataFrame has columns
    ``ds``, ``yhat``, ``yhat_lower``, ``yhat_upper`` — matching the shape
    produced by ``pd.read_parquet(data/forecasts/{TICKER}_{H}m_forecast.parquet)``.

    Args:
        repo: Active :class:`~stocks.repository.StockRepository` instance.
        ticker: Uppercase ticker symbol.
        horizon_months: Forecast horizon (e.g. 3, 6, or 9).

    Returns:
        DataFrame with forecast columns, or ``None`` if no forecast exists.
    """
    cache_key = (ticker, horizon_months)
    now = _time.monotonic()
    entry = _FORECAST_CACHE.get(cache_key)
    if entry and entry[1] > now:
        return entry[0]

    df = repo.get_latest_forecast_series(ticker, horizon_months)
    if df.empty:
        _FORECAST_CACHE[cache_key] = (None, now + _SHARED_TTL)
        return None

    # Reshape Iceberg columns to match expected format
    result = pd.DataFrame(
        {
            "ds": pd.to_datetime(df["forecast_date"]),
            "yhat": df["predicted_price"],
            "yhat_lower": df["lower_bound"],
            "yhat_upper": df["upper_bound"],
        }
    )

    _FORECAST_CACHE[cache_key] = (result, now + _SHARED_TTL)
    return result


# ------------------------------------------------------------------
# Dividends cached helper
# ------------------------------------------------------------------


def _get_dividends_cached(
    repo: object,
    ticker: str,
) -> Optional[pd.DataFrame]:
    """Return dividend history for *ticker*, cached for ``_SHARED_TTL`` s.

    Args:
        repo: Active :class:`~stocks.repository.StockRepository`.
        ticker: Uppercase ticker symbol.

    Returns:
        DataFrame with ``ex_date``, ``dividend_amount``,
        ``currency`` columns, or ``None`` if no dividends.
    """
    now = _time.monotonic()
    entry = _DIVIDENDS_CACHE.get(ticker)
    if entry and entry[1] > now:
        return entry[0]

    df = repo.get_dividends(ticker)
    if df.empty:
        _DIVIDENDS_CACHE[ticker] = (None, now + _SHARED_TTL)
        return None

    _DIVIDENDS_CACHE[ticker] = (df, now + _SHARED_TTL)
    return df


# ------------------------------------------------------------------
# Analysis summary cached helpers
# ------------------------------------------------------------------


def _get_analysis_summary_cached(repo: object):
    """Return all latest analysis summaries, cached for ``_SHARED_TTL`` seconds.

    Avoids repeated Iceberg scans when multiple callbacks (screener, risk,
    sectors) all need the same table within the same refresh cycle.

    Args:
        repo: Active :class:`~stocks.repository.StockRepository` instance.

    Returns:
        :class:`~pandas.DataFrame` of analysis summary rows.
    """
    now = _time.monotonic()
    if _SUMMARY_CACHE["data"] is not None and now < _SUMMARY_CACHE["expiry"]:
        return _SUMMARY_CACHE["data"]
    data = repo.get_all_latest_analysis_summary()
    _SUMMARY_CACHE.update({"data": data, "expiry": now + _SHARED_TTL})
    return data


def _get_company_info_cached(repo: object):
    """Return all latest company info rows, cached for ``_SHARED_TTL`` seconds.

    Args:
        repo: Active :class:`~stocks.repository.StockRepository` instance.

    Returns:
        :class:`~pandas.DataFrame` of company info rows.
    """
    now = _time.monotonic()
    if _COMPANY_CACHE["data"] is not None and now < _COMPANY_CACHE["expiry"]:
        return _COMPANY_CACHE["data"]
    data = repo.get_all_latest_company_info()
    _COMPANY_CACHE.update({"data": data, "expiry": now + _SHARED_TTL})
    return data


def _get_analysis_with_gaps_filled(repo: object) -> pd.DataFrame:
    """Return analysis summaries for all registered tickers, filling gaps.

    Reads the Iceberg ``stocks.analysis_summary`` table and identifies any
    registered tickers that are missing.  For missing tickers, analysis is
    computed on-the-fly from their Iceberg OHLCV data so that every ticker
    in the registry appears in the result.

    Cached for ``_SHARED_TTL`` seconds.

    Args:
        repo: Active :class:`~stocks.repository.StockRepository` instance.

    Returns:
        :class:`~pandas.DataFrame` of analysis summary rows covering all
        registered tickers (Iceberg + on-the-fly computed).
    """
    now = _time.monotonic()
    if (
        _FILLED_SUMMARY_CACHE["data"] is not None
        and now < _FILLED_SUMMARY_CACHE["expiry"]
    ):
        return _FILLED_SUMMARY_CACHE["data"]

    df = _get_analysis_summary_cached(repo)
    existing_tickers = set(df["ticker"].tolist()) if not df.empty else set()
    registry = repo.get_all_registry()
    missing = [t for t in sorted(registry.keys()) if t not in existing_tickers]

    if missing:
        extra_rows = []
        try:
            # backend tools use `import tools.*` internally, so
            # backend/ must be on sys.path for those imports to resolve.
            _backend_dir = str(_PROJECT_ROOT / "backend")
            if _backend_dir not in sys.path:
                sys.path.insert(0, _backend_dir)
            from tools.price_analysis_tool import (  # noqa: PLC0415
                _analyse_price_movement,
                _calculate_technical_indicators,
                _generate_summary_stats,
            )

            for ticker in missing:
                try:
                    ohlcv = _get_ohlcv_cached(repo, ticker)
                    if ohlcv is None:
                        continue
                    _df = ohlcv.copy()
                    _df.index = pd.to_datetime(_df.index).tz_localize(None)
                    _df = _calculate_technical_indicators(_df)
                    movement = _analyse_price_movement(_df)
                    stats = _generate_summary_stats(_df, ticker)
                    extra_rows.append(
                        {
                            "ticker": ticker,
                            "current_price": stats.get("current_price"),
                            "rsi_14": stats.get("rsi_14"),
                            "rsi_signal": stats.get("rsi_signal"),
                            "macd_signal_text": stats.get("macd_signal"),
                            "sma_200_signal": stats.get("sma_200_signal"),
                            "sharpe_ratio": movement.get("sharpe_ratio"),
                            "annualized_return_pct": movement.get(
                                "annualized_return_pct"
                            ),
                            "annualized_volatility_pct": movement.get(
                                "annualized_volatility_pct"
                            ),
                            "max_drawdown_pct": movement.get(
                                "max_drawdown_pct"
                            ),
                            "max_drawdown_duration_days": movement.get(
                                "max_drawdown_duration_days"
                            ),
                            "bull_phase_pct": movement.get("bull_phase_pct"),
                            "bear_phase_pct": movement.get("bear_phase_pct"),
                        }
                    )
                except Exception as _e:
                    _logger.debug(
                        "On-the-fly analysis failed for %s: %s", ticker, _e
                    )
        except Exception as _e:
            _logger.warning("On-the-fly analysis import failed: %s", _e)
        if extra_rows:
            extra_df = pd.DataFrame(extra_rows)
            df = (
                pd.concat([df, extra_df], ignore_index=True)
                if not df.empty
                else extra_df
            )

    _FILLED_SUMMARY_CACHE.update({"data": df, "expiry": now + _SHARED_TTL})
    return df


def clear_caches(ticker: str | None = None) -> None:
    """Invalidate TTL caches so subsequent reads fetch fresh Iceberg data.

    When *ticker* is provided, only entries for that ticker are evicted from
    the per-ticker caches (``_OHLCV_CACHE``, ``_FORECAST_CACHE``,
    ``_DIVIDENDS_CACHE``).  The
    global caches (``_SUMMARY_CACHE``, ``_COMPANY_CACHE``,
    ``_FILLED_SUMMARY_CACHE``) are always fully cleared because they
    aggregate data across all tickers.

    Args:
        ticker: Uppercase ticker symbol.  If ``None``, all per-ticker
            entries are cleared as well.
    """
    if ticker:
        _OHLCV_CACHE.pop(ticker, None)
        _DIVIDENDS_CACHE.pop(ticker, None)
        keys_to_drop = [k for k in _FORECAST_CACHE if k[0] == ticker]
        for k in keys_to_drop:
            _FORECAST_CACHE.pop(k, None)
    else:
        _OHLCV_CACHE.clear()
        _FORECAST_CACHE.clear()
        _DIVIDENDS_CACHE.clear()

    _SUMMARY_CACHE.update({"data": None, "expiry": 0.0})
    _COMPANY_CACHE.update({"data": None, "expiry": 0.0})
    _FILLED_SUMMARY_CACHE.update({"data": None, "expiry": 0.0})

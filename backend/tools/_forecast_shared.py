"""Shared constants, lazy Iceberg repository, and data helpers for forecasting."""

import logging
from datetime import date

import holidays as holidays_lib
import pandas as pd
from tools._stock_shared import (  # noqa: F401 — re-exported
    _get_repo,
    _require_repo,
)

# Module-level logger; required at module scope.
_logger = logging.getLogger(__name__)


# Fix #6: delegate to shared helpers module to eliminate duplication.
# Fix #5: TTL cache is implemented in _helpers._load_currency.
from tools._helpers import _currency_symbol, _load_currency  # noqa: F401,E402


def _fetch_market_regressors(
    ticker: str,
    start_date: str = "2015-01-01",
) -> pd.DataFrame | None:
    """Fetch VIX + benchmark index for *ticker*'s market.

    Returns a DataFrame with columns ``ds``, ``vix``,
    ``index_return`` aligned to trading days.  Returns
    ``None`` on failure.

    US stocks  → ^VIX + ^GSPC (S&P 500)
    India stocks → ^INDIAVIX + ^NSEI (Nifty 50)
    """
    try:
        import yfinance as yf

        if _is_indian_ticker(ticker):
            vix_sym = "^INDIAVIX"
            idx_sym = "^NSEI"
        else:
            vix_sym = "^VIX"
            idx_sym = "^GSPC"

        vix_df = yf.Ticker(vix_sym).history(
            start=start_date,
            auto_adjust=False,
        )
        idx_df = yf.Ticker(idx_sym).history(
            start=start_date,
            auto_adjust=False,
        )

        if vix_df.empty or idx_df.empty:
            _logger.warning(
                "Market regressor data empty: " "vix=%s(%d) idx=%s(%d)",
                vix_sym,
                len(vix_df),
                idx_sym,
                len(idx_df),
            )
            return None

        # Normalise index to tz-naive dates.
        vix_df.index = pd.to_datetime(
            vix_df.index,
        ).tz_localize(None)
        idx_df.index = pd.to_datetime(
            idx_df.index,
        ).tz_localize(None)

        regs = pd.DataFrame(
            {
                "ds": vix_df.index,
                "vix": vix_df["Close"].values,
            }
        )
        idx_ret = pd.DataFrame(
            {
                "ds": idx_df.index,
                "index_return": (idx_df["Close"].pct_change() * 100).values,
            }
        )
        merged = regs.merge(idx_ret, on="ds", how="outer")
        merged = merged.sort_values("ds").reset_index(
            drop=True,
        )
        merged["vix"] = merged["vix"].ffill().bfill()
        merged["index_return"] = merged["index_return"].ffill().fillna(0)
        _logger.info(
            "Market regressors: %s+%s → %d rows",
            vix_sym,
            idx_sym,
            len(merged),
        )
        return merged
    except Exception as exc:
        _logger.warning(
            "Market regressors unavailable for %s: %s",
            ticker,
            exc,
        )
        return None


def _load_regressors_from_iceberg(
    ticker: str,
    prophet_df: pd.DataFrame,
) -> pd.DataFrame | None:
    """Load pre-computed regressors from Iceberg.

    Reads VIX + index + sentiment from persisted tables.
    Falls back to live yfinance fetch if Iceberg tables
    are empty (first run before gap_filler).

    Returns a DataFrame with ``ds`` + regressor columns,
    or ``None`` if no data available.
    """
    try:
        repo = _require_repo()
        is_india = _is_indian_ticker(ticker)

        vix_sym = "^INDIAVIX" if is_india else "^VIX"
        idx_sym = "^NSEI" if is_india else "^GSPC"

        vix_df = repo.get_ohlcv(vix_sym)
        idx_df = repo.get_ohlcv(idx_sym)

        # If market indices empty, fall back to live fetch
        # but still include sentiment from Iceberg.
        if vix_df.empty or idx_df.empty:
            _logger.info(
                "Market indices empty in Iceberg, "
                "falling back to live fetch",
            )
            regressors = _fetch_market_regressors(ticker)
            # Append sentiment even with live market data.
            sent_df = repo.get_sentiment_series(ticker)
            if regressors is not None and not sent_df.empty:
                sent_reg = pd.DataFrame(
                    {
                        "ds": pd.to_datetime(
                            sent_df["score_date"],
                        ),
                        "sentiment": sent_df["avg_score"].values,
                    }
                )
                regressors = regressors.merge(
                    sent_reg,
                    on="ds",
                    how="outer",
                )
                regressors["sentiment"] = (
                    regressors["sentiment"].ffill().fillna(0)
                )
                _logger.info(
                    "Sentiment merged: %d rows",
                    len(sent_df),
                )
            return regressors

        # Build VIX regressor.
        vix_reg = pd.DataFrame(
            {
                "ds": pd.to_datetime(vix_df["date"]),
                "vix": vix_df["close"].values,
            }
        )

        # Build index return regressor.
        idx_reg = pd.DataFrame(
            {
                "ds": pd.to_datetime(idx_df["date"]),
                "index_return": (idx_df["close"].pct_change() * 100).values,
            }
        )

        regressors = vix_reg.merge(
            idx_reg,
            on="ds",
            how="outer",
        )
        regressors = regressors.sort_values(
            "ds",
        ).reset_index(drop=True)
        regressors["vix"] = regressors["vix"].ffill().bfill()
        regressors["index_return"] = (
            regressors["index_return"].ffill().fillna(0)
        )

        # Add sentiment from Iceberg.
        sent_df = repo.get_sentiment_series(ticker)
        if not sent_df.empty:
            sent_reg = pd.DataFrame(
                {
                    "ds": pd.to_datetime(
                        sent_df["score_date"],
                    ),
                    "sentiment": sent_df["avg_score"].values,
                }
            )
            regressors = regressors.merge(
                sent_reg,
                on="ds",
                how="outer",
            )
            regressors["sentiment"] = regressors["sentiment"].ffill().fillna(0)

        # Add macro indicators from OHLCV (Phase 3).
        regressors = _merge_macro_regressors(
            repo,
            regressors,
        )

        _logger.info(
            "Iceberg regressors: %s (%d rows)",
            list(
                regressors.columns.drop(
                    "ds",
                    errors="ignore",
                )
            ),
            len(regressors),
        )
        return regressors
    except Exception as exc:
        _logger.warning(
            "Iceberg regressors failed for %s: %s, "
            "falling back to live fetch",
            ticker,
            exc,
        )
        return _fetch_market_regressors(ticker)


def _merge_macro_regressors(
    repo,
    regressors: pd.DataFrame,
) -> pd.DataFrame:
    """Merge macro indicators into the regressor DataFrame.

    Loads Treasury yield, T-bill rate, crude oil, and US
    dollar index from the OHLCV table.  Computes the yield
    spread (10Y − 3M) as a recession signal.

    Applies to both US and Indian tickers — Fed rate and
    oil directly impact Indian markets via FII flows and
    import costs.

    Returns the enriched regressors DataFrame.
    """
    _macro_syms = {
        "^TNX": "treasury_10y",
        "CL=F": "oil_price",
        "DX-Y.NYB": "dollar_index",
    }
    for sym, col_name in _macro_syms.items():
        try:
            df = repo.get_ohlcv(sym)
            if df.empty:
                continue
            part = pd.DataFrame(
                {
                    "ds": pd.to_datetime(df["date"]),
                    col_name: df["close"].values,
                }
            )
            regressors = regressors.merge(
                part,
                on="ds",
                how="outer",
            )
            regressors[col_name] = regressors[col_name].ffill().bfill()
        except Exception:
            _logger.debug(
                "Macro %s unavailable",
                sym,
            )

    # Yield spread = 10Y Treasury − 13-week T-bill.
    try:
        irx_df = repo.get_ohlcv("^IRX")
        if not irx_df.empty and "treasury_10y" in regressors.columns:
            irx_reg = pd.DataFrame(
                {
                    "ds": pd.to_datetime(
                        irx_df["date"],
                    ),
                    "_irx": irx_df["close"].values,
                }
            )
            regressors = regressors.merge(
                irx_reg,
                on="ds",
                how="outer",
            )
            regressors["_irx"] = regressors["_irx"].ffill().bfill()
            regressors["yield_spread"] = (
                regressors["treasury_10y"] - regressors["_irx"]
            )
            regressors = regressors.drop(
                columns=["_irx"],
            )
    except Exception:
        _logger.debug("Yield spread unavailable")

    return regressors


def _fetch_analyst_signals(
    ticker: str,
    current_price: float,
    prophet_df: pd.DataFrame,
) -> pd.DataFrame | None:
    """Fetch analyst target + EPS revision as regressors.

    Returns a DataFrame with ``ds``, ``analyst_bias``, and
    ``eps_revision`` columns aligned to prophet_df dates.

    - ``analyst_bias``: (mean_target - current) / current
      Range: typically -0.3 to +0.5
    - ``eps_revision``: average of 7d + 30d EPS revision %
      Range: typically -20 to +20

    Returns ``None`` if data unavailable or analyst
    coverage is insufficient (< 5 analysts).
    """
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        bias = 0.0
        revision = 0.0
        has_data = False

        # Analyst price targets.
        try:
            targets = t.analyst_price_targets
            if targets and targets.get("mean"):
                n_analysts = targets.get(
                    "numberOfAnalysts",
                    0,
                )
                if n_analysts >= 5 or (
                    targets.get("low") and targets.get("high")
                ):
                    mean_t = float(targets["mean"])
                    bias = (mean_t - current_price) / current_price
                    has_data = True
                    _logger.info(
                        "Analyst target %s: mean=%.2f " "bias=%.2f",
                        ticker,
                        mean_t,
                        bias,
                    )
        except Exception:
            _logger.debug(
                "Analyst targets N/A for %s",
                ticker,
            )

        # EPS revision momentum.
        try:
            trend = t.earnings_trend
            if trend is not None and not trend.empty:
                # Look for 7d and 30d columns.
                cols = trend.columns.tolist()
                rev_7d = 0.0
                rev_30d = 0.0
                for col in cols:
                    if "7d" in str(col).lower():
                        vals = trend[col].dropna()
                        if not vals.empty:
                            rev_7d = float(vals.iloc[0])
                    if "30d" in str(col).lower():
                        vals = trend[col].dropna()
                        if not vals.empty:
                            rev_30d = float(vals.iloc[0])
                revision = (rev_7d + rev_30d) / 2.0
                if revision != 0.0:
                    has_data = True
                _logger.info(
                    "EPS revision %s: 7d=%.1f " "30d=%.1f avg=%.1f",
                    ticker,
                    rev_7d,
                    rev_30d,
                    revision,
                )
        except Exception:
            _logger.debug(
                "EPS trend N/A for %s",
                ticker,
            )

        if not has_data:
            return None

        result = pd.DataFrame(
            {
                "ds": prophet_df["ds"],
                "analyst_bias": bias,
                "eps_revision": revision,
            }
        )
        return result
    except Exception as exc:
        _logger.debug(
            "Analyst signals failed for %s: %s",
            ticker,
            exc,
        )
        return None


def _fetch_earnings_holidays(
    ticker: str,
) -> pd.DataFrame:
    """Fetch earnings dates and format as Prophet holidays.

    Adds a ±2 trading-day window around each earnings date
    to capture elevated volatility.  Returns an empty
    DataFrame if no earnings data is available.
    """
    try:
        import yfinance as yf

        ed = yf.Ticker(ticker).earnings_dates
        if ed is None or ed.empty:
            return pd.DataFrame(
                columns=[
                    "holiday",
                    "ds",
                    "lower_window",
                    "upper_window",
                ],
            )
        dates = pd.to_datetime(
            ed.index,
        ).tz_localize(None)
        rows = [
            {
                "holiday": "earnings",
                "ds": d,
                "lower_window": -2,
                "upper_window": 2,
            }
            for d in dates
        ]
        _logger.info(
            "Earnings holidays for %s: %d dates",
            ticker,
            len(rows),
        )
        return pd.DataFrame(rows)
    except Exception as exc:
        _logger.debug(
            "Earnings dates unavailable for %s: %s",
            ticker,
            exc,
        )
        return pd.DataFrame(
            columns=[
                "holiday",
                "ds",
                "lower_window",
                "upper_window",
            ],
        )


def _is_ohlcv_stale(df: pd.DataFrame) -> bool:
    """Return True if OHLCV data is more than 2 calendar days old.

    Args:
        df: OHLCV DataFrame with a ``date`` column.

    Returns:
        ``True`` when data needs refreshing.
    """
    if df.empty:
        return True
    latest = pd.to_datetime(df["date"]).max().date()
    gap = (date.today() - latest).days
    return gap > 2


def _auto_fetch(ticker: str) -> None:
    """Trigger a yfinance fetch to fill missing/stale data.

    Args:
        ticker: Stock ticker symbol (already uppercased).
    """
    try:
        from tools.stock_data_tool import (
            fetch_stock_data,
        )

        _logger.info(
            "Auto-fetching stale/missing OHLCV for %s",
            ticker,
        )
        fetch_stock_data.invoke({"ticker": ticker})
    except Exception as exc:
        _logger.warning(
            "Auto-fetch failed for %s: %s", ticker, exc
        )


def _load_ohlcv(ticker: str) -> pd.DataFrame | None:
    """Load OHLCV data for a ticker from Iceberg.

    If Iceberg has no data or the data is stale (>2 days old),
    automatically triggers a yfinance fetch before reading.

    Returns a DataFrame with a DatetimeIndex and columns
    ``Open``, ``High``, ``Low``, ``Close``, ``Adj Close``,
    ``Volume``.

    Args:
        ticker: Stock ticker symbol (already uppercased).

    Returns:
        A :class:`pandas.DataFrame` with a DatetimeIndex, or
        ``None`` if data is unavailable even after fetch.
    """
    try:
        repo = _require_repo()
        df = repo.get_ohlcv(ticker)

        if df.empty or _is_ohlcv_stale(df):
            _auto_fetch(ticker)
            df = repo.get_ohlcv(ticker)
            if df.empty:
                _logger.warning(
                    "No OHLCV data for %s after auto-fetch",
                    ticker,
                )
                return None

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        # Use adj_close only when it has meaningful coverage
        # (>50 %); otherwise fall back to close.  Indian
        # stocks via yfinance often have adj_close as
        # all-NaN or nearly so.
        use_adj = (
            "adj_close" in df.columns
            and df["adj_close"].notna().mean() > 0.5
        )
        # Drop rows with NaN close — happens when
        # yfinance returns today's intraday placeholder
        # row before market close.
        df = df.dropna(subset=["close"])
        # Assign adj_col AFTER dropna to avoid stale
        # index reference that reintroduces NaN.
        adj_col = (
            df["adj_close"] if use_adj else df["close"]
        )

        result = pd.DataFrame(
            {
                "Open": df["open"],
                "High": df["high"],
                "Low": df["low"],
                "Close": df["close"],
                "Adj Close": adj_col,
                "Volume": df["volume"],
            }
        )
        result.index.name = "Date"
        result.index = pd.to_datetime(result.index)
        return result
    except Exception as exc:
        _logger.warning(
            "Iceberg OHLCV read failed for %s: %s",
            ticker,
            exc,
        )
        return None


def _is_indian_ticker(ticker: str) -> bool:
    """Return True for NSE/BSE tickers.

    Checks suffix first, then falls back to
    stock_registry market field for canonical symbols.
    """
    if ticker.endswith((".NS", ".BO")):
        return True
    try:
        from backend.tools._stock_shared import (
            _require_repo,
        )
        reg = _require_repo().check_existing_data(ticker)
        if reg and reg.get("market", "").upper() in (
            "NSE", "BSE", "INDIA",
        ):
            return True
    except Exception:
        pass
    return False


def _build_holidays_df(
    years: range,
    ticker: str = "",
) -> pd.DataFrame:
    """Build a Prophet-compatible holidays DataFrame.

    Uses Indian market holidays for ``.NS``/``.BO`` tickers,
    US federal holidays for everything else.

    Args:
        years: Range of calendar years to include.
        ticker: Stock ticker — used to pick the right
            market calendar.

    Returns:
        DataFrame with columns ``holiday`` (str) and ``ds``
        (:class:`pandas.Timestamp`).
    """
    country = "IN" if _is_indian_ticker(ticker) else "US"
    hols = holidays_lib.country_holidays(
        country,
        years=list(years),
    )
    rows = [
        {"holiday": name, "ds": pd.Timestamp(dt)} for dt, name in hols.items()
    ]
    _logger.debug(
        "Built %d %s holidays for %s",
        len(rows),
        country,
        ticker or "default",
    )
    return (
        pd.DataFrame(rows) if rows else pd.DataFrame(columns=["holiday", "ds"])
    )

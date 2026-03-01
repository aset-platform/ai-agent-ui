"""Yahoo Finance data fetching tools for the Stock Analysis Agent.

Provides LangChain ``@tool`` functions for fetching stock market data from
Yahoo Finance, persisting it as parquet files, and maintaining a metadata
registry for smart delta fetching.

Path constants and helper functions live in :mod:`tools._stock_shared` and
:mod:`tools._stock_registry`.  The constants are re-exported here so that
existing ``monkeypatch.setattr(stock_data_tool, ...)`` calls continue to work
in tests.  For new tests, prefer patching ``tools._stock_shared.<attr>``.

Typical usage::

    from tools.stock_data_tool import fetch_stock_data

    result = fetch_stock_data.invoke({"ticker": "AAPL"})
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf
from langchain_core.tools import tool

import tools._stock_shared as _ss
from tools._stock_shared import _currency_symbol, _load_currency, _get_repo
from tools._stock_registry import (
    _load_registry,
    _save_registry,
    _check_existing_data,
    _update_registry,
)

# Module-level logger — kept at module scope intentionally (not inside a class).
_logger = logging.getLogger(__name__)

# Re-export constants so ``monkeypatch.setattr(stock_data_tool, "_DATA_RAW", ...)`` works.
# Sub-modules access shared state via module attrs on ``_ss`` for correct patching.
_PROJECT_ROOT = _ss._PROJECT_ROOT
_DATA_RAW = _ss._DATA_RAW
_DATA_PROCESSED = _ss._DATA_PROCESSED
_DATA_METADATA = _ss._DATA_METADATA
_REGISTRY_PATH = _ss._REGISTRY_PATH
_STOCK_REPO = _ss._STOCK_REPO


# ---------------------------------------------------------------------------
# Public @tool functions
# ---------------------------------------------------------------------------


@tool
def fetch_stock_data(ticker: str, period: str = "10y") -> str:
    """Fetch OHLCV stock data from Yahoo Finance with smart delta fetching.

    On first call: fetches full history and saves as parquet.  On subsequent
    calls: only fetches missing date range (delta) and appends to existing file.
    If data is already up to date, the fetch is skipped.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``, ``"RELIANCE.NS"``.
        period: History period for first-time fetches, e.g. ``"10y"``.

    Returns:
        A summary string describing the fetch result, or an error string.

    Example:
        >>> result = fetch_stock_data.invoke({"ticker": "AAPL"})
        >>> "AAPL" in result
        True
    """
    ticker = ticker.upper().strip()
    _logger.info("fetch_stock_data | ticker=%s | period=%s", ticker, period)

    try:
        existing = _check_existing_data(ticker)
        file_path = _ss._DATA_RAW / f"{ticker}_raw.parquet"
        _ss._DATA_RAW.mkdir(parents=True, exist_ok=True)

        if existing is None:
            df = yf.Ticker(ticker).history(period=period, auto_adjust=False)
            if df.empty:
                return (
                    f"Error: No data returned for '{ticker}'. "
                    "Please check the ticker symbol and try again. "
                    "Examples: AAPL (Apple), TSLA (Tesla), RELIANCE.NS (Reliance India)."
                )
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.to_parquet(file_path, engine="pyarrow", index=True)
            _update_registry(ticker, df, file_path)
            try:
                repo = _get_repo()
                if repo is not None:
                    repo.insert_ohlcv(ticker, df)
            except Exception as _e:
                _logger.warning("Iceberg OHLCV insert failed for %s: %s", ticker, _e)
            msg = (
                f"Full fetch completed for {ticker}: {len(df)} rows saved. "
                f"Date range: {df.index.min().date()} to {df.index.max().date()}."
            )
            _logger.info(msg)
            return msg

        last_fetch = datetime.strptime(existing["last_fetch_date"], "%Y-%m-%d").date()
        today = date.today()
        delta_days = (today - last_fetch).days

        if delta_days == 0:
            msg = f"Data is already up to date for {ticker} (last fetch: {last_fetch})."
            _logger.info(msg)
            return msg

        new_df = yf.Ticker(ticker).history(start=str(last_fetch), end=str(today), auto_adjust=False)

        if new_df.empty:
            msg = (
                f"No new trading data found for {ticker} since {last_fetch}. "
                "This may be a weekend or holiday period."
            )
            _logger.info(msg)
            return msg

        new_df.index = pd.to_datetime(new_df.index).tz_localize(None)
        existing_df = pd.read_parquet(file_path, engine="pyarrow")
        combined = pd.concat([existing_df, new_df])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)
        combined.to_parquet(file_path, engine="pyarrow", index=True)
        _update_registry(ticker, combined, file_path)
        try:
            repo = _get_repo()
            if repo is not None:
                repo.insert_ohlcv(ticker, new_df)
        except Exception as _e:
            _logger.warning("Iceberg OHLCV delta-write failed for %s: %s", ticker, _e)

        msg = (
            f"Delta fetch for {ticker}: {len(new_df)} new rows added "
            f"({last_fetch} to {today}). Total: {len(combined)} rows."
        )
        _logger.info(msg)
        return msg

    except Exception as e:
        _logger.error("fetch_stock_data failed for %s: %s", ticker, e, exc_info=True)
        return f"Error fetching data for '{ticker}': {e}"


@tool
def get_stock_info(ticker: str) -> str:
    """Fetch company metadata for a stock ticker from Yahoo Finance.

    Retrieves company name, sector, industry, market cap, PE ratio, and
    52-week high/low. Results are cached to
    ``data/metadata/{TICKER}_info.json`` (refreshed once per day).

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``.

    Returns:
        A JSON-formatted string containing company metadata fields, or
        an error string.

    Example:
        >>> result = get_stock_info.invoke({"ticker": "AAPL"})
        >>> "Apple" in result
        True
    """
    ticker = ticker.upper().strip()
    cache_path = _ss._DATA_METADATA / f"{ticker}_info.json"
    _logger.info("get_stock_info | ticker=%s", ticker)

    try:
        if cache_path.exists():
            with open(cache_path, "r") as f:
                cached = json.load(f)
            if cached.get("_fetched_date") == str(date.today()):
                cached.pop("_fetched_date", None)
                return json.dumps(cached, indent=2)

        info = yf.Ticker(ticker).info
        result = {
            "ticker": ticker,
            "company_name": info.get("longName", "N/A"),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "market_cap": info.get("marketCap", "N/A"),
            "pe_ratio": info.get("trailingPE", "N/A"),
            "52w_high": info.get("fiftyTwoWeekHigh", "N/A"),
            "52w_low": info.get("fiftyTwoWeekLow", "N/A"),
            "current_price": info.get("currentPrice", info.get("regularMarketPrice", "N/A")),
            "currency": info.get("currency", "USD"),
        }
        _ss._DATA_METADATA.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump({**result, "_fetched_date": str(date.today())}, f, indent=2)
        try:
            repo = _get_repo()
            if repo is not None:
                repo.insert_company_info(ticker, info)
        except Exception as _e:
            _logger.warning("Iceberg company_info insert failed for %s: %s", ticker, _e)
        return json.dumps(result, indent=2)

    except Exception as e:
        _logger.error("get_stock_info failed for %s: %s", ticker, e, exc_info=True)
        return f"Error fetching info for '{ticker}': {e}"


@tool
def load_stock_data(ticker: str) -> str:
    """Return a summary of locally stored OHLCV data for a ticker.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``.

    Returns:
        A formatted summary string, or an error string if no local data exists.

    Example:
        >>> result = load_stock_data.invoke({"ticker": "AAPL"})
        >>> "rows" in result
        True
    """
    ticker = ticker.upper().strip()
    file_path = _ss._DATA_RAW / f"{ticker}_raw.parquet"
    _logger.info("load_stock_data | ticker=%s", ticker)

    if not file_path.exists():
        return (
            f"No local data found for '{ticker}'. "
            "Run fetch_stock_data first to download and store the data."
        )
    try:
        df = pd.read_parquet(file_path, engine="pyarrow")
        missing = int(df.isnull().sum().sum())
        size_kb = file_path.stat().st_size / 1024
        return (
            f"Loaded {ticker}: {len(df)} rows x {len(df.columns)} columns. "
            f"Date range: {df.index.min().date()} to {df.index.max().date()}. "
            f"Columns: {list(df.columns)}. Missing values: {missing}. "
            f"File size: {size_kb:.1f} KB."
        )
    except Exception as e:
        _logger.error("load_stock_data failed for %s: %s", ticker, e, exc_info=True)
        return f"Error loading data for '{ticker}': {e}"


@tool
def fetch_multiple_stocks(tickers: str, period: str = "10y") -> str:
    """Fetch OHLCV data for multiple stock tickers in a single call.

    Args:
        tickers: Comma-separated ticker symbols, e.g. ``"AAPL,TSLA,MSFT"``.
        period: History period for first-time fetches. Defaults to ``"10y"``.

    Returns:
        A multi-line summary with result per ticker and final count.

    Example:
        >>> result = fetch_multiple_stocks.invoke({"tickers": "AAPL,MSFT"})
        >>> "AAPL" in result
        True
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    _logger.info("fetch_multiple_stocks | tickers=%s", ticker_list)
    results = []
    full_count = delta_count = skip_count = error_count = 0
    for ticker in ticker_list:
        result = fetch_stock_data.invoke({"ticker": ticker, "period": period})
        results.append(f"  {ticker}: {result}")
        if "Full fetch" in result:
            full_count += 1
        elif "Delta fetch" in result:
            delta_count += 1
        elif "Error" in result:
            error_count += 1
        else:
            skip_count += 1
    lines = [
        f"Batch fetch complete for {len(ticker_list)} tickers:",
        *results,
        f"\nSummary: {full_count} full, {delta_count} delta, {skip_count} skipped, {error_count} error(s).",
    ]
    return "\n".join(lines)


@tool
def get_dividend_history(ticker: str) -> str:
    """Fetch and store the full dividend history for a stock ticker.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``.

    Returns:
        A summary string with dividend payment count and date range.

    Example:
        >>> result = get_dividend_history.invoke({"ticker": "AAPL"})
        >>> isinstance(result, str)
        True
    """
    ticker = ticker.upper().strip()
    _logger.info("get_dividend_history | ticker=%s", ticker)
    _ss._DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

    try:
        dividends = yf.Ticker(ticker).dividends
        if dividends.empty:
            return f"{ticker} has no dividend history on record."
        dividends.index = pd.to_datetime(dividends.index).tz_localize(None)
        df = dividends.reset_index()
        df.columns = ["date", "dividend"]
        out_path = _ss._DATA_PROCESSED / f"{ticker}_dividends.parquet"
        df.to_parquet(out_path, engine="pyarrow", index=False)
        try:
            repo = _get_repo()
            if repo is not None:
                repo.insert_dividends(ticker, df, currency=_load_currency(ticker))
        except Exception as _e:
            _logger.warning("Iceberg dividends insert failed for %s: %s", ticker, _e)
        curr_sym = _currency_symbol(_load_currency(ticker))
        msg = (
            f"Dividend history for {ticker}: {len(df)} payments. "
            f"Date range: {df['date'].min().date()} to {df['date'].max().date()}. "
            f"Most recent: {curr_sym}{df['dividend'].iloc[-1]:.4f} on {df['date'].iloc[-1].date()}. "
            f"Saved to {out_path}."
        )
        _logger.info(msg)
        return msg

    except Exception as e:
        _logger.error("get_dividend_history failed for %s: %s", ticker, e, exc_info=True)
        return f"Error fetching dividend history for '{ticker}': {e}"


@tool
def list_available_stocks() -> str:
    """List all stocks currently stored in the local data registry.

    Returns:
        A formatted table string, or a message indicating the registry is empty.

    Example:
        >>> result = list_available_stocks.invoke({})
        >>> isinstance(result, str)
        True
    """
    _logger.info("list_available_stocks called")
    registry = _load_registry()
    if not registry:
        return "No stocks in the local registry yet. Use fetch_stock_data to download and store stock data."
    lines = [
        f"{'Ticker':<15} {'Rows':>6}  {'Start':>12}  {'End':>12}  {'Last Fetch':>12}",
        "-" * 65,
    ]
    for entry in registry.values():
        dr = entry.get("date_range", {})
        lines.append(
            f"{entry['ticker']:<15} {entry['total_rows']:>6}  "
            f"{dr.get('start', 'N/A'):>12}  {dr.get('end', 'N/A'):>12}  "
            f"{entry['last_fetch_date']:>12}"
        )
    lines.append(f"\nTotal: {len(registry)} stock(s) in registry.")
    return "\n".join(lines)
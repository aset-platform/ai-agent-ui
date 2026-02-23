"""Yahoo Finance data fetching tools for the Stock Analysis Agent.

This module provides LangChain ``@tool`` functions for fetching stock market
data from Yahoo Finance, persisting it as parquet files, and maintaining a
metadata registry for smart delta fetching.

Data is stored relative to the project root::

    data/raw/{TICKER}_raw.parquet          ← OHLCV history
    data/processed/{TICKER}_dividends.parquet
    data/metadata/{TICKER}_info.json       ← company metadata cache
    data/metadata/stock_registry.json      ← fetch registry

**Delta fetching strategy**: On subsequent calls for the same ticker, only
the missing date range is fetched from Yahoo Finance and appended to the
existing parquet file, minimising API calls and network traffic.

Typical usage (via LangChain tool call)::

    from tools.stock_data_tool import fetch_stock_data, get_stock_info

    result = fetch_stock_data.invoke({"ticker": "AAPL"})
    info   = get_stock_info.invoke({"ticker": "AAPL"})
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf
from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# Project root is 3 levels up from this file:
#   backend/tools/stock_data_tool.py → backend/tools/ → backend/ → ai-agent-ui/
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_RAW = _PROJECT_ROOT / "data" / "raw"
_DATA_PROCESSED = _PROJECT_ROOT / "data" / "processed"
_DATA_METADATA = _PROJECT_ROOT / "data" / "metadata"
_REGISTRY_PATH = _DATA_METADATA / "stock_registry.json"

# ---------------------------------------------------------------------------
# Private helper functions (not exposed as tools)
# ---------------------------------------------------------------------------


def _load_registry() -> dict:
    """Load the stock registry JSON file from disk.

    Returns:
        Dictionary mapping ticker symbols to their metadata records.
        Returns an empty dict if the registry file does not exist or
        cannot be parsed.
    """
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        with open(_REGISTRY_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to load stock registry: %s", e)
        return {}


def _save_registry(registry: dict) -> None:
    """Persist the stock registry dictionary to disk as JSON.

    Args:
        registry: Dictionary mapping ticker symbols to metadata records.
    """
    _DATA_METADATA.mkdir(parents=True, exist_ok=True)
    with open(_REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2, default=str)
    logger.debug("Registry saved with %d entries", len(registry))


def _check_existing_data(ticker: str) -> Optional[dict]:
    """Look up a ticker in the stock registry.

    Args:
        ticker: The stock ticker symbol (already uppercased).

    Returns:
        The registry entry dict if the ticker exists, or ``None``.
    """
    registry = _load_registry()
    return registry.get(ticker)


def _update_registry(ticker: str, df: pd.DataFrame, file_path: Path) -> None:
    """Update the stock registry with metadata for a ticker.

    Args:
        ticker: The stock ticker symbol (already uppercased).
        df: The full DataFrame (used to derive row count and date range).
        file_path: Absolute path to the saved parquet file.
    """
    registry = _load_registry()
    registry[ticker] = {
        "ticker": ticker,
        "last_fetch_date": str(date.today()),
        "total_rows": len(df),
        "date_range": {
            "start": str(df.index.min().date()),
            "end": str(df.index.max().date()),
        },
        "file_path": str(file_path),
    }
    _save_registry(registry)


# ---------------------------------------------------------------------------
# Public @tool functions
# ---------------------------------------------------------------------------


@tool
def fetch_stock_data(ticker: str, period: str = "10y") -> str:
    """Fetch OHLCV stock data from Yahoo Finance with smart delta fetching.

    On first call for a ticker: fetches the full history for the specified
    period and saves it as a parquet file. On subsequent calls: only fetches
    the missing date range (delta), appends it to the existing file, and
    updates the registry. If data is already up to date, the fetch is skipped.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``, ``"TSLA"``,
            ``"RELIANCE.NS"``.
        period: History period for first-time fetches, e.g. ``"10y"``,
            ``"5y"``. Ignored on delta fetches. Defaults to ``"10y"``.

    Returns:
        A summary string describing the fetch result (fetch type, row count,
        date range). Returns an error string if the fetch fails or the
        ticker is not recognised by Yahoo Finance.

    Example:
        >>> result = fetch_stock_data.invoke({"ticker": "AAPL"})
        >>> "AAPL" in result
        True
    """
    ticker = ticker.upper().strip()
    logger.info("fetch_stock_data | ticker=%s | period=%s", ticker, period)

    try:
        existing = _check_existing_data(ticker)
        file_path = _DATA_RAW / f"{ticker}_raw.parquet"
        _DATA_RAW.mkdir(parents=True, exist_ok=True)

        if existing is None:
            # ── Full fetch ────────────────────────────────────────────────
            logger.info("No existing data for %s — performing full fetch", ticker)
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

            msg = (
                f"Full fetch completed for {ticker}: {len(df)} rows saved. "
                f"Date range: {df.index.min().date()} to {df.index.max().date()}."
            )
            logger.info(msg)
            return msg

        # ── Delta fetch ───────────────────────────────────────────────────
        last_fetch = datetime.strptime(
            existing["last_fetch_date"], "%Y-%m-%d"
        ).date()
        today = date.today()
        delta_days = (today - last_fetch).days

        if delta_days == 0:
            msg = (
                f"Data is already up to date for {ticker} "
                f"(last fetch: {last_fetch})."
            )
            logger.info(msg)
            return msg

        logger.info(
            "Delta fetch for %s: %d day(s) missing since %s",
            ticker,
            delta_days,
            last_fetch,
        )
        new_df = yf.Ticker(ticker).history(
            start=str(last_fetch), end=str(today), auto_adjust=False
        )

        if new_df.empty:
            msg = (
                f"No new trading data found for {ticker} since {last_fetch}. "
                "This may be a weekend or holiday period."
            )
            logger.info(msg)
            return msg

        new_df.index = pd.to_datetime(new_df.index).tz_localize(None)
        existing_df = pd.read_parquet(file_path, engine="pyarrow")
        combined = pd.concat([existing_df, new_df])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)
        combined.to_parquet(file_path, engine="pyarrow", index=True)
        _update_registry(ticker, combined, file_path)

        msg = (
            f"Delta fetch for {ticker}: {len(new_df)} new rows added "
            f"({last_fetch} to {today}). Total: {len(combined)} rows."
        )
        logger.info(msg)
        return msg

    except Exception as e:
        logger.error(
            "fetch_stock_data failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error fetching data for '{ticker}': {e}"


@tool
def get_stock_info(ticker: str) -> str:
    """Fetch company metadata for a stock ticker from Yahoo Finance.

    Retrieves company name, sector, industry, market cap, PE ratio, and
    52-week high/low. Results are cached to
    ``data/metadata/{TICKER}_info.json`` (refreshed once per day) to
    avoid redundant API calls.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``, ``"MSFT"``.

    Returns:
        A JSON-formatted string containing company metadata fields, or
        an error string if the fetch fails.

    Example:
        >>> result = get_stock_info.invoke({"ticker": "AAPL"})
        >>> "Apple" in result
        True
    """
    ticker = ticker.upper().strip()
    cache_path = _DATA_METADATA / f"{ticker}_info.json"
    logger.info("get_stock_info | ticker=%s", ticker)

    try:
        # Return cached result if it was fetched today
        if cache_path.exists():
            with open(cache_path, "r") as f:
                cached = json.load(f)
            if cached.get("_fetched_date") == str(date.today()):
                logger.debug("Returning cached info for %s", ticker)
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
            "current_price": info.get(
                "currentPrice", info.get("regularMarketPrice", "N/A")
            ),
            "currency": info.get("currency", "USD"),
        }

        _DATA_METADATA.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump({**result, "_fetched_date": str(date.today())}, f, indent=2)

        logger.info("Stock info fetched and cached for %s", ticker)
        return json.dumps(result, indent=2)

    except Exception as e:
        logger.error(
            "get_stock_info failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error fetching info for '{ticker}': {e}"


@tool
def load_stock_data(ticker: str) -> str:
    """Return a summary of locally stored OHLCV data for a ticker.

    Reads the parquet file saved by :func:`fetch_stock_data` and returns a
    human-readable summary (shape, date range, columns, missing value count).
    Does not re-fetch from Yahoo Finance.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``.

    Returns:
        A formatted summary string, or an error string if no local data
        exists for the ticker.

    Example:
        >>> result = load_stock_data.invoke({"ticker": "AAPL"})
        >>> "rows" in result
        True
    """
    ticker = ticker.upper().strip()
    file_path = _DATA_RAW / f"{ticker}_raw.parquet"
    logger.info("load_stock_data | ticker=%s", ticker)

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
            f"Columns: {list(df.columns)}. "
            f"Missing values: {missing}. "
            f"File size: {size_kb:.1f} KB."
        )
    except Exception as e:
        logger.error(
            "load_stock_data failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error loading data for '{ticker}': {e}"


@tool
def fetch_multiple_stocks(tickers: str, period: str = "10y") -> str:
    """Fetch OHLCV data for multiple stock tickers in a single call.

    Accepts a comma-separated string of ticker symbols. Calls
    :func:`fetch_stock_data` for each ticker (delta logic is handled
    automatically) and returns a consolidated summary.

    Args:
        tickers: Comma-separated ticker symbols, e.g. ``"AAPL,TSLA,MSFT"``.
        period: History period for first-time fetches. Defaults to ``"10y"``.

    Returns:
        A multi-line summary string with the result per ticker, plus a
        final count of full fetches, delta fetches, and skips.

    Example:
        >>> result = fetch_multiple_stocks.invoke({"tickers": "AAPL,MSFT"})
        >>> "AAPL" in result
        True
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    logger.info("fetch_multiple_stocks | tickers=%s", ticker_list)

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
        (
            f"\nSummary: {full_count} full, {delta_count} delta, "
            f"{skip_count} skipped, {error_count} error(s)."
        ),
    ]
    return "\n".join(lines)


@tool
def get_dividend_history(ticker: str) -> str:
    """Fetch and store the full dividend history for a stock ticker.

    Retrieves all historical dividend payments from Yahoo Finance and saves
    them to ``data/processed/{TICKER}_dividends.parquet`` using pyarrow.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``.

    Returns:
        A summary string with the total number of dividend payments, date
        range, and most recent dividend amount. Returns an informational
        string if the ticker pays no dividends, or an error string on failure.

    Example:
        >>> result = get_dividend_history.invoke({"ticker": "AAPL"})
        >>> isinstance(result, str)
        True
    """
    ticker = ticker.upper().strip()
    logger.info("get_dividend_history | ticker=%s", ticker)
    _DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

    try:
        dividends = yf.Ticker(ticker).dividends
        if dividends.empty:
            return f"{ticker} has no dividend history on record."

        dividends.index = pd.to_datetime(dividends.index).tz_localize(None)
        df = dividends.reset_index()
        df.columns = ["date", "dividend"]

        out_path = _DATA_PROCESSED / f"{ticker}_dividends.parquet"
        df.to_parquet(out_path, engine="pyarrow", index=False)

        msg = (
            f"Dividend history for {ticker}: {len(df)} payments. "
            f"Date range: {df['date'].min().date()} to {df['date'].max().date()}. "
            f"Most recent: ${df['dividend'].iloc[-1]:.4f} "
            f"on {df['date'].iloc[-1].date()}. "
            f"Saved to {out_path}."
        )
        logger.info(msg)
        return msg

    except Exception as e:
        logger.error(
            "get_dividend_history failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error fetching dividend history for '{ticker}': {e}"


@tool
def list_available_stocks() -> str:
    """List all stocks currently stored in the local data registry.

    Reads ``data/metadata/stock_registry.json`` and returns a formatted
    table of all tickers with their row count, date range, and last fetch
    date.

    Returns:
        A formatted table string, or a message indicating the registry
        is empty.

    Example:
        >>> result = list_available_stocks.invoke({})
        >>> isinstance(result, str)
        True
    """
    logger.info("list_available_stocks called")
    registry = _load_registry()

    if not registry:
        return (
            "No stocks in the local registry yet. "
            "Use fetch_stock_data to download and store stock data."
        )

    lines = [
        f"{'Ticker':<15} {'Rows':>6}  {'Start':>12}  {'End':>12}  "
        f"{'Last Fetch':>12}",
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

"""Yahoo Finance data fetching tools for the Stock Analysis Agent.

Provides LangChain ``@tool`` functions for fetching stock market data from
Yahoo Finance, persisting it as parquet files, and maintaining the Iceberg
registry for smart delta fetching.

Path constants and helper functions live in :mod:`tools._stock_shared` and
:mod:`tools._stock_registry`.

Typical usage::

    from tools.stock_data_tool import fetch_stock_data

    result = fetch_stock_data.invoke({"ticker": "AAPL"})
"""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta

import pandas as pd
import tools._stock_shared as _ss
import yfinance as yf
from langchain_core.tools import tool
from tools._stock_registry import (
    _check_existing_data,
    _load_registry,
    _update_registry,
)
from tools._stock_shared import _get_repo  # noqa: F401 patched by tests
from tools._stock_shared import (  # noqa: F401
    _currency_symbol,
    _load_currency,
    _parquet_path,
    _require_repo,
)
from validation import (
    validate_ticker,
    validate_ticker_batch,
)

# Module-level logger (not inside a class).
_logger = logging.getLogger(__name__)

# Re-export constants so existing monkeypatch calls still work.
_PROJECT_ROOT = _ss._PROJECT_ROOT
_DATA_RAW = _ss._DATA_RAW
_DATA_PROCESSED = _ss._DATA_PROCESSED


# ---------------------------------------------------------------------------
# Pipeline integration: stock_master lookup helpers
# ---------------------------------------------------------------------------


async def _async_lookup(symbol: str) -> dict | None:
    """Async lookup of stock_master by symbol or yf_ticker."""
    from backend.db.engine import get_session_factory
    from backend.db.models.stock_master import StockMaster
    from sqlalchemy import or_, select

    canonical = symbol.replace(".NS", "").replace(".BO", "").upper()
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(StockMaster).where(
                or_(
                    StockMaster.symbol == canonical,
                    StockMaster.yf_ticker == symbol.upper(),
                )
            )
        )
        stock = result.scalar_one_or_none()
        if stock is None:
            return None
        return {
            "id": stock.id,
            "symbol": stock.symbol,
            "nse_symbol": stock.nse_symbol,
            "yf_ticker": stock.yf_ticker,
            "exchange": stock.exchange,
        }


def _lookup_stock_master(symbol: str) -> dict | None:
    """Synchronous lookup of stock_master by symbol.

    Returns dict with stock_master fields if found,
    None otherwise. Uses a one-shot async session via
    asyncio with a thread-pool bridge when an event loop
    is already running (e.g. inside FastAPI).
    """
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # Inside an async context — schedule on the
            # running loop instead of creating a new one
            fut = asyncio.run_coroutine_threadsafe(
                _async_lookup(symbol),
                loop,
            )
            return fut.result(timeout=5.0)
        return asyncio.run(_async_lookup(symbol))
    except Exception:
        _logger.debug(
            "stock_master lookup failed for %s, "
            "falling through to yfinance",
            symbol,
        )
        return None


# ---------------------------------------------------------------------------
# Public @tool functions
# ---------------------------------------------------------------------------


@tool
def fetch_stock_data(ticker: str, period: str = "10y") -> str:
    """Fetch OHLCV stock data from Yahoo Finance with smart delta fetching.

    On first call: fetches full history and saves as parquet.  On subsequent
    calls: checks the actual last date of data in Iceberg (``date_range_end``)
    and fetches from the day after that through today.  Already up-to-date
    tickers are skipped.

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
    err = validate_ticker(ticker)
    if err:
        return f"Error: {err}"
    ticker = ticker.upper().strip()
    from tools._ticker_linker import auto_link_ticker

    auto_link_ticker(ticker)

    # Pipeline integration: try canonical symbol lookup
    master = _lookup_stock_master(ticker)
    if master:
        _logger.info(
            "stock_master hit for %s (canonical=%s)",
            ticker,
            master["symbol"],
        )
        canonical = master["symbol"]
        existing = _check_existing_data(canonical)
        if existing is not None:
            # Use canonical for rest of function
            ticker = canonical

    _logger.info(
        "fetch_stock_data | ticker=%s | period=%s",
        ticker,
        period,
    )

    try:
        existing = _check_existing_data(ticker)
        file_path = _parquet_path(ticker)
        _ss._DATA_RAW.mkdir(parents=True, exist_ok=True)

        if existing is None:
            df = yf.Ticker(ticker).history(period=period, auto_adjust=False)
            if df.empty:
                return (
                    f"Error: No data returned for '{ticker}'. "
                    "Please check the ticker symbol and try again. "
                    "Examples: AAPL (Apple), TSLA (Tesla),"
                    " RELIANCE.NS (Reliance India)."
                )
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.to_parquet(file_path, engine="pyarrow", index=True)
            _update_registry(ticker, df, file_path)
            repo = _require_repo()
            repo.insert_ohlcv(ticker, df)
            d_min = df.index.min().date()
            d_max = df.index.max().date()
            msg = (
                f"Full fetch completed for {ticker}: "
                f"{len(df)} rows saved. "
                f"Date range: {d_min} to {d_max}."
            )
            _logger.info(msg)
            return msg

        # Use the actual last date of data (not the last *run* date)
        # so that gaps caused by weekends / holidays are filled correctly.
        dr_end_str = existing.get("date_range", {}).get("end", "")
        if dr_end_str:
            data_end = datetime.strptime(dr_end_str, "%Y-%m-%d").date()
        else:
            # Fallback: parse last_fetch_date if date_range is absent
            data_end = datetime.strptime(
                existing["last_fetch_date"], "%Y-%m-%d"
            ).date()

        today = date.today()
        # Start one day after the last data point to avoid
        # re-fetching data that already exists in Iceberg.
        fetch_start = data_end + timedelta(days=1)

        if fetch_start > today:
            msg = (
                f"Data is already up to date for {ticker} "
                f"(last data: {data_end})."
            )
            _logger.info(msg)
            return msg

        # Omit `end` so yfinance fetches up to the current moment
        # (including today's intraday data when the market is open).
        new_df = yf.Ticker(ticker).history(
            start=str(fetch_start), auto_adjust=False
        )

        if new_df.empty:
            msg = (
                f"No new trading data found for {ticker} "
                f"since {data_end}. "
                "This may be a weekend or holiday period."
            )
            _logger.info(msg)
            return msg

        new_df.index = pd.to_datetime(new_df.index).tz_localize(None)
        repo = _require_repo()
        repo.insert_ohlcv(ticker, new_df)

        # Read full OHLCV from Iceberg to rebuild local backup parquet
        ice_df = repo.get_ohlcv(ticker)
        if not ice_df.empty:
            ice_df["date"] = pd.to_datetime(ice_df["date"])
            ice_df = ice_df.sort_values("date").set_index("date")
            backup = pd.DataFrame(
                {
                    "Open": ice_df["open"],
                    "High": ice_df["high"],
                    "Low": ice_df["low"],
                    "Close": ice_df["close"],
                    "Adj Close": ice_df.get("adj_close", ice_df["close"]),
                    "Volume": ice_df["volume"],
                }
            )
            backup.index.name = "Date"
            backup.to_parquet(file_path, engine="pyarrow", index=True)
            total_rows = len(backup)
            _update_registry(ticker, backup, file_path)
        else:
            total_rows = int(existing.get("total_rows", 0)) + len(new_df)
            _update_registry(ticker, new_df, file_path)

        new_end = new_df.index.max().date() if not new_df.empty else today
        msg = (
            f"Delta fetch for {ticker}: {len(new_df)} new rows "
            f"({fetch_start} to {new_end}). "
            f"Total: {total_rows} rows."
        )
        _logger.info(msg)
        return msg

    except Exception as e:
        _logger.error(
            "fetch_stock_data failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error fetching data for '{ticker}': {e}"


@tool
def get_stock_info(ticker: str) -> str:
    """Fetch company metadata for a stock ticker from Yahoo Finance.

    Retrieves company name, sector, industry, market cap, PE ratio, and
    52-week high/low. Results are cached in Iceberg (refreshed once per day).

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
    err = validate_ticker(ticker)
    if err:
        return f"Error: {err}"
    ticker = ticker.upper().strip()
    from tools._ticker_linker import auto_link_ticker

    auto_link_ticker(ticker)
    _logger.info("get_stock_info | ticker=%s", ticker)

    try:
        repo = _require_repo()
        cached = repo.get_latest_company_info_if_fresh(ticker, date.today())
        if cached is not None:
            result = {
                "ticker": ticker,
                "company_name": cached.get("company_name", "N/A"),
                "sector": cached.get("sector", "N/A"),
                "industry": cached.get("industry", "N/A"),
                "market_cap": cached.get("market_cap", "N/A"),
                "pe_ratio": cached.get("pe_ratio", "N/A"),
                "52w_high": cached.get("week_52_high", "N/A"),
                "52w_low": cached.get("week_52_low", "N/A"),
                "current_price": cached.get("current_price", "N/A"),
                "currency": cached.get("currency", "USD"),
            }
            return json.dumps(result, indent=2, default=str)

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
        repo.insert_company_info(ticker, info)
        return json.dumps(result, indent=2)

    except Exception as e:
        _logger.error(
            "get_stock_info failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error fetching info for '{ticker}': {e}"


@tool
def load_stock_data(ticker: str) -> str:
    """Return a summary of OHLCV data stored in Iceberg for a ticker.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``.

    Returns:
        A formatted summary string, or an error string if no data exists.

    Example:
        >>> result = load_stock_data.invoke({"ticker": "AAPL"})
        >>> "rows" in result
        True
    """
    err = validate_ticker(ticker)
    if err:
        return f"Error: {err}"
    ticker = ticker.upper().strip()
    _logger.info("load_stock_data | ticker=%s", ticker)

    try:
        repo = _require_repo()
        df = repo.get_ohlcv(ticker)
        if df.empty:
            return (
                f"No data found for '{ticker}'. "
                "Run fetch_stock_data first to download and store the data."
            )
        df["date"] = pd.to_datetime(df["date"])
        missing = int(df.isnull().sum().sum())
        cols = [c for c in df.columns if c not in ("ticker", "fetched_at")]
        d_min = df["date"].min().date()
        d_max = df["date"].max().date()
        return (
            f"Loaded {ticker}: {len(df)} rows x "
            f"{len(cols)} columns. "
            f"Date range: {d_min} to {d_max}. "
            f"Columns: {cols}. "
            f"Missing values: {missing}."
        )
    except Exception as e:
        _logger.error(
            "load_stock_data failed for %s: %s", ticker, e, exc_info=True
        )
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
    err = validate_ticker_batch(tickers)
    if err:
        return f"Error: {err}"
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    _logger.info(
        "fetch_multiple_stocks | tickers=%s",
        ticker_list,
    )
    results = []
    full_count = delta_count = skip_count = error_count = 0
    for ticker in ticker_list:
        result = fetch_stock_data.invoke(
            {"ticker": ticker, "period": period},
        )
        # Also fetch company info (sector, price, etc.)
        info_result = get_stock_info.invoke(
            {"ticker": ticker},
        )
        results.append(f"  {ticker}: {result}")
        results.append(f"  {ticker} info: {info_result}")
        if "Full fetch" in result:
            full_count += 1
        elif "Delta fetch" in result:
            delta_count += 1
        elif "Error" in result:
            error_count += 1
        else:
            skip_count += 1
    lines = [
        f"Batch fetch complete for {len(ticker_list)} " f"tickers:",
        *results,
        f"\nSummary: {full_count} full, "
        f"{delta_count} delta, {skip_count} skipped, "
        f"{error_count} error(s).",
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
    err = validate_ticker(ticker)
    if err:
        return f"Error: {err}"
    ticker = ticker.upper().strip()
    from tools._ticker_linker import auto_link_ticker

    auto_link_ticker(ticker)
    _logger.info(
        "get_dividend_history | ticker=%s",
        ticker,
    )
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
        repo = _require_repo()
        repo.insert_dividends(ticker, df, currency=_load_currency(ticker))
        curr_sym = _currency_symbol(_load_currency(ticker))
        d_min = df["date"].min().date()
        d_max = df["date"].max().date()
        last_div = df["dividend"].iloc[-1]
        last_dt = df["date"].iloc[-1].date()
        msg = (
            f"Dividend history for {ticker}: "
            f"{len(df)} payments. "
            f"Date range: {d_min} to {d_max}. "
            f"Most recent: {curr_sym}{last_div:.4f} "
            f"on {last_dt}. "
            f"Saved to {out_path}."
        )
        _logger.info(msg)
        return msg

    except Exception as e:
        _logger.error(
            "get_dividend_history failed for %s: %s", ticker, e, exc_info=True
        )
        return f"Error fetching dividend history for '{ticker}': {e}"


# Mapping from yfinance row labels to schema columns
_INCOME_MAP = {
    "Total Revenue": "revenue",
    "Net Income": "net_income",
    "Gross Profit": "gross_profit",
    "Operating Income": "operating_income",
    "EBITDA": "ebitda",
    "Basic EPS": "eps_basic",
    "Diluted EPS": "eps_diluted",
}

_BALANCE_MAP = {
    "Total Assets": "total_assets",
    "Total Liabilities Net Minority Interest": ("total_liabilities"),
    "Stockholders Equity": "total_equity",
    "Total Debt": "total_debt",
    "Cash And Cash Equivalents": "cash_and_equivalents",
    "Current Assets": "current_assets",
    "Current Liabilities": "current_liabilities",
    "Ordinary Shares Number": "shares_outstanding",
}

_CASHFLOW_MAP = {
    "Operating Cash Flow": "operating_cashflow",
    "Capital Expenditure": "capex",
    "Free Cash Flow": "free_cashflow",
}


def _extract_statement(
    stmt_df: pd.DataFrame,
    metric_map: dict,
    statement_type: str,
    ticker: str,
) -> list[dict]:
    """Extract quarterly rows from a yfinance statement.

    Skips quarters where every mapped metric is null (e.g.
    partially reported balance sheets for some Indian stocks).


    Args:
        stmt_df: Raw yfinance statement (rows=metrics,
            cols=quarter-end dates).
        metric_map: Label-to-column mapping.
        statement_type: ``"income"``, ``"balance"``, or
            ``"cashflow"``.
        ticker: Uppercase ticker symbol.

    Returns:
        List of row dicts ready for DataFrame construction.
    """
    if stmt_df is None or stmt_df.empty:
        return []
    rows = []
    metric_cols = list(metric_map.values())
    all_cols = (
        list(_INCOME_MAP.values())
        + list(_BALANCE_MAP.values())
        + list(_CASHFLOW_MAP.values())
    )
    for col_date in stmt_df.columns:
        qe = pd.Timestamp(col_date).date()
        row = {
            "ticker": ticker,
            "quarter_end": qe,
            "fiscal_year": qe.year,
            "fiscal_quarter": (f"Q{(qe.month - 1) // 3 + 1}"),
            "statement_type": statement_type,
        }
        # Initialise all metric cols to None
        for c in all_cols:
            row.setdefault(c, None)
        for label, col_name in metric_map.items():
            try:
                val = stmt_df.loc[label, col_date]
                row[col_name] = float(val) if pd.notna(val) else None
            except (KeyError, ValueError, TypeError):
                row[col_name] = None
        # Skip rows where ALL mapped metrics are None
        if all(row[c] is None for c in metric_cols):
            _logger.debug(
                "Skipping all-null %s row for %s %s",
                statement_type,
                ticker,
                qe,
            )
            continue
        rows.append(row)
    return rows


def _fetch_and_store_quarterly(
    ticker: str,
    repo,
    force: bool = False,
) -> str:
    """Fetch quarterly statements and persist to Iceberg.

    Args:
        ticker: Uppercase ticker with suffix (e.g. RELIANCE.NS).
        repo: StockRepository instance.
        force: If True, skip 7-day freshness check.

    Returns:
        Summary string.
    """
    try:
        if not force:
            cached = repo.get_quarterly_results_if_fresh(
                ticker,
                days=7,
            )
            if cached is not None:
                n = len(cached)
                return (
                    f"Quarterly results for {ticker} are "
                    f"up-to-date ({n} records, fetched "
                    f"within last 7 days)."
                )

        yt = yf.Ticker(ticker)
        all_rows: list[dict] = []
        gaps: list[str] = []

        # Income Statement
        inc_rows = _extract_statement(
            yt.quarterly_income_stmt,
            _INCOME_MAP,
            "income",
            ticker,
        )
        all_rows.extend(inc_rows)
        if not inc_rows:
            gaps.append("income (no data)")

        # Balance Sheet
        bs_rows = _extract_statement(
            yt.quarterly_balance_sheet,
            _BALANCE_MAP,
            "balance",
            ticker,
        )
        all_rows.extend(bs_rows)
        if not bs_rows:
            gaps.append("balance_sheet (no data)")

        # Cash Flow
        cf_rows = _extract_statement(
            yt.quarterly_cashflow,
            _CASHFLOW_MAP,
            "cashflow",
            ticker,
        )
        all_rows.extend(cf_rows)
        if not cf_rows:
            # Fallback: try annual cashflow
            annual_cf = _extract_statement(
                yt.cashflow,
                _CASHFLOW_MAP,
                "cashflow",
                ticker,
            )
            if annual_cf:
                for r in annual_cf:
                    r["fiscal_quarter"] = "FY"
                all_rows.extend(annual_cf)
                gaps.append(
                    "cashflow (annual fallback, " f"{len(annual_cf)} years)"
                )
            else:
                gaps.append("cashflow (no data)")

        if not all_rows:
            return f"No quarterly financial data found " f"for {ticker}."

        df = pd.DataFrame(all_rows)
        repo.insert_quarterly_results(ticker, df)

        counts = df.groupby("statement_type").size().to_dict()
        parts = [f"{st}: {n}q" for st, n in sorted(counts.items())]
        msg = (
            f"Fetched quarterly results for {ticker} "
            f"— {', '.join(parts)}. "
            f"Total {len(df)} records saved."
        )
        if gaps:
            msg += f" Gaps: {', '.join(gaps)}."
            _logger.warning(
                "Quarterly data gaps for %s: %s",
                ticker,
                ", ".join(gaps),
            )
        _logger.info(msg)
        return msg

    except Exception as e:
        _logger.error(
            "fetch_quarterly_results failed for %s: %s",
            ticker,
            e,
            exc_info=True,
        )
        return f"Error fetching quarterly results " f"for '{ticker}': {e}"


@tool
def fetch_quarterly_results(ticker: str) -> str:
    """Fetch quarterly financial statements from Yahoo Finance.

    Retrieves quarterly income statement, balance sheet, and
    cash flow data and persists to the Iceberg
    ``stocks.quarterly_results`` table. Data is cached for 7 days.

    Args:
        ticker: Stock ticker symbol, e.g. ``"AAPL"``.

    Returns:
        Summary string describing fetched quarters.

    Example:
        >>> result = fetch_quarterly_results.invoke(
        ...     {"ticker": "AAPL"}
        ... )
        >>> "AAPL" in result
        True
    """
    err = validate_ticker(ticker)
    if err:
        return f"Error: {err}"
    ticker = ticker.upper().strip()
    from tools._ticker_linker import auto_link_ticker

    auto_link_ticker(ticker)
    _logger.info(
        "fetch_quarterly_results | ticker=%s",
        ticker,
    )
    repo = _require_repo()
    return _fetch_and_store_quarterly(
        ticker,
        repo,
        force=False,
    )


@tool
def list_available_stocks() -> str:
    """List all stocks currently stored in the Iceberg registry.

    Returns:
        A formatted table string, or an empty-registry message.

    Example:
        >>> result = list_available_stocks.invoke({})
        >>> isinstance(result, str)
        True
    """
    _logger.info("list_available_stocks called")
    registry = _load_registry()
    if not registry:
        return (
            "No stocks in the registry yet. "
            "Use fetch_stock_data to download "
            "and store stock data."
        )
    lines = [
        f"{'Ticker':<15} {'Rows':>6}  "
        f"{'Start':>12}  {'End':>12}  "
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

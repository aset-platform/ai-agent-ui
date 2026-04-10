"""Batch data refresh for the scheduler.

Fetches yfinance data for all tickers in parallel,
collects into DataFrames, then bulk-writes to Iceberg
in a few commits instead of thousands.

Usage::

    from backend.jobs.batch_refresh import (
        batch_data_refresh,
    )
    result = batch_data_refresh(tickers, repo, run_id)
"""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from datetime import date
from datetime import datetime as dt
from datetime import timedelta, timezone

import pandas as pd
import pyarrow as pa
import yfinance as yf

_logger = logging.getLogger(__name__)


# ── Safe-conversion helpers (match repository.py) ────
def _sf(val) -> float | None:
    """Safe float -- match repository._safe_float."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return None


def _si(val) -> int | None:
    """Safe int -- match repository._safe_int."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f):
            return None
        return int(f)
    except (ValueError, TypeError):
        return None


def _to_dt(val):
    """Convert value to datetime.date for Arrow date32."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, dt):
        return val
    if isinstance(val, dt):
        return val.date()
    if isinstance(val, pd.Timestamp):
        return val.date()
    try:
        return pd.Timestamp(val).date()
    except Exception:
        return None


def _now_utc():
    """UTC now without tzinfo (Iceberg timestamp)."""
    return dt.now(timezone.utc).replace(tzinfo=None)


def _fetch_one_ticker(
    ticker: str,
    ohlcv_start: str | None = None,
    skip_quarterly: bool = False,
    skip_dividends: bool = False,
    skip_info: bool = False,
) -> dict:
    """Fetch yfinance data for a single ticker.

    Args:
        ticker: Ticker symbol (e.g. RELIANCE.NS).
        ohlcv_start: If set, fetch OHLCV from this
            date (delta). If None, fetch full 10y.

    Returns dict with raw data (no Iceberg writes).
    Keys: ticker, ohlcv_df, info, dividends_df,
          quarterly_rows, error.
    """
    result = {
        "ticker": ticker,
        "error": None,
        "timings": {},
    }
    try:
        t0 = time.monotonic()
        yt = yf.Ticker(ticker)

        # OHLCV (skip / delta / full)
        try:
            t1 = time.monotonic()
            if ohlcv_start == "__skip__":
                result["ohlcv_df"] = pd.DataFrame()
            elif ohlcv_start:
                ohlcv = yt.history(
                    start=ohlcv_start,
                    auto_adjust=False,
                )
                if not ohlcv.empty:
                    ohlcv.index = pd.to_datetime(
                        ohlcv.index,
                    ).tz_localize(None)
                result["ohlcv_df"] = ohlcv
            else:
                ohlcv = yt.history(
                    period="10y",
                    auto_adjust=False,
                )
                if not ohlcv.empty:
                    ohlcv.index = pd.to_datetime(
                        ohlcv.index,
                    ).tz_localize(None)
                result["ohlcv_df"] = ohlcv
            result["timings"]["ohlcv"] = round(
                time.monotonic() - t1,
                2,
            )
        except Exception:
            result["ohlcv_df"] = pd.DataFrame()

        # Company info (skip if fresh today)
        if skip_info:
            result["info"] = {}
            result["timings"]["info"] = 0.0
        else:
            try:
                t1 = time.monotonic()
                result["info"] = yt.info or {}
                result["timings"]["info"] = round(
                    time.monotonic() - t1,
                    2,
                )
            except Exception:
                result["info"] = {}

        # Dividends (skip if fresh < 30d)
        if skip_dividends:
            result["dividends_df"] = pd.Series(
                dtype=float,
            )
            result["timings"]["dividends"] = 0.0
        else:
            try:
                t1 = time.monotonic()
                divs = yt.dividends
                if not divs.empty:
                    divs.index = pd.to_datetime(
                        divs.index,
                    ).tz_localize(None)
                result["dividends_df"] = divs
                result["timings"]["dividends"] = round(
                    time.monotonic() - t1,
                    2,
                )
            except Exception:
                result["dividends_df"] = pd.Series(
                    dtype=float,
                )

        # Quarterly statements (skip if fresh < 7d)
        if skip_quarterly:
            result["quarterly_rows"] = []
            result["timings"]["quarterly"] = 0.0
        try:
            if not skip_quarterly:
                t1 = time.monotonic()
                from tools.stock_data_tool import (
                    _BALANCE_MAP,
                    _CASHFLOW_MAP,
                    _INCOME_MAP,
                    _extract_statement,
                )

                all_rows: list[dict] = []
                inc = _extract_statement(
                    yt.quarterly_income_stmt,
                    _INCOME_MAP,
                    "income",
                    ticker,
                )
                all_rows.extend(inc)
                bs = _extract_statement(
                    yt.quarterly_balance_sheet,
                    _BALANCE_MAP,
                    "balance",
                    ticker,
                )
                all_rows.extend(bs)
                cf = _extract_statement(
                    yt.quarterly_cashflow,
                    _CASHFLOW_MAP,
                    "cashflow",
                    ticker,
                )
                if cf:
                    all_rows.extend(cf)
                else:
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
                result["quarterly_rows"] = all_rows
                result["timings"]["quarterly"] = round(
                    time.monotonic() - t1,
                    2,
                )
        except Exception:
            result["quarterly_rows"] = []

        result["timings"]["total"] = round(
            time.monotonic() - t0,
            2,
        )

    except Exception as exc:
        result["error"] = str(exc)

    return result


def batch_data_refresh(
    tickers: list[str],
    repo,
    run_id: str,
    cancel_event=None,
    max_workers: int = 5,
) -> dict:
    """Batch refresh: parallel fetch, bulk write.

    Args:
        tickers: List of ticker symbols.
        repo: StockRepository (for scheduler run updates
            and Iceberg writes).
        run_id: Scheduler run ID for progress tracking.
        cancel_event: Optional threading.Event for cancel.
        max_workers: Parallel fetch workers.

    Returns:
        Summary dict: tickers, fetched, written, failed,
        elapsed_s.
    """
    from tools._stock_shared import _require_repo

    stock_repo = _require_repo()
    t0 = time.monotonic()
    total = len(tickers)
    repo.update_scheduler_run(
        run_id,
        {"tickers_total": total},
    )

    # ── Pre-filter: check OHLCV freshness (single query) ─
    today = date.today()
    yesterday = today - timedelta(days=1)
    ohlcv_starts: dict[str, str | None] = {}
    try:
        from backend.db.duckdb_engine import (
            query_iceberg_df,
        )

        latest_df = query_iceberg_df(
            "stocks.ohlcv",
            "SELECT ticker, MAX(date) AS latest " "FROM ohlcv GROUP BY ticker",
        )
        latest_map: dict = {}
        if not latest_df.empty:
            for _, row in latest_df.iterrows():
                d = row["latest"]
                if hasattr(d, "date"):
                    d = d.date()
                latest_map[row["ticker"]] = d
    except Exception:
        _logger.warning(
            "[batch] DuckDB freshness query failed; " "treating all as new",
            exc_info=True,
        )
        latest_map = {}

    # Check quarterly freshness (7-day threshold)
    qtr_cutoff = today - timedelta(days=30)
    qtr_fresh: set[str] = set()
    try:
        qtr_df = query_iceberg_df(
            "stocks.quarterly_results",
            "SELECT ticker, MAX(updated_at) AS latest "
            "FROM quarterly_results GROUP BY ticker",
        )
        if not qtr_df.empty:
            for _, row in qtr_df.iterrows():
                d = row["latest"]
                if hasattr(d, "date"):
                    d = d.date()
                if d >= qtr_cutoff:
                    qtr_fresh.add(row["ticker"])
    except Exception:
        pass
    _logger.info(
        "[batch] Quarterly freshness: %d fresh " "(skip), %d need fetch",
        len(qtr_fresh),
        total - len(qtr_fresh),
    )

    # Check dividend freshness (30-day threshold)
    div_cutoff = today - timedelta(days=30)
    div_fresh: set[str] = set()
    try:
        div_df = query_iceberg_df(
            "stocks.dividends",
            "SELECT ticker, MAX(fetched_at) AS latest "
            "FROM dividends GROUP BY ticker",
        )
        if not div_df.empty:
            for _, row in div_df.iterrows():
                d = row["latest"]
                if hasattr(d, "date"):
                    d = d.date()
                if d >= div_cutoff:
                    div_fresh.add(row["ticker"])
    except Exception:
        pass
    _logger.info(
        "[batch] Dividend freshness: %d fresh " "(skip), %d need fetch",
        len(div_fresh),
        total - len(div_fresh),
    )

    # Check company_info freshness (1-day threshold)
    info_fresh: set[str] = set()
    try:
        info_df = query_iceberg_df(
            "stocks.company_info",
            "SELECT ticker, MAX(fetched_at) AS latest "
            "FROM company_info GROUP BY ticker",
        )
        if not info_df.empty:
            for _, row in info_df.iterrows():
                d = row["latest"]
                if hasattr(d, "date"):
                    d = d.date()
                if d >= yesterday:
                    info_fresh.add(row["ticker"])
    except Exception:
        pass
    _logger.info(
        "[batch] Company info freshness: %d fresh " "(skip), %d need fetch",
        len(info_fresh),
        total - len(info_fresh),
    )

    for t in tickers:
        latest = latest_map.get(t)
        if latest is not None and latest >= yesterday:
            ohlcv_starts[t] = "__skip__"
        elif latest is not None:
            ohlcv_starts[t] = str(latest)
        else:
            ohlcv_starts[t] = None

    fresh_count = sum(1 for v in ohlcv_starts.values() if v == "__skip__")
    _logger.info(
        "[batch] OHLCV freshness: %d fresh (skip), " "%d need fetch",
        fresh_count,
        total - fresh_count,
    )

    # ── Phase 1: Parallel yfinance fetch ──────────────
    _logger.info(
        "[batch] Phase 1: fetching %d tickers " "(%d workers)",
        total,
        max_workers,
    )
    results: list[dict] = []
    done = 0
    errors: list[str] = []
    cancelled = False
    lock = threading.Lock()

    def _submit_fetch(t):
        start = ohlcv_starts.get(t)
        skip_qtr = t in qtr_fresh
        skip_div = t in div_fresh
        skip_ci = t in info_fresh
        return _fetch_one_ticker(
            t,
            start,
            skip_qtr,
            skip_div,
            skip_ci,
        )

    with ThreadPoolExecutor(
        max_workers=max_workers,
    ) as pool:
        future_map = {pool.submit(_submit_fetch, t): t for t in tickers}
        for future in as_completed(future_map):
            if cancel_event and cancel_event.is_set():
                pool.shutdown(
                    wait=False,
                    cancel_futures=True,
                )
                cancelled = True
                break
            t = future_map[future]
            try:
                r = future.result()
                if r.get("error"):
                    with lock:
                        errors.append(
                            f"{t}: {r['error']}",
                        )
                else:
                    with lock:
                        results.append(r)
                    timings = r.get("timings", {})
                    if timings.get("total", 0) > 5:
                        _logger.info(
                            "[batch] slow: %s %.1fs "
                            "(ohlcv=%.1f info=%.1f "
                            "div=%.1f qtr=%.1f)",
                            t,
                            timings.get("total", 0),
                            timings.get("ohlcv", 0),
                            timings.get("info", 0),
                            timings.get(
                                "dividends",
                                0,
                            ),
                            timings.get(
                                "quarterly",
                                0,
                            ),
                        )
            except Exception as exc:
                with lock:
                    errors.append(f"{t}: {exc}")
            with lock:
                done += 1
            if done % 25 == 0 or done == total:
                _logger.info(
                    "[batch] Phase 1 progress: " "%d/%d fetched",
                    done,
                    total,
                )
                repo.update_scheduler_run(
                    run_id,
                    {"tickers_done": done},
                )

    # Phase 1 timing summary
    if results:
        all_timings = [r.get("timings", {}) for r in results]
        avg_total = sum(t.get("total", 0) for t in all_timings) / len(
            all_timings
        )
        avg_ohlcv = sum(t.get("ohlcv", 0) for t in all_timings) / len(
            all_timings
        )
        avg_info = sum(t.get("info", 0) for t in all_timings) / len(
            all_timings
        )
        avg_div = sum(t.get("dividends", 0) for t in all_timings) / len(
            all_timings
        )
        avg_qtr = sum(t.get("quarterly", 0) for t in all_timings) / len(
            all_timings
        )
        _logger.info(
            "[batch] Phase 1 avg per ticker: "
            "total=%.1fs ohlcv=%.1fs info=%.1fs "
            "div=%.1fs qtr=%.1fs",
            avg_total,
            avg_ohlcv,
            avg_info,
            avg_div,
            avg_qtr,
        )

    if cancelled:
        return _finalize(
            repo,
            run_id,
            t0,
            total,
            done,
            errors,
            "cancelled",
        )

    _logger.info(
        "[batch] Phase 1 complete: %d fetched, " "%d errors in %.1fs",
        len(results),
        len(errors),
        time.monotonic() - t0,
    )

    # ── Phase 2: Bulk OHLCV write ────────────────────
    _logger.info("[batch] Phase 2: bulk OHLCV write")
    t_phase2 = time.monotonic()
    all_ohlcv: list[pd.DataFrame] = []
    for r in results:
        ohlcv_df = r.get("ohlcv_df")
        if ohlcv_df is not None and not ohlcv_df.empty:
            ticker = r["ticker"]
            df = ohlcv_df.copy().reset_index()
            col_map = {
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Adj Close": "adj_close",
                "Volume": "volume",
            }
            df = df.rename(columns=col_map)
            df["ticker"] = ticker
            all_ohlcv.append(df)

    ohlcv_count = 0
    if all_ohlcv:
        combined = pd.concat(
            all_ohlcv,
            ignore_index=True,
        )
        # Dedup: load existing (ticker, date) pairs
        try:
            from backend.db.duckdb_engine import (
                query_iceberg_df,
            )

            existing = query_iceberg_df(
                "stocks.ohlcv",
                "SELECT ticker, date FROM ohlcv",
            )
            if not existing.empty:
                existing_keys = set(
                    zip(
                        existing["ticker"],
                        existing["date"],
                    )
                )
                combined["_date"] = pd.to_datetime(
                    combined["date"],
                ).dt.date
                mask = ~combined.apply(
                    lambda row: (
                        row["ticker"],
                        row["_date"],
                    )
                    in existing_keys,
                    axis=1,
                )
                new_only = combined[mask].drop(
                    columns=["_date"],
                )
            else:
                new_only = combined
        except Exception:
            new_only = combined

        if not new_only.empty:
            now = _now_utc()
            dates = pd.to_datetime(new_only["date"])
            adj = (
                new_only["adj_close"]
                if "adj_close" in new_only.columns
                else new_only["close"]
            )
            arrow = pa.table(
                {
                    "ticker": pa.array(
                        new_only["ticker"].tolist(),
                        pa.string(),
                    ),
                    "date": pa.array(
                        [d.date() if hasattr(d, "date") else d for d in dates],
                        pa.date32(),
                    ),
                    "open": pa.array(
                        [_sf(v) for v in new_only["open"]],
                        pa.float64(),
                    ),
                    "high": pa.array(
                        [_sf(v) for v in new_only["high"]],
                        pa.float64(),
                    ),
                    "low": pa.array(
                        [_sf(v) for v in new_only["low"]],
                        pa.float64(),
                    ),
                    "close": pa.array(
                        [_sf(v) for v in new_only["close"]],
                        pa.float64(),
                    ),
                    "adj_close": pa.array(
                        [_sf(v) for v in adj],
                        pa.float64(),
                    ),
                    "volume": pa.array(
                        [_si(v) for v in new_only["volume"]],
                        pa.int64(),
                    ),
                    "fetched_at": pa.array(
                        [now] * len(new_only),
                        pa.timestamp("us"),
                    ),
                }
            )
            stock_repo._append_rows(
                "stocks.ohlcv",
                arrow,
            )
            ohlcv_count = len(new_only)

    _logger.info(
        "[batch] OHLCV: %d new rows in %.1fs",
        ohlcv_count,
        time.monotonic() - t_phase2,
    )

    # ── Phase 3: Bulk company_info + dividends + qtr ─
    _logger.info(
        "[batch] Phase 3: bulk company_info" " + dividends + quarterly",
    )
    t_phase3 = time.monotonic()

    # --- 3a: company_info (concat all, single append) -
    ci_tables: list[pa.Table] = []
    for r in results:
        info = r.get("info", {})
        if not info:
            continue
        ticker = r["ticker"]
        now = _now_utc()
        try:
            ci_tables.append(
                pa.table(
                    {
                        "info_id": pa.array(
                            [str(uuid.uuid4())],
                            pa.string(),
                        ),
                        "ticker": pa.array(
                            [ticker],
                            pa.string(),
                        ),
                        "company_name": pa.array(
                            [
                                str(
                                    info.get(
                                        "company_name",
                                    )
                                    or info.get(
                                        "longName",
                                    )
                                    or ""
                                )
                            ],
                            pa.string(),
                        ),
                        "sector": pa.array(
                            [info.get("sector")],
                            pa.string(),
                        ),
                        "industry": pa.array(
                            [info.get("industry")],
                            pa.string(),
                        ),
                        "market_cap": pa.array(
                            [
                                _si(
                                    info.get(
                                        "market_cap",
                                    )
                                    or info.get(
                                        "marketCap",
                                    )
                                )
                            ],
                            pa.int64(),
                        ),
                        "pe_ratio": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "pe_ratio",
                                    )
                                    or info.get(
                                        "trailingPE",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "week_52_high": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "52w_high",
                                    )
                                    or info.get(
                                        "fiftyTwoWeekHigh",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "week_52_low": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "52w_low",
                                    )
                                    or info.get(
                                        "fiftyTwoWeekLow",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "current_price": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "current_price",
                                    )
                                    or info.get(
                                        "currentPrice",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "currency": pa.array(
                            [
                                str(
                                    info.get(
                                        "currency",
                                    )
                                    or "USD"
                                )
                            ],
                            pa.string(),
                        ),
                        "fetched_at": pa.array(
                            [now],
                            pa.timestamp("us"),
                        ),
                        "exchange": pa.array(
                            [info.get("exchange")],
                            pa.string(),
                        ),
                        "country": pa.array(
                            [info.get("country")],
                            pa.string(),
                        ),
                        "employees": pa.array(
                            [
                                _si(
                                    info.get(
                                        "fullTimeEmployees",
                                    )
                                )
                            ],
                            pa.int64(),
                        ),
                        "dividend_yield": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "dividendYield",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "beta": pa.array(
                            [_sf(info.get("beta"))],
                            pa.float64(),
                        ),
                        "book_value": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "bookValue",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "price_to_book": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "priceToBook",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "earnings_growth": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "earningsGrowth",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "revenue_growth": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "revenueGrowth",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "profit_margins": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "profitMargins",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "avg_volume": pa.array(
                            [
                                _si(
                                    info.get(
                                        "averageVolume",
                                    )
                                )
                            ],
                            pa.int64(),
                        ),
                        "float_shares": pa.array(
                            [
                                _si(
                                    info.get(
                                        "floatShares",
                                    )
                                )
                            ],
                            pa.int64(),
                        ),
                        "short_ratio": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "shortRatio",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "analyst_target": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "targetMeanPrice",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                        "recommendation": pa.array(
                            [
                                _sf(
                                    info.get(
                                        "recommendationMean",
                                    )
                                )
                            ],
                            pa.float64(),
                        ),
                    }
                )
            )
        except Exception:
            _logger.warning(
                "[batch] company_info build " "failed for %s",
                ticker,
                exc_info=True,
            )

    ci_count = 0
    if ci_tables:
        ci_arrow = pa.concat_tables(ci_tables)
        stock_repo._append_rows(
            "stocks.company_info",
            ci_arrow,
        )
        ci_count = len(ci_arrow)
    _logger.info(
        "[batch] company_info: %d rows",
        ci_count,
    )

    # --- 3b: dividends (concat all, dedup, append) ----
    all_divs: list[dict] = []
    for r in results:
        divs = r.get("dividends_df")
        ticker = r["ticker"]
        if divs is None or divs.empty:
            continue
        for idx, val in divs.items():
            all_divs.append(
                {
                    "ticker": ticker,
                    "ex_date": idx,
                    "dividend_amount": val,
                }
            )

    div_count = 0
    if all_divs:
        div_df = pd.DataFrame(all_divs)
        # Dedup against existing
        try:
            from backend.db.duckdb_engine import (
                query_iceberg_df,
            )

            ex_div = query_iceberg_df(
                "stocks.dividends",
                "SELECT ticker, ex_date " "FROM dividends",
            )
            if not ex_div.empty:
                ex_keys = set(
                    zip(
                        ex_div["ticker"],
                        ex_div["ex_date"],
                    )
                )
                div_df["_dt"] = pd.to_datetime(
                    div_df["ex_date"],
                ).dt.date
                mask = ~div_df.apply(
                    lambda row: (
                        row["ticker"],
                        row["_dt"],
                    )
                    in ex_keys,
                    axis=1,
                )
                div_df = div_df[mask].drop(
                    columns=["_dt"],
                )
        except Exception:
            pass

        if not div_df.empty:
            now = _now_utc()
            dates = pd.to_datetime(
                div_df["ex_date"],
            )
            arrow = pa.table(
                {
                    "ticker": pa.array(
                        div_df["ticker"].tolist(),
                        pa.string(),
                    ),
                    "ex_date": pa.array(
                        [d.date() if hasattr(d, "date") else d for d in dates],
                        pa.date32(),
                    ),
                    "dividend_amount": pa.array(
                        [_sf(v) for v in div_df["dividend_amount"]],
                        pa.float64(),
                    ),
                    "currency": pa.array(
                        ["INR"] * len(div_df),
                        pa.string(),
                    ),
                    "fetched_at": pa.array(
                        [now] * len(div_df),
                        pa.timestamp("us"),
                    ),
                }
            )
            stock_repo._append_rows(
                "stocks.dividends",
                arrow,
            )
            div_count = len(div_df)
    _logger.info(
        "[batch] dividends: %d new rows",
        div_count,
    )

    # --- 3c: quarterly (bulk delete + append) ---------
    all_qtr: list[pd.DataFrame] = []
    qtr_tickers: set[str] = set()
    for r in results:
        qtr_rows = r.get("quarterly_rows", [])
        if qtr_rows:
            qdf = pd.DataFrame(qtr_rows)
            all_qtr.append(qdf)
            qtr_tickers.add(r["ticker"])

    qtr_count = 0
    if all_qtr:
        combined_qtr = pd.concat(
            all_qtr,
            ignore_index=True,
        )
        # Bulk delete affected tickers, then append
        from pyiceberg.expressions import In

        affected = list(qtr_tickers)
        try:
            stock_repo._delete_rows(
                "stocks.quarterly_results",
                In("ticker", affected),
            )
        except Exception:
            _logger.debug(
                "[batch] quarterly bulk delete " "failed",
                exc_info=True,
            )

        now = _now_utc()
        n = len(combined_qtr)

        def _qtr_col(name, fallback=None):
            if name in combined_qtr.columns:
                return combined_qtr[name]
            return [fallback] * n

        arrow = pa.table(
            {
                "ticker": pa.array(
                    combined_qtr["ticker"].tolist(),
                    pa.string(),
                ),
                "quarter_end": pa.array(
                    [_to_dt(d) for d in combined_qtr["quarter_end"]],
                    pa.date32(),
                ),
                "fiscal_year": pa.array(
                    [_si(v) for v in combined_qtr["fiscal_year"]],
                    pa.int32(),
                ),
                "fiscal_quarter": pa.array(
                    combined_qtr["fiscal_quarter"].tolist(),
                    pa.string(),
                ),
                "statement_type": pa.array(
                    combined_qtr["statement_type"].tolist(),
                    pa.string(),
                ),
                "revenue": pa.array(
                    [_sf(v) for v in _qtr_col("revenue")],
                    pa.float64(),
                ),
                "net_income": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "net_income",
                        )
                    ],
                    pa.float64(),
                ),
                "gross_profit": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "gross_profit",
                        )
                    ],
                    pa.float64(),
                ),
                "operating_income": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "operating_income",
                        )
                    ],
                    pa.float64(),
                ),
                "ebitda": pa.array(
                    [_sf(v) for v in _qtr_col("ebitda")],
                    pa.float64(),
                ),
                "eps_basic": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "eps_basic",
                        )
                    ],
                    pa.float64(),
                ),
                "eps_diluted": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "eps_diluted",
                        )
                    ],
                    pa.float64(),
                ),
                "total_assets": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "total_assets",
                        )
                    ],
                    pa.float64(),
                ),
                "total_liabilities": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "total_liabilities",
                        )
                    ],
                    pa.float64(),
                ),
                "total_equity": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "total_equity",
                        )
                    ],
                    pa.float64(),
                ),
                "total_debt": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "total_debt",
                        )
                    ],
                    pa.float64(),
                ),
                "cash_and_equivalents": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "cash_and_equivalents",
                        )
                    ],
                    pa.float64(),
                ),
                "operating_cashflow": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "operating_cashflow",
                        )
                    ],
                    pa.float64(),
                ),
                "capex": pa.array(
                    [_sf(v) for v in _qtr_col("capex")],
                    pa.float64(),
                ),
                "free_cashflow": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "free_cashflow",
                        )
                    ],
                    pa.float64(),
                ),
                "current_assets": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "current_assets",
                        )
                    ],
                    pa.float64(),
                ),
                "current_liabilities": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "current_liabilities",
                        )
                    ],
                    pa.float64(),
                ),
                "shares_outstanding": pa.array(
                    [
                        _sf(v)
                        for v in _qtr_col(
                            "shares_outstanding",
                        )
                    ],
                    pa.float64(),
                ),
                "updated_at": pa.array(
                    [now] * n,
                    pa.timestamp("us"),
                ),
            }
        )
        stock_repo._append_rows(
            "stocks.quarterly_results",
            arrow,
        )
        qtr_count = n
    _logger.info(
        "[batch] quarterly: %d rows",
        qtr_count,
    )
    _logger.info(
        "[batch] Phase 3 complete in %.1fs",
        time.monotonic() - t_phase3,
    )

    # ── Phase 4: Registry updates ─────────────────────
    _logger.info(
        "[batch] Phase 4: updating registry",
    )
    from tools._stock_registry import (
        _update_registry,
    )
    from tools._stock_shared import _parquet_path

    for r in results:
        ticker = r["ticker"]
        ohlcv_df = r.get("ohlcv_df")
        if ohlcv_df is not None and not ohlcv_df.empty:
            try:
                file_path = _parquet_path(ticker)
                _update_registry(
                    ticker,
                    ohlcv_df,
                    file_path,
                )
            except Exception:
                pass

    return _finalize(
        repo,
        run_id,
        t0,
        total,
        done,
        errors,
        None,
    )


def _finalize(
    repo,
    run_id: str,
    t0: float,
    total: int,
    done: int,
    errors: list[str],
    forced_status: str | None,
) -> dict:
    """Write final scheduler run status."""
    elapsed = time.monotonic() - t0
    if forced_status:
        status = forced_status
    elif errors:
        status = "failed" if len(errors) > total // 2 else "success"
    else:
        status = "success"

    now = dt.now(timezone.utc)
    repo.update_scheduler_run(
        run_id,
        {
            "status": status,
            "completed_at": now,
            "tickers_done": done,
            "error_message": ("; ".join(errors[:5]) if errors else None),
        },
    )
    summary = {
        "tickers": total,
        "fetched": done - len(errors),
        "errors": len(errors),
        "status": status,
        "elapsed_s": round(elapsed, 1),
    }
    _logger.info("[batch] Complete: %s", summary)
    return summary

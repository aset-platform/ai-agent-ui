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
import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from datetime import date, timedelta, timezone
from datetime import datetime as dt

import pandas as pd
import yfinance as yf

_logger = logging.getLogger(__name__)


def _fetch_one_ticker(
    ticker: str,
    ohlcv_start: str | None = None,
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

        # Company info
        try:
            t1 = time.monotonic()
            result["info"] = yt.info or {}
            result["timings"]["info"] = round(
                time.monotonic() - t1,
                2,
            )
        except Exception:
            result["info"] = {}

        # Dividends
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

        # Quarterly statements
        try:
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
        if start == "__skip__":
            return _fetch_one_ticker(t, "__skip__")
        return _fetch_one_ticker(t, start)

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
            if done % 50 == 0 or done == total:
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
    ohlcv_count = 0
    for r in results:
        ohlcv_df = r.get("ohlcv_df")
        ticker = r["ticker"]
        if ohlcv_df is not None and not ohlcv_df.empty:
            try:
                n = stock_repo.insert_ohlcv(
                    ticker,
                    ohlcv_df,
                )
                ohlcv_count += n
            except Exception as exc:
                _logger.warning(
                    "[batch] OHLCV write failed " "for %s: %s",
                    ticker,
                    exc,
                )
    _logger.info(
        "[batch] OHLCV: %d rows written",
        ohlcv_count,
    )

    # ── Phase 3: Bulk company_info + dividends ────────
    _logger.info(
        "[batch] Phase 3: bulk company_info + " "dividends + quarterly",
    )
    for r in results:
        ticker = r["ticker"]

        # Company info
        info = r.get("info", {})
        if info:
            try:
                stock_repo.insert_company_info(
                    ticker,
                    info,
                )
            except Exception:
                pass

        # Dividends
        divs = r.get("dividends_df")
        if divs is not None and not divs.empty:
            try:
                div_df = pd.DataFrame(
                    {
                        "ex_date": divs.index,
                        "dividend": divs.values,
                    }
                )
                stock_repo.insert_dividends(
                    ticker,
                    div_df,
                )
            except Exception:
                pass

        # Quarterly
        qtr_rows = r.get("quarterly_rows", [])
        if qtr_rows:
            try:
                qtr_df = pd.DataFrame(qtr_rows)
                stock_repo.insert_quarterly_results(
                    ticker,
                    qtr_df,
                )
            except Exception:
                pass

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

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
from datetime import timezone
from datetime import datetime as dt

import pandas as pd
import yfinance as yf

_logger = logging.getLogger(__name__)


def _fetch_one_ticker(ticker: str) -> dict:
    """Fetch yfinance data for a single ticker.

    Returns dict with raw data (no Iceberg writes).
    Keys: ticker, ohlcv_df, info, dividends_df,
          quarterly_rows, error.
    """
    result = {"ticker": ticker, "error": None}
    try:
        yt = yf.Ticker(ticker)

        # OHLCV (full history)
        try:
            ohlcv = yt.history(
                period="10y",
                auto_adjust=False,
            )
            if not ohlcv.empty:
                ohlcv.index = pd.to_datetime(
                    ohlcv.index,
                ).tz_localize(None)
            result["ohlcv_df"] = ohlcv
        except Exception:
            result["ohlcv_df"] = pd.DataFrame()

        # Company info
        try:
            result["info"] = yt.info or {}
        except Exception:
            result["info"] = {}

        # Dividends
        try:
            divs = yt.dividends
            if not divs.empty:
                divs.index = pd.to_datetime(
                    divs.index,
                ).tz_localize(None)
            result["dividends_df"] = divs
        except Exception:
            result["dividends_df"] = pd.Series(
                dtype=float,
            )

        # Quarterly statements
        try:
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
        except Exception:
            result["quarterly_rows"] = []

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

    with ThreadPoolExecutor(
        max_workers=max_workers,
    ) as pool:
        future_map = {pool.submit(_fetch_one_ticker, t): t for t in tickers}
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
            except Exception as exc:
                with lock:
                    errors.append(f"{t}: {exc}")
            with lock:
                done += 1
            repo.update_scheduler_run(
                run_id,
                {"tickers_done": done},
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

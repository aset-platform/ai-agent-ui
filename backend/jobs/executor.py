"""Extensible job executor registry.

Register new job types with ``@register_job("type_name")``.
The decorated function receives ``(scope, run_id, repo)``
and is responsible for doing the work + updating the run
record's ``tickers_done`` counter.

Example::

    @register_job("gap_fill")
    def execute_gap_fill(scope, run_id, repo):
        ...
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from datetime import datetime, timezone

from market_utils import is_indian_market

_iceberg_write_lock = threading.Lock()

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

JOB_EXECUTORS: dict[str, Callable[..., None]] = {}


def register_job(job_type: str):
    """Decorator to register a job executor function."""

    def wrapper(fn: Callable[..., None]):
        JOB_EXECUTORS[job_type] = fn
        _logger.info(
            "Registered job executor: %s",
            job_type,
        )
        return fn

    return wrapper


# ------------------------------------------------------------------
# Parallel fetch infrastructure
# ------------------------------------------------------------------


def _parallel_fetch(
    tickers: list[str],
    fetch_fn,
    repo,
    run_id: str,
    cancel_event=None,
    max_workers: int = 5,
) -> tuple[int, list[str], bool]:
    """Fetch tickers in parallel with progress tracking.

    Args:
        tickers: List of ticker symbols.
        fetch_fn: Callable(ticker) that does the work.
            May raise on failure.
        repo: StockRepository for progress updates.
        run_id: Scheduler run ID.
        cancel_event: Threading event for cancellation.
        max_workers: Concurrent fetch threads.

    Returns:
        (done_count, error_list, cancelled) tuple.
    """
    done = 0
    errors: list[str] = []
    cancelled = False
    lock = threading.Lock()

    with ThreadPoolExecutor(
        max_workers=max_workers,
    ) as pool:
        future_map = {pool.submit(fetch_fn, t): t for t in tickers}
        for future in as_completed(future_map):
            if cancel_event and cancel_event.is_set():
                pool.shutdown(
                    wait=False,
                    cancel_futures=True,
                )
                cancelled = True
                break
            ticker = future_map[future]
            try:
                future.result()
            except Exception as exc:
                _logger.warning(
                    "[scheduler] %s failed: %s",
                    ticker,
                    exc,
                )
                with lock:
                    errors.append(
                        f"{ticker}: {exc}",
                    )
            with lock:
                done += 1
            repo.update_scheduler_run(
                run_id,
                {"tickers_done": done},
            )

    return done, errors, cancelled


# ------------------------------------------------------------------
# Shared helpers (scope filtering, ticker mapping, finalization)
# ------------------------------------------------------------------


def _scope_filter(
    registry: dict,
    scope: str,
) -> list[str]:
    """Filter registry tickers by market scope."""
    tickers = list(registry.keys())
    if scope == "india":
        return [
            t
            for t in tickers
            if is_indian_market(
                t,
                registry.get(t, {}).get("market"),
            )
        ]
    if scope == "us":
        return [
            t
            for t in tickers
            if not is_indian_market(
                t,
                registry.get(t, {}).get("market"),
            )
        ]
    return tickers


def _yf_ticker_map(
    registry: dict,
    tickers: list[str],
) -> dict[str, str]:
    """Build canonical → yfinance ticker mapping."""
    yf_map: dict[str, str] = {}
    for t in tickers:
        if t.endswith((".NS", ".BO")):
            continue
        meta = registry.get(t, {})
        mkt = meta.get("market", "")
        if mkt.upper() in ("NSE", "BSE", "INDIA"):
            yf_map[t] = f"{t}.NS"
    return yf_map


def _finalize_run(
    repo,
    run_id: str,
    done: int,
    total: int,
    errors: list[str],
    cancelled: bool,
) -> None:
    """Write final status to the scheduler run record."""
    if cancelled:
        status = "cancelled"
    elif errors and done > 0:
        # Treat as success with warnings when <5%
        # of tickers failed (data-quality issues).
        error_rate = len(errors) / max(done, 1)
        status = (
            "failed" if error_rate >= 0.05
            else "success"
        )
    elif errors:
        status = "failed"
    else:
        status = "success"
    now = datetime.now(timezone.utc)
    repo.update_scheduler_run(
        run_id,
        {
            "status": status,
            "completed_at": now,
            "tickers_done": done,
            "error_message": (
                "; ".join(errors[:5]) if errors else None
            ),
        },
    )
    _logger.info(
        "[scheduler] Run %s finished: %s (%d/%d)",
        run_id,
        status,
        done,
        total,
    )


# ------------------------------------------------------------------
# Built-in: data_refresh
# ------------------------------------------------------------------


@register_job("data_refresh")
def execute_data_refresh(
    scope: str,
    run_id: str,
    repo,  # StockRepository
    cancel_event=None,
) -> None:
    """Batch refresh all tickers matching *scope*.

    Uses ``batch_data_refresh`` for parallel yfinance
    fetch with sequential Iceberg writes (no commit
    conflicts). Skips technical analysis, sentiment,
    and Prophet forecast (compute-heavy; handled by
    interactive ``run_full_refresh`` or separate jobs).

    Args:
        cancel_event: Optional ``threading.Event``.
            When set, the fetch phase stops and marks
            the run as ``cancelled``.
    """
    from backend.jobs.batch_refresh import (
        batch_data_refresh,
    )

    registry = repo.get_all_registry()
    tickers = list(registry.keys())

    if scope == "india":
        tickers = [
            t
            for t in tickers
            if is_indian_market(
                t,
                registry.get(t, {}).get("market"),
            )
        ]
    elif scope == "us":
        tickers = [
            t
            for t in tickers
            if not is_indian_market(
                t,
                registry.get(t, {}).get("market"),
            )
        ]

    # Resolve canonical symbols to yfinance tickers
    yf_tickers = []
    for t in tickers:
        if t.endswith((".NS", ".BO")):
            yf_tickers.append(t)
        else:
            meta = registry.get(t, {})
            mkt = meta.get("market", "")
            if mkt.upper() in (
                "NSE",
                "BSE",
                "INDIA",
            ):
                yf_tickers.append(f"{t}.NS")
            else:
                yf_tickers.append(t)

    batch_data_refresh(
        yf_tickers,
        repo,
        run_id,
        cancel_event=cancel_event,
        max_workers=5,
    )


# ------------------------------------------------------------------
# Built-in: compute_analytics
# ------------------------------------------------------------------


@register_job("compute_analytics")
def execute_compute_analytics(
    scope: str,
    run_id: str,
    repo,  # StockRepository
    cancel_event=None,
) -> None:
    """Compute analysis summary for all tickers.

    Phase 1 — parallel OHLCV read + indicator computation
    (5 workers). Accumulates only the lightweight summary
    dicts (not full indicator DataFrames).
    Phase 2 — single bulk append of analysis_summary rows
    (748 rows, one Iceberg commit).

    Technical indicators are NOT persisted — they are
    derived columns from OHLCV, computable on-the-fly in
    <500ms per ticker. API endpoints compute them on
    demand and cache in Redis.

    Sentiment is handled by the separate ``run_sentiment``
    job to avoid LLM latency blocking analytics.

    Schedule daily after ``data_refresh`` completes.
    """
    import time
    import uuid

    import pandas as pd
    import pyarrow as pa
    from db.duckdb_engine import query_iceberg_df
    from jobs.gap_filler import (
        refresh_market_indices,
    )
    from tools._analysis_indicators import (
        _calculate_technical_indicators,
    )
    from tools._analysis_movement import (
        _analyse_price_movement,
    )
    from tools._analysis_summary import (
        _generate_summary_stats,
    )
    from tools._stock_shared import _require_repo

    stock_repo = _require_repo()
    registry = repo.get_all_registry()
    tickers = _scope_filter(registry, scope)
    yf_map = _yf_ticker_map(registry, tickers)

    # ── Pre-query: skip tickers analysed today ──────────
    today = datetime.now(timezone.utc).date()
    analysis_fresh: set[str] = set()
    try:
        adf = query_iceberg_df(
            "stocks.analysis_summary",
            "SELECT ticker, MAX(analysis_date) AS latest "
            "FROM analysis_summary GROUP BY ticker",
        )
        if not adf.empty:
            for _, row in adf.iterrows():
                d = row["latest"]
                if hasattr(d, "date"):
                    d = d.date()
                if d >= today:
                    analysis_fresh.add(row["ticker"])
    except Exception as exc:
        _logger.warning(
            "[scheduler] Analysis freshness query "
            "failed: %s",
            exc,
        )

    total = len(tickers)
    repo.update_scheduler_run(
        run_id,
        {"tickers_total": total},
    )

    # Market indices: once per run (not per ticker).
    try:
        mi_count = refresh_market_indices()
        _logger.info(
            "[scheduler] Market indices: %d rows",
            mi_count,
        )
    except Exception as exc:
        _logger.warning(
            "[scheduler] Market indices failed: %s",
            exc,
        )

    # ── Phase 1: Batch read + parallel compute ──────────
    # Filter to non-fresh tickers only.
    compute_tickers = [
        yf_map.get(t, t)
        for t in tickers
        if yf_map.get(t, t) not in analysis_fresh
    ]
    _logger.info(
        "[batch-analytics] Phase 1: %d to compute "
        "(%d fresh, skip)",
        len(compute_tickers),
        len(analysis_fresh),
    )
    t_start = time.monotonic()
    results: list[dict] = []
    errors: list[str] = []
    cancelled = False
    done = 0
    lock = threading.Lock()

    BATCH_SIZE = 50

    def _compute_one(ticker, ohlcv):
        """Compute summary for a single ticker."""
        indicators = _calculate_technical_indicators(
            ohlcv,
        )
        movement = _analyse_price_movement(indicators)
        stats = _generate_summary_stats(
            indicators,
            ticker,
        )
        return {
            **movement,
            **stats,
            "macd_signal_text": stats.get(
                "macd_signal",
            ),
            "support_levels": str(
                movement.get("support_levels", []),
            ),
            "resistance_levels": str(
                movement.get("resistance_levels", []),
            ),
        }

    for batch_start in range(
        0,
        len(compute_tickers),
        BATCH_SIZE,
    ):
        if cancel_event and cancel_event.is_set():
            cancelled = True
            break

        batch = compute_tickers[
            batch_start: batch_start + BATCH_SIZE
        ]
        _logger.info(
            "[batch-analytics] Batch %d-%d/%d "
            "(%d tickers)",
            batch_start,
            batch_start + len(batch),
            len(compute_tickers),
            len(batch),
        )

        # Single DuckDB read for entire batch.
        placeholders = ",".join(
            [f"'{t}'" for t in batch],
        )
        try:
            batch_df = query_iceberg_df(
                "stocks.ohlcv",
                "SELECT ticker, date, open, high, low, "
                "close, adj_close, volume "
                "FROM ohlcv "
                f"WHERE ticker IN ({placeholders}) "
                "ORDER BY ticker, date",
            )
        except Exception as exc:
            _logger.warning(
                "[batch-analytics] OHLCV batch read "
                "failed: %s",
                exc,
            )
            for t in batch:
                errors.append(f"{t}: OHLCV read failed")
                done += 1
            continue

        if batch_df.empty:
            for t in batch:
                done += 1
            continue

        # Group by ticker in memory.
        grouped = dict(
            tuple(batch_df.groupby("ticker")),
        )

        # Parallel compute within batch (5 workers).
        def _process(ticker):
            df = grouped.get(ticker)
            if df is None or df.empty:
                raise ValueError(
                    f"No OHLCV for {ticker}",
                )
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").set_index(
                "date",
            )
            use_adj = (
                "adj_close" in df.columns
                and df["adj_close"].notna().mean()
                > 0.5
            )
            adj_col = (
                df["adj_close"]
                if use_adj
                else df["close"]
            )
            ohlcv = pd.DataFrame(
                {
                    "Open": df["open"],
                    "High": df["high"],
                    "Low": df["low"],
                    "Close": df["close"],
                    "Adj Close": adj_col,
                    "Volume": df["volume"],
                }
            )
            ohlcv.index.name = "Date"
            return _compute_one(ticker, ohlcv)

        with ThreadPoolExecutor(
            max_workers=5,
        ) as pool:
            future_map = {
                pool.submit(_process, t): t
                for t in batch
            }
            for future in as_completed(future_map):
                t = future_map[future]
                try:
                    summary = future.result()
                    with lock:
                        results.append(
                            {
                                "ticker": t,
                                "summary": summary,
                            }
                        )
                except Exception as exc:
                    _logger.warning(
                        "[scheduler] %s failed: %s",
                        t,
                        exc,
                    )
                    errors.append(f"{t}: {exc}")
                done += 1

    # done = computed + fresh (skipped as up-to-date).
    # Errors are excluded from done count.
    done = len(results) + len(analysis_fresh)
    repo.update_scheduler_run(
        run_id,
        {"tickers_done": done},
    )

    t_phase1 = time.monotonic() - t_start
    _logger.info(
        "[batch-analytics] Phase 1 done: %d computed "
        "in %.1fs",
        len(results),
        t_phase1,
    )

    # ── Phase 2: Bulk analysis_summary append ──────────
    if results:
        _logger.info(
            "[batch-analytics] Phase 2: writing %d "
            "analysis_summary rows",
            len(results),
        )
        t_phase2 = time.monotonic()

        from stocks.repository import (
            _now_utc,
            _safe_float,
            _safe_int,
            _to_date,
        )

        now = _now_utc()
        summary_rows: list[dict] = []
        for r in results:
            summary_rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "ticker": r["ticker"],
                    **r["summary"],
                }
            )

        summary_tbl = pa.table(
            {
                "summary_id": pa.array(
                    [r["id"] for r in summary_rows],
                    pa.string(),
                ),
                "ticker": pa.array(
                    [r["ticker"]
                     for r in summary_rows],
                    pa.string(),
                ),
                "analysis_date": pa.array(
                    [today] * len(summary_rows),
                    pa.date32(),
                ),
                "bull_phase_pct": pa.array(
                    [_safe_float(r.get(
                        "bull_phase_pct"))
                     for r in summary_rows],
                    pa.float64(),
                ),
                "bear_phase_pct": pa.array(
                    [_safe_float(r.get(
                        "bear_phase_pct"))
                     for r in summary_rows],
                    pa.float64(),
                ),
                "max_drawdown_pct": pa.array(
                    [_safe_float(r.get(
                        "max_drawdown_pct"))
                     for r in summary_rows],
                    pa.float64(),
                ),
                "max_drawdown_duration_days": (
                    pa.array(
                        [_safe_int(r.get(
                            "max_drawdown_"
                            "duration_days"))
                         for r in summary_rows],
                        pa.int64(),
                    )
                ),
                "annualized_volatility_pct": (
                    pa.array(
                        [_safe_float(r.get(
                            "annualized_"
                            "volatility_pct"))
                         for r in summary_rows],
                        pa.float64(),
                    )
                ),
                "annualized_return_pct": pa.array(
                    [_safe_float(r.get(
                        "annualized_return_pct"))
                     for r in summary_rows],
                    pa.float64(),
                ),
                "sharpe_ratio": pa.array(
                    [_safe_float(r.get(
                        "sharpe_ratio"))
                     for r in summary_rows],
                    pa.float64(),
                ),
                "all_time_high": pa.array(
                    [_safe_float(r.get(
                        "all_time_high"))
                     for r in summary_rows],
                    pa.float64(),
                ),
                "all_time_high_date": pa.array(
                    [_to_date(r.get(
                        "all_time_high_date"))
                     for r in summary_rows],
                    pa.date32(),
                ),
                "all_time_low": pa.array(
                    [_safe_float(r.get(
                        "all_time_low"))
                     for r in summary_rows],
                    pa.float64(),
                ),
                "all_time_low_date": pa.array(
                    [_to_date(r.get(
                        "all_time_low_date"))
                     for r in summary_rows],
                    pa.date32(),
                ),
                "support_levels": pa.array(
                    [r.get("support_levels")
                     for r in summary_rows],
                    pa.string(),
                ),
                "resistance_levels": pa.array(
                    [r.get("resistance_levels")
                     for r in summary_rows],
                    pa.string(),
                ),
                "sma_50_signal": pa.array(
                    [r.get("sma_50_signal")
                     for r in summary_rows],
                    pa.string(),
                ),
                "sma_200_signal": pa.array(
                    [r.get("sma_200_signal")
                     for r in summary_rows],
                    pa.string(),
                ),
                "rsi_signal": pa.array(
                    [r.get("rsi_signal")
                     for r in summary_rows],
                    pa.string(),
                ),
                "macd_signal_text": pa.array(
                    [r.get("macd_signal_text")
                     for r in summary_rows],
                    pa.string(),
                ),
                "best_month": pa.array(
                    [r.get("best_month")
                     for r in summary_rows],
                    pa.string(),
                ),
                "worst_month": pa.array(
                    [r.get("worst_month")
                     for r in summary_rows],
                    pa.string(),
                ),
                "best_year": pa.array(
                    [r.get("best_year")
                     for r in summary_rows],
                    pa.string(),
                ),
                "worst_year": pa.array(
                    [r.get("worst_year")
                     for r in summary_rows],
                    pa.string(),
                ),
                "computed_at": pa.array(
                    [now] * len(summary_rows),
                    pa.timestamp("us"),
                ),
            }
        )
        # Upsert: delete existing rows for affected
        # tickers, then bulk append fresh summaries.
        from pyiceberg.expressions import In

        affected = [r["ticker"] for r in summary_rows]
        try:
            stock_repo._delete_rows(
                "stocks.analysis_summary",
                In("ticker", affected),
            )
        except Exception:
            _logger.debug(
                "Bulk delete analysis_summary failed",
                exc_info=True,
            )
        stock_repo._append_rows(
            "stocks.analysis_summary",
            summary_tbl,
        )
        _logger.info(
            "[batch-analytics] Phase 2 done: %d rows "
            "in %.1fs",
            len(summary_rows),
            time.monotonic() - t_phase2,
        )

    _finalize_run(
        repo, run_id, done, total, errors, cancelled,
    )


# ------------------------------------------------------------------
# Built-in: run_sentiment
# ------------------------------------------------------------------


@register_job("run_sentiment")
def execute_run_sentiment(
    scope: str,
    run_id: str,
    repo,  # StockRepository
    cancel_event=None,
) -> None:
    """Score sentiment for all tickers via LLM.

    Pre-queries sentiment freshness in one DuckDB scan so
    tickers already scored today are skipped entirely.

    Uses ``refresh_sentiment`` from gap_filler which calls
    ``refresh_ticker_sentiment`` (fetch headlines → LLM
    score → persist to Iceberg).

    Schedule daily after ``data_refresh`` completes.
    """
    from db.duckdb_engine import query_iceberg_df
    from jobs.gap_filler import refresh_sentiment

    registry = repo.get_all_registry()
    tickers = _scope_filter(registry, scope)
    yf_map = _yf_ticker_map(registry, tickers)

    # ── Pre-query: skip tickers scored today ────────────
    today = datetime.now(timezone.utc).date()
    sentiment_fresh: set[str] = set()
    try:
        sdf = query_iceberg_df(
            "stocks.sentiment_scores",
            "SELECT ticker, MAX(score_date) AS latest "
            "FROM sentiment_scores GROUP BY ticker",
        )
        if not sdf.empty:
            for _, row in sdf.iterrows():
                d = row["latest"]
                if hasattr(d, "date"):
                    d = d.date()
                if d >= today:
                    sentiment_fresh.add(row["ticker"])
    except Exception as exc:
        _logger.warning(
            "[scheduler] Sentiment freshness query "
            "failed: %s",
            exc,
        )

    total = len(tickers)
    repo.update_scheduler_run(
        run_id,
        {"tickers_total": total},
    )

    def _sentiment_one(ticker):
        yf_ticker = yf_map.get(ticker, ticker)

        # Skip if already scored today
        if yf_ticker in sentiment_fresh:
            _logger.info(
                "[scheduler] Sentiment %s fresh. "
                "Skipped.",
                yf_ticker,
            )
            return

        _logger.info(
            "[scheduler] Sentiment %s",
            yf_ticker,
        )
        refresh_sentiment(yf_ticker)

    done, errors, cancelled = _parallel_fetch(
        tickers,
        _sentiment_one,
        repo,
        run_id,
        cancel_event,
        max_workers=5,
    )

    _finalize_run(
        repo, run_id, done, total, errors, cancelled,
    )


# ------------------------------------------------------------------
# Built-in: run_forecasts
# ------------------------------------------------------------------


@register_job("run_forecasts")
def execute_run_forecasts(
    scope: str,
    run_id: str,
    repo,  # StockRepository
    cancel_event=None,
) -> None:
    """Run Prophet forecasts for all tickers.

    Same logic as ``run_full_refresh`` step 8 — train Prophet,
    generate forecast, compute accuracy, persist to Iceberg.
    Skips tickers whose forecast is <7 days old.

    Schedule weekly (Prophet has 7-day freshness check).
    """
    from tools._forecast_accuracy import (
        _calculate_forecast_accuracy,
        _generate_forecast_summary,
    )
    from tools._forecast_model import (
        _generate_forecast,
        _prepare_data_for_prophet,
        _train_prophet_model,
    )
    from tools._forecast_shared import (
        _load_ohlcv,
        _load_regressors_from_iceberg,
    )
    from tools._stock_shared import _require_repo

    stock_repo = _require_repo()
    registry = repo.get_all_registry()
    tickers = _scope_filter(registry, scope)
    yf_map = _yf_ticker_map(registry, tickers)
    horizon_months = 9

    total = len(tickers)
    repo.update_scheduler_run(
        run_id,
        {"tickers_total": total},
    )

    def _forecast_one(ticker):
        yf_ticker = yf_map.get(ticker, ticker)
        _logger.info(
            "[scheduler] Forecast %s",
            yf_ticker,
        )

        # Skip if forecast is <7 days old
        fc_run = stock_repo.get_latest_forecast_run(
            yf_ticker,
            horizon_months,
        )
        if fc_run:
            from datetime import timedelta

            rd = fc_run.get("run_date")
            if rd is not None:
                if hasattr(rd, "date"):
                    rd = rd.date()
                cutoff = (
                    datetime.now(timezone.utc).date()
                    - timedelta(days=7)
                )
                if rd >= cutoff:
                    _logger.info(
                        "[scheduler] Forecast %s fresh "
                        "(run_date=%s). Skipped.",
                        yf_ticker,
                        rd,
                    )
                    return

        df = _load_ohlcv(yf_ticker)
        if df is None:
            raise ValueError(
                f"No OHLCV data for {yf_ticker}",
            )

        prophet_df = _prepare_data_for_prophet(df)
        current_price = float(
            prophet_df["y"].iloc[-1],
        )

        regressors = _load_regressors_from_iceberg(
            yf_ticker,
            prophet_df,
        )

        model, train_df = _train_prophet_model(
            prophet_df,
            ticker=yf_ticker,
            regressors=regressors,
        )
        forecast_df = _generate_forecast(
            model,
            prophet_df,
            horizon_months,
            regressors=regressors,
        )

        # XGBoost ensemble correction
        from config import get_settings as _gs

        if getattr(
            _gs(),
            "ensemble_enabled",
            False,
        ):
            from tools._forecast_ensemble import (
                ensemble_forecast,
            )

            corrected = ensemble_forecast(
                model,
                train_df,
                prophet_df,
                forecast_df,
                yf_ticker,
                regressors=regressors,
            )
            if corrected is not None:
                forecast_df = corrected

        accuracy = _calculate_forecast_accuracy(
            model,
            prophet_df,
        )

        summary = _generate_forecast_summary(
            forecast_df,
            current_price,
            yf_ticker,
            horizon_months,
        )
        from datetime import date as _date

        run_date = _date.today()
        run_dict: dict = {
            "run_date": run_date,
            "sentiment": summary.get("sentiment"),
            "current_price_at_run": current_price,
        }
        for m_key in ["3m", "6m", "9m"]:
            t = summary.get("targets", {}).get(m_key)
            if t:
                run_dict[f"target_{m_key}_date"] = t.get(
                    "date",
                )
                run_dict[f"target_{m_key}_price"] = t.get(
                    "price",
                )
                run_dict[f"target_{m_key}_pct_change"] = (
                    t.get("pct_change")
                )
                run_dict[f"target_{m_key}_lower"] = t.get(
                    "lower",
                )
                run_dict[f"target_{m_key}_upper"] = t.get(
                    "upper",
                )
        if "error" not in accuracy:
            run_dict["mae"] = accuracy.get("MAE")
            run_dict["rmse"] = accuracy.get("RMSE")
            run_dict["mape"] = accuracy.get("MAPE_pct")

        with _iceberg_write_lock:
            stock_repo.insert_forecast_run(
                yf_ticker,
                horizon_months,
                run_dict,
            )
            stock_repo.insert_forecast_series(
                yf_ticker,
                horizon_months,
                run_date,
                forecast_df,
            )

    done, errors, cancelled = _parallel_fetch(
        tickers,
        _forecast_one,
        repo,
        run_id,
        cancel_event,
        max_workers=3,  # Prophet is CPU-heavy
    )

    _finalize_run(
        repo, run_id, done, total, errors, cancelled,
    )

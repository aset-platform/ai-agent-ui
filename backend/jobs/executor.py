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
import os
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
    started_at: datetime | None = None,
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
    duration = None
    if started_at:
        duration = (now - started_at).total_seconds()
    repo.update_scheduler_run(
        run_id,
        {
            "status": status,
            "completed_at": now,
            "duration_secs": duration,
            "tickers_total": total,
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
    force: bool = False,
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
        force=force,
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
    force: bool = False,
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
    if not force:
        try:
            adf = query_iceberg_df(
                "stocks.analysis_summary",
                "SELECT ticker, MAX(analysis_date) "
                "AS latest "
                "FROM analysis_summary GROUP BY ticker",
            )
            if not adf.empty:
                for _, row in adf.iterrows():
                    d = row["latest"]
                    if hasattr(d, "date"):
                        d = d.date()
                    if d >= today:
                        analysis_fresh.add(
                            row["ticker"],
                        )
        except Exception as exc:
            _logger.warning(
                "[scheduler] Analysis freshness query"
                " failed: %s",
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
    force: bool = False,
) -> None:
    """Score sentiment for all tickers via LLM.

    Smart scoring with market fallback + trickle:

    1. Fetch market-wide headlines once → score → cache.
    2. Classify tickers into hot/cold/learning:
       - Hot: had real headlines in last 10 days.
       - Learning: <10 days of sentiment history.
       - Cold: no real headlines for 10+ days.
    3. Hot + learning: full headline fetch + LLM score.
    4. Cold trickle (10-15%): headline fetch to detect
       new coverage.
    5. Remaining cold: market fallback score directly.
    6. All tickers get a score every day.
    """
    import random
    import time

    from db.duckdb_engine import query_iceberg_df
    from jobs.gap_filler import refresh_sentiment
    from tools._stock_shared import _require_repo

    stock_repo = _require_repo()
    registry = repo.get_all_registry()
    tickers = _scope_filter(registry, scope)
    yf_map = _yf_ticker_map(registry, tickers)

    today = datetime.now(timezone.utc).date()
    total = len(tickers)
    repo.update_scheduler_run(
        run_id,
        {"tickers_total": total},
    )

    # ── Step 1: Market-wide sentiment (1 LLM call) ────
    _logger.info(
        "[batch-sentiment] Fetching market-wide "
        "headlines",
    )
    market_score = 0.0
    try:
        from tools._sentiment_scorer import (
            score_headlines,
        )
        from tools._sentiment_sources import (
            fetch_market_headlines,
        )

        market_headlines = fetch_market_headlines(
            max_age_days=3,
        )
        if market_headlines:
            from jobs.gap_filler import (
                _get_scoring_llm,
            )

            llm = _get_scoring_llm()
            scored = score_headlines(
                market_headlines,
                llm=llm,
            )
            if scored is not None:
                market_score = scored
            _logger.info(
                "[batch-sentiment] Market score: "
                "%.3f (%d headlines)",
                market_score,
                len(market_headlines),
            )
        else:
            _logger.info(
                "[batch-sentiment] No market "
                "headlines found, using 0.0",
            )
    except Exception as exc:
        _logger.warning(
            "[batch-sentiment] Market score "
            "failed: %s",
            exc,
        )

    # ── Step 2: Classify tickers ──────────────────────
    # Pre-query: freshness + hot/cold classification.
    sentiment_fresh: set[str] = set()
    hot_tickers: set[str] = set()
    ticker_history_days: dict[str, int] = {}

    try:
        # Tickers scored today (skip entirely).
        if not force:
            sdf = query_iceberg_df(
                "stocks.sentiment_scores",
                "SELECT ticker, "
                "MAX(score_date) AS latest "
                "FROM sentiment_scores "
                "GROUP BY ticker",
            )
            if not sdf.empty:
                for _, row in sdf.iterrows():
                    d = row["latest"]
                    if hasattr(d, "date"):
                        d = d.date()
                    if d >= today:
                        sentiment_fresh.add(
                            row["ticker"],
                        )

        # Hot tickers: had real headlines in last 10d.
        hot_df = query_iceberg_df(
            "stocks.sentiment_scores",
            "SELECT DISTINCT ticker "
            "FROM sentiment_scores "
            "WHERE score_date >= CURRENT_DATE - 10 "
            "AND headline_count > 0 "
            "AND source = 'llm'",
        )
        if not hot_df.empty:
            hot_tickers = set(hot_df["ticker"].tolist())

        # History length per ticker (for learning).
        hist_df = query_iceberg_df(
            "stocks.sentiment_scores",
            "SELECT ticker, "
            "COUNT(DISTINCT score_date) AS days "
            "FROM sentiment_scores "
            "GROUP BY ticker",
        )
        if not hist_df.empty:
            for _, row in hist_df.iterrows():
                ticker_history_days[
                    row["ticker"]
                ] = int(row["days"])
    except Exception as exc:
        _logger.warning(
            "[batch-sentiment] Classification "
            "query failed: %s",
            exc,
        )

    # Build ticker lists.
    all_yf = [yf_map.get(t, t) for t in tickers]
    to_skip = [t for t in all_yf if t in sentiment_fresh]
    remaining = [
        t for t in all_yf if t not in sentiment_fresh
    ]

    learning = [
        t for t in remaining
        if ticker_history_days.get(t, 0) < 10
    ]
    hot = [
        t for t in remaining
        if t in hot_tickers and t not in learning
    ]
    cold = [
        t for t in remaining
        if t not in hot_tickers
        and t not in learning
    ]

    # Trickle: 15% of cold tickers sampled randomly.
    trickle_size = max(1, int(len(cold) * 0.15))
    trickle = random.sample(
        cold, min(trickle_size, len(cold)),
    ) if cold else []
    cold_skip = [t for t in cold if t not in trickle]

    _logger.info(
        "[batch-sentiment] Classification: "
        "%d fresh (skip), %d hot, %d learning, "
        "%d cold (%d trickle + %d fallback)",
        len(to_skip),
        len(hot),
        len(learning),
        len(cold),
        len(trickle),
        len(cold_skip),
    )

    # ── Step 3: Score hot + learning + trickle ────────
    # 15 workers — headline fetch is I/O bound (3 HTTP
    # calls per ticker). No per-ticker PG progress
    # updates (single update at the end).
    t_start = time.monotonic()
    check_tickers = hot + learning + trickle
    done = len(to_skip)  # Already scored today.
    errors: list[str] = []
    cancelled = False

    if check_tickers:
        with ThreadPoolExecutor(
            max_workers=15,
        ) as pool:
            future_map = {
                pool.submit(
                    refresh_sentiment, t,
                ): t
                for t in check_tickers
            }
            for future in as_completed(future_map):
                if (
                    cancel_event
                    and cancel_event.is_set()
                ):
                    pool.shutdown(
                        wait=False,
                        cancel_futures=True,
                    )
                    cancelled = True
                    break
                t = future_map[future]
                try:
                    future.result()
                except Exception as exc:
                    _logger.warning(
                        "[scheduler] %s failed: %s",
                        t,
                        exc,
                    )
                    errors.append(f"{t}: {exc}")
                done += 1

        _logger.info(
            "[batch-sentiment] Headline check "
            "done: %d tickers in %.1fs",
            len(check_tickers),
            time.monotonic() - t_start,
        )

    # Single progress update after parallel phase.
    repo.update_scheduler_run(
        run_id,
        {"tickers_done": done},
    )

    # ── Step 4: Market fallback for cold_skip ─────────
    # (Merged into Step 5 gap-fill — cold_skip tickers
    # will be caught there along with any other unscored
    # tickers. No separate insert needed.)

    # ── Step 5: Fill gaps — any ticker still without ──
    # a score today gets the market fallback. This
    # catches tickers where headline fetch returned
    # nothing and refresh_sentiment didn't insert.
    if not cancelled:
        try:
            scored_df = query_iceberg_df(
                "stocks.sentiment_scores",
                "SELECT DISTINCT ticker "
                "FROM sentiment_scores "
                "WHERE score_date = CURRENT_DATE",
            )
            scored_today = (
                set(scored_df["ticker"].tolist())
                if not scored_df.empty
                else set()
            )
            unscored = [
                t for t in all_yf
                if t not in scored_today
            ]
            if unscored:
                _logger.info(
                    "[batch-sentiment] Filling %d "
                    "unscored tickers with market "
                    "fallback (%.3f)",
                    len(unscored),
                    market_score,
                )
                # Bulk insert all fallback rows in
                # one Iceberg append (not per-ticker).
                import pyarrow as pa
                from stocks.repository import _now_utc

                now = _now_utc()
                fallback_tbl = pa.table(
                    {
                        "ticker": pa.array(
                            [t.upper() for t in unscored],
                            pa.string(),
                        ),
                        "score_date": pa.array(
                            [today] * len(unscored),
                            pa.date32(),
                        ),
                        "avg_score": pa.array(
                            [market_score]
                            * len(unscored),
                            pa.float64(),
                        ),
                        "headline_count": pa.array(
                            [0] * len(unscored),
                            pa.int32(),
                        ),
                        "source": pa.array(
                            ["market_fallback"]
                            * len(unscored),
                            pa.string(),
                        ),
                        "scored_at": pa.array(
                            [now] * len(unscored),
                            pa.timestamp("us"),
                        ),
                    }
                )
                # Upsert: delete old rows for these
                # tickers on today, then bulk append.
                from pyiceberg.expressions import (
                    And,
                    EqualTo,
                    In,
                )

                try:
                    stock_repo._delete_rows(
                        "stocks.sentiment_scores",
                        And(
                            In(
                                "ticker",
                                [t.upper()
                                 for t in unscored],
                            ),
                            EqualTo(
                                "score_date",
                                today,
                            ),
                        ),
                    )
                except Exception:
                    pass
                stock_repo._append_rows(
                    "stocks.sentiment_scores",
                    fallback_tbl,
                )
                done += len(unscored)
                _logger.info(
                    "[batch-sentiment] Fallback "
                    "inserted: %d rows",
                    len(unscored),
                )
        except Exception as exc:
            _logger.warning(
                "[batch-sentiment] Gap fill "
                "failed: %s",
                exc,
            )

    # Final progress update.
    repo.update_scheduler_run(
        run_id,
        {"tickers_done": done},
    )

    elapsed = time.monotonic() - t_start
    _logger.info(
        "[batch-sentiment] Done: %d/%d in %.1fs "
        "(hot=%d, learning=%d, trickle=%d, "
        "fallback=%d)",
        done,
        total,
        elapsed,
        len(hot),
        len(learning),
        len(trickle),
        len(cold_skip),
    )

    _finalize_run(
        repo, run_id, done, total, errors, cancelled,
    )


# ------------------------------------------------------------------
# Built-in: run_forecasts
# ------------------------------------------------------------------


def _ohlcv_from_cached(
    df: "pd.DataFrame",
) -> "pd.DataFrame | None":
    """Convert raw OHLCV DataFrame to _load_ohlcv format.

    Mirrors the transform in ``_forecast_shared._load_ohlcv``
    but skips the Iceberg read (data already in memory).
    """
    import pandas as pd

    if df.empty:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    use_adj = (
        "adj_close" in df.columns
        and df["adj_close"].notna().mean() > 0.5
    )
    df = df.dropna(subset=["close"])
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


@register_job("run_forecasts")
def execute_run_forecasts(
    scope: str,
    run_id: str,
    repo,  # StockRepository
    cancel_event=None,
    force: bool = False,
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

    _run_start = datetime.now(timezone.utc)
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

    # Pre-load all OHLCV in one batch DuckDB query
    # (~99% faster than N individual reads).
    _ohlcv_cache: dict[str, pd.DataFrame] = {}
    try:
        from backend.db.duckdb_engine import (
            query_iceberg_df,
        )

        yf_tickers_all = [
            yf_map.get(t, t) for t in tickers
        ]
        ph = ",".join(
            f"'{t}'" for t in yf_tickers_all
        )
        _t0 = datetime.now(timezone.utc)
        _bulk_ohlcv = query_iceberg_df(
            "stocks.ohlcv",
            "SELECT * FROM ohlcv "
            f"WHERE ticker IN ({ph}) "
            "ORDER BY ticker, date",
        )
        if not _bulk_ohlcv.empty:
            for tk, grp in _bulk_ohlcv.groupby(
                "ticker",
            ):
                _ohlcv_cache[str(tk)] = (
                    grp.reset_index(drop=True)
                )
        _elapsed = (
            datetime.now(timezone.utc) - _t0
        ).total_seconds()
        _logger.info(
            "[forecast] Batch OHLCV: %d tickers, "
            "%d rows in %.2fs",
            len(_ohlcv_cache),
            len(_bulk_ohlcv),
            _elapsed,
        )
    except Exception:
        _logger.warning(
            "[forecast] Batch OHLCV failed, "
            "falling back to per-ticker",
            exc_info=True,
        )

    # Pre-load all forecast runs for freshness check
    # (one DuckDB query instead of 748 Iceberg reads).
    _fc_run_cache: dict[str, dict] = {}
    try:
        _t0 = datetime.now(timezone.utc)
        _fc_df = query_iceberg_df(
            "stocks.forecast_runs",
            "SELECT ticker, horizon_months, "
            "run_date, mae, rmse, mape "
            "FROM forecast_runs "
            f"WHERE horizon_months = {horizon_months}",
        )
        if not _fc_df.empty:
            import pandas as _pd

            _fc_df["run_date"] = _pd.to_datetime(
                _fc_df["run_date"],
            )
            # Keep latest run per ticker.
            _fc_df = _fc_df.sort_values(
                "run_date", ascending=False,
            ).drop_duplicates(
                subset=["ticker"], keep="first",
            )
            for _, _r in _fc_df.iterrows():
                _fc_run_cache[str(_r["ticker"])] = (
                    _r.to_dict()
                )
        _elapsed = (
            datetime.now(timezone.utc) - _t0
        ).total_seconds()
        _logger.info(
            "[forecast] Batch forecast_runs: "
            "%d tickers in %.2fs",
            len(_fc_run_cache),
            _elapsed,
        )
    except Exception:
        _logger.warning(
            "[forecast] Batch forecast_runs "
            "failed, falling back to per-ticker",
            exc_info=True,
        )

    def _forecast_one(ticker):
        yf_ticker = yf_map.get(ticker, ticker)
        _logger.info(
            "[scheduler] Forecast %s",
            yf_ticker,
        )

        # Skip if forecast is <7 days old.
        # Uses pre-loaded cache (dict lookup) instead
        # of per-ticker Iceberg read (~2.2s → <0.001s).
        if not force:
            fc_run = _fc_run_cache.get(yf_ticker)
            if not fc_run:
                fc_run = (
                    stock_repo.get_latest_forecast_run(
                        yf_ticker,
                        horizon_months,
                    )
                )
            if fc_run:
                from datetime import timedelta

                rd = fc_run.get("run_date")
                if rd is not None:
                    if hasattr(rd, "date"):
                        rd = rd.date()
                    cutoff = (
                        datetime.now(
                            timezone.utc,
                        ).date()
                        - timedelta(days=7)
                    )
                    if rd >= cutoff:
                        _logger.info(
                            "[scheduler] Forecast"
                            " %s fresh "
                            "(run_date=%s)."
                            " Skipped.",
                            yf_ticker,
                            rd,
                        )
                        return

        # Use pre-loaded OHLCV if available.
        cached_df = _ohlcv_cache.get(yf_ticker)
        if cached_df is not None:
            df = _ohlcv_from_cached(cached_df)
        else:
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

        # Reuse accuracy from previous run if <30 days
        # old. CV drifts <1% MAPE for 95% of tickers
        # over 30 days — no need to recompute weekly.
        _prev_acc = None
        if not force:
            _prev = _fc_run_cache.get(yf_ticker)
            if _prev and _prev.get("mae"):
                from datetime import timedelta

                rd = _prev.get("run_date")
                if hasattr(rd, "date"):
                    rd = rd.date()
                elif hasattr(rd, "to_pydatetime"):
                    rd = rd.to_pydatetime().date()
                acc_cutoff = (
                    datetime.now(
                        timezone.utc,
                    ).date()
                    - timedelta(days=30)
                )
                if rd and rd >= acc_cutoff:
                    _prev_acc = {
                        "MAE": _prev["mae"],
                        "RMSE": _prev["rmse"],
                        "MAPE_pct": _prev["mape"],
                    }
                    _logger.info(
                        "[scheduler] Reusing "
                        "accuracy for %s "
                        "(run_date=%s)",
                        yf_ticker,
                        rd,
                    )

        accuracy = (
            _prev_acc
            if _prev_acc
            else _calculate_forecast_accuracy(
                model, prophet_df,
            )
        )

        # Persist backtest overlay (horizon_months=0)
        # when CV actually ran (not reused).
        _bt = accuracy.get("backtest_df")
        if _bt is not None and not _bt.empty:
            _bt = _bt.copy()
            _bt = _bt.rename(
                columns={"y": "yhat_lower"},
            )
            _bt["yhat_upper"] = _bt["yhat"]
            from datetime import date as _d

            with _write_lock:
                _pending_series.append(
                    (
                        yf_ticker,
                        0,
                        _d.today(),
                        _bt,
                    ),
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

        # Accumulate for bulk write after parallel loop.
        with _write_lock:
            _pending_runs.append(
                (yf_ticker, horizon_months, run_dict),
            )
            _pending_series.append(
                (
                    yf_ticker,
                    horizon_months,
                    run_date,
                    forecast_df,
                ),
            )

    _write_lock = threading.Lock()
    _pending_runs: list[tuple] = []
    _pending_series: list[tuple] = []

    done, errors, cancelled = _parallel_fetch(
        tickers,
        _forecast_one,
        repo,
        run_id,
        cancel_event,
        max_workers=max(os.cpu_count() // 2, 2),
    )

    # Bulk write all forecast results (2 commits
    # instead of 3 × N).
    if _pending_runs:
        _t0 = datetime.now(timezone.utc)
        stock_repo.insert_forecast_runs_batch(
            _pending_runs,
        )
        stock_repo.insert_forecast_series_batch(
            _pending_series,
        )
        _elapsed = (
            datetime.now(timezone.utc) - _t0
        ).total_seconds()
        _logger.info(
            "[forecast] Bulk write: %d runs + "
            "%d series in %.2fs",
            len(_pending_runs),
            len(_pending_series),
            _elapsed,
        )

    _finalize_run(
        repo, run_id, done, total, errors, cancelled,
        started_at=_run_start,
    )


# ------------------------------------------------------------------
# Built-in: run_piotroski
# ------------------------------------------------------------------


@register_job("run_piotroski")
def execute_run_piotroski(
    scope: str,
    run_id: str,
    repo,  # StockRepository
    cancel_event=None,
    force: bool = False,
) -> None:
    """Compute Piotroski F-Score for all tickers.

    Reads quarterly_results via DuckDB, aggregates to
    annual, scores, and writes to piotroski_scores
    Iceberg table.  Schedule monthly (aligns with 30-day
    quarterly data refresh cycle).
    """
    import asyncio

    registry = repo.get_all_registry()
    tickers = _scope_filter(registry, scope)
    yf_map = _yf_ticker_map(registry, tickers)

    # Build yf-ticker list for run_screen
    yf_tickers = []
    for t in tickers:
        yf_tickers.append(yf_map.get(t, t))

    total = len(yf_tickers)
    repo.update_scheduler_run(
        run_id, {"tickers_total": total},
    )
    _logger.info(
        "[batch-piotroski] Scoring %d tickers "
        "(scope=%s)",
        total,
        scope,
    )

    if cancel_event and cancel_event.is_set():
        _finalize_run(
            repo, run_id, 0, total, [], True,
        )
        return

    try:
        from backend.pipeline.screener.screen import (
            run_screen,
        )

        result = asyncio.run(
            run_screen(tickers=yf_tickers),
        )
        scored = result.get("scored", 0)
        failed = result.get("failed", 0)
        errors = []
        if failed > 0:
            errors.append(
                f"{failed} tickers failed scoring",
            )

        _logger.info(
            "[batch-piotroski] Done: %d scored, "
            "%d failed, %d strong, %d moderate, "
            "%d weak in %.1fs",
            scored,
            failed,
            result.get("strong", 0),
            result.get("moderate", 0),
            result.get("weak", 0),
            result.get("elapsed_s", 0),
        )

        _finalize_run(
            repo,
            run_id,
            scored,
            total,
            errors,
            False,
        )
    except Exception as exc:
        _logger.error(
            "[batch-piotroski] Failed: %s",
            exc,
            exc_info=True,
        )
        _finalize_run(
            repo, run_id, 0, total,
            [str(exc)[:500]], False,
        )


# ------------------------------------------------------------------
# Built-in: recommendations
# ------------------------------------------------------------------


@register_job("recommendations")
def execute_run_recommendations(
    scope: str,
    run_id: str,
    repo,  # StockRepository
    cancel_event=None,
    force: bool = False,
    **kwargs,
) -> None:
    """Generate LLM portfolio recommendations.

    Monthly job — runs the 3-stage smart funnel for
    every user with portfolio transactions.  Skips
    users whose latest run for this scope is <30 days
    old (unless ``force=True``).

    1. Stage 1: DuckDB pre-filter (shared, cached 1h).
    2. Stage 2: Per-user gap analysis.
    3. Stage 3: LLM reasoning pass.
    4. Write recommendation_run + recs to PG.
    5. Expire prior active recs for user.

    Errors per user are caught and logged; the batch
    continues to the next user.
    """
    import asyncio
    import uuid
    from datetime import date, timedelta

    from backend.db.engine import get_session_factory
    from backend.db.pg_stocks import (
        expire_old_recommendations,
        get_latest_recommendation_run,
        insert_recommendation_run,
        insert_recommendations,
    )
    from backend.jobs.recommendation_engine import (
        stage1_prefilter,
        stage2_gap_analysis,
        stage3_llm_reasoning,
    )
    from db.duckdb_engine import query_iceberg_df

    _FRESHNESS_DAYS = 30

    _run_start = datetime.now(timezone.utc)
    today = date.today()

    # ── Stage 1: shared pre-filter (cached) ───────────
    _logger.info("[recommendations] Stage 1: pre-filter")
    candidates_df = stage1_prefilter(scope=scope)
    if candidates_df.empty:
        _logger.warning(
            "[recommendations] No candidates after "
            "pre-filter — aborting",
        )
        _finalize_run(
            repo, run_id, 0, 0, [], False,
            started_at=_run_start,
        )
        return

    _logger.info(
        "[recommendations] Stage 1: %d candidates",
        len(candidates_df),
    )

    # ── Discover users with portfolios ────────────────
    try:
        user_df = query_iceberg_df(
            "stocks.portfolio_transactions",
            "SELECT DISTINCT user_id "
            "FROM portfolio_transactions",
        )
        user_ids = (
            user_df["user_id"].tolist()
            if not user_df.empty
            else []
        )
    except Exception as exc:
        _logger.error(
            "[recommendations] Failed to query "
            "portfolio users: %s",
            exc,
        )
        _finalize_run(
            repo, run_id, 0, 0,
            [f"user query: {exc}"], False,
            started_at=_run_start,
        )
        return

    total = len(user_ids)
    _logger.info(
        "[recommendations] %d users with portfolios",
        total,
    )
    repo.update_scheduler_run(
        run_id,
        {"tickers_total": total},
    )

    done = 0
    errors: list[str] = []
    cancelled = False
    session_factory = get_session_factory()

    for uid in user_ids:
        if cancel_event and cancel_event.is_set():
            cancelled = True
            break

        try:
            # ── Freshness gate (30 days) ──────────
            if not force:
                try:
                    from sqlalchemy.ext.asyncio import (
                        AsyncSession as _FAS,
                        async_sessionmaker as _fasm,
                        create_async_engine as _fcae,
                    )
                    from sqlalchemy.pool import (
                        NullPool as _FNP,
                    )
                    from config import get_settings as _fgs

                    async def _check_fresh():
                        _fe = _fcae(
                            _fgs().database_url,
                            poolclass=_FNP,
                        )
                        _ff = _fasm(
                            _fe, class_=_FAS,
                        )
                        async with _ff() as s:
                            r = await (
                                get_latest_recommendation_run(
                                    s, uid,
                                    scope=scope,
                                )
                            )
                        await _fe.dispose()
                        return r

                    latest = asyncio.run(
                        _check_fresh(),
                    )
                    if latest:
                        ca = latest.get("created_at")
                        if ca:
                            if isinstance(ca, str):
                                ca = (
                                    datetime.fromisoformat(
                                        ca,
                                    )
                                )
                            if ca.tzinfo is None:
                                ca = ca.replace(
                                    tzinfo=timezone.utc,
                                )
                            age = (
                                datetime.now(
                                    timezone.utc,
                                )
                                - ca
                            )
                            if (
                                age.days
                                < _FRESHNESS_DAYS
                            ):
                                _logger.info(
                                    "[recommendations]"
                                    " User %s: fresh "
                                    "(%dd ago, "
                                    "scope=%s) — skip",
                                    uid[:8],
                                    age.days,
                                    scope,
                                )
                                done += 1
                                repo.update_scheduler_run(
                                    run_id,
                                    {
                                        "tickers_done":
                                        done,
                                    },
                                )
                                continue
                except Exception as _fex:
                    _logger.debug(
                        "[recommendations] "
                        "Freshness check failed "
                        "for %s: %s",
                        uid[:8],
                        _fex,
                    )

            # Stage 2: per-user gap analysis.
            stage2 = stage2_gap_analysis(
                uid, candidates_df, repo,
                scope=scope,
            )
            portfolio = stage2.get(
                "portfolio_summary", {},
            )
            if not portfolio.get("total_holdings"):
                _logger.info(
                    "[recommendations] User %s: "
                    "empty portfolio, skip",
                    uid,
                )
                done += 1
                continue

            # Stage 3: LLM reasoning.
            stage3 = stage3_llm_reasoning(stage2)
            recs = stage3.get(
                "recommendations", [],
            )

            # Write to PG via async NullPool session.
            rec_run_id = str(uuid.uuid4())
            run_data = {
                "run_id": rec_run_id,
                "user_id": uid,
                "run_date": today,
                "run_type": "scheduled",
                "scope": scope,
                "portfolio_snapshot": portfolio,
                "health_score": stage3.get(
                    "health_score", 0,
                ),
                "health_label": stage3.get(
                    "health_label", "critical",
                ),
                "health_assessment": stage3.get(
                    "portfolio_health_assessment",
                ),
                "candidates_scanned": len(
                    candidates_df,
                ),
                "candidates_passed": len(
                    stage2.get("candidates", []),
                ),
                "llm_model": stage3.get("llm_model"),
                "llm_tokens_used": stage3.get(
                    "llm_tokens_used", 0,
                ),
            }

            from backend.db.models.recommendation import (
                Recommendation as RecModel,
                RecommendationRun as RunModel,
            )
            from sqlalchemy import (
                select as sa_select,
                update as sa_upd,
            )
            from sqlalchemy.ext.asyncio import (
                AsyncSession,
                async_sessionmaker,
                create_async_engine,
            )
            from sqlalchemy.pool import NullPool
            from config import get_settings

            async def _persist_recs():
                eng = create_async_engine(
                    get_settings().database_url,
                    poolclass=NullPool,
                )
                factory = async_sessionmaker(
                    eng, class_=AsyncSession,
                )
                # Build candidate lookup for enrichment
                cand_map = {
                    c["ticker"]: c
                    for c in stage2.get(
                        "candidates", [],
                    )
                }
                async with factory() as s:
                    s.add(RunModel(**run_data))
                    for r in recs:
                        r["run_id"] = rec_run_id
                        r["id"] = str(uuid.uuid4())
                        r.setdefault(
                            "data_signals", {},
                        )
                        r.setdefault("status", "active")
                        # Enrich with price data
                        ticker = r.get("ticker")
                        c = cand_map.get(ticker, {})
                        if not r.get("price_at_rec"):
                            r["price_at_rec"] = c.get(
                                "current_price",
                            )
                        if not r.get("target_price"):
                            r["target_price"] = c.get(
                                "target_price",
                            )
                        if not r.get(
                            "expected_return_pct",
                        ):
                            r["expected_return_pct"] = (
                                c.get("forecast_3m_pct")
                            )
                        s.add(RecModel(**r))
                    await s.commit()

                # Expire old recs
                async with factory() as s:
                    old_q = await s.execute(
                        sa_select(RunModel.run_id)
                        .where(
                            RunModel.user_id == uid,
                            RunModel.run_id
                            != rec_run_id,
                        )
                    )
                    old_ids = [
                        r[0] for r in old_q.all()
                    ]
                    if old_ids:
                        await s.execute(
                            sa_upd(RecModel)
                            .where(
                                RecModel.run_id.in_(
                                    old_ids,
                                ),
                                RecModel.status
                                == "active",
                            )
                            .values(status="expired")
                        )
                        await s.commit()
                await eng.dispose()

            asyncio.run(_persist_recs())

            _logger.info(
                "[recommendations] User %s: "
                "%d recs generated",
                uid,
                len(recs),
            )
        except Exception as exc:
            _logger.warning(
                "[recommendations] User %s "
                "failed: %s",
                uid,
                exc,
            )
            errors.append(f"{uid}: {exc}")

        done += 1
        repo.update_scheduler_run(
            run_id,
            {"tickers_done": done},
        )

    _finalize_run(
        repo, run_id, done, total,
        errors, cancelled,
        started_at=_run_start,
    )


# ------------------------------------------------------------------
# Built-in: recommendation_outcomes
# ------------------------------------------------------------------


@register_job("recommendation_outcomes")
def execute_run_recommendation_outcomes(
    scope: str,
    run_id: str,
    repo,  # StockRepository
    cancel_event=None,
    **kwargs,
) -> None:
    """Track recommendation outcomes at 30/60/90d.

    Daily job — checks which active recommendations
    are due for outcome evaluation, fetches current
    prices, computes return, labels outcome, and
    persists to PG.  Expires stale (>90d) recs.
    """
    import asyncio
    import uuid as _uuid
    from datetime import date, timedelta

    from sqlalchemy import select as sa_select, update as sa_upd, func
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import NullPool
    from config import get_settings
    from backend.db.models.recommendation import (
        Recommendation as RecModel,
        RecommendationOutcome as OutcomeModel,
    )
    from jobs.recommendation_engine import (
        compute_outcome_label,
    )
    from db.duckdb_engine import query_iceberg_df

    _run_start = datetime.now(timezone.utc)
    today = date.today()

    # Async NullPool — safe in thread pool workers.
    _eng = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
    )
    _factory = async_sessionmaker(
        _eng, class_=AsyncSession,
    )

    # ── Fetch due recommendations ─────────────────────
    async def _get_due():
        results = []
        async with _factory() as s:
            for days in (30, 60, 90):
                target = today - timedelta(days=days)
                w_start = target - timedelta(days=2)
                w_end = target + timedelta(days=2)

                existing = (
                    sa_select(
                        OutcomeModel.recommendation_id
                    )
                    .where(
                        OutcomeModel.days_elapsed
                        == days
                    )
                    .scalar_subquery()
                )

                q = await s.execute(
                    sa_select(RecModel).where(
                        RecModel.status.in_(
                            ("active", "acted_on"),
                        ),
                        RecModel.ticker.isnot(None),
                        func.date(
                            RecModel.created_at
                        ).between(w_start, w_end),
                        RecModel.id.notin_(existing),
                    )
                )
                for r in q.scalars().all():
                    results.append({
                        "id": r.id,
                        "ticker": r.ticker,
                        "action": r.action,
                        "price_at_rec": r.price_at_rec,
                        "days_due": days,
                    })
        await _eng.dispose()
        return results

    try:
        due_recs = asyncio.run(_get_due())
    except Exception as exc:
        _logger.error(
            "[rec-outcomes] Failed to query due "
            "recs: %s",
            exc,
        )
        _finalize_run(
            repo, run_id, 0, 0,
            [str(exc)[:200]], False,
            started_at=_run_start,
        )
        return

    total = len(due_recs)
    _logger.info(
        "[rec-outcomes] %d recs due for outcome",
        total,
    )
    repo.update_scheduler_run(
        run_id,
        {"tickers_total": total},
    )

    if not due_recs:
        # Expire stale recs even if none due.
        try:
            async def _expire_empty():
                eng2 = create_async_engine(
                    get_settings().database_url,
                    poolclass=NullPool,
                )
                fac2 = async_sessionmaker(
                    eng2, class_=AsyncSession,
                )
                cutoff = today - timedelta(days=90)
                async with fac2() as s:
                    result = await s.execute(
                        sa_upd(RecModel)
                        .where(
                            RecModel.status == "active",
                            func.date(
                                RecModel.created_at
                            ) < cutoff,
                        )
                        .values(status="expired")
                    )
                    await s.commit()
                    cnt = result.rowcount
                await eng2.dispose()
                return cnt

            expired = asyncio.run(_expire_empty())
            _logger.info(
                "[rec-outcomes] Expired %d stale",
                expired,
            )
        except Exception as exc:
            _logger.warning(
                "[rec-outcomes] Expire failed: %s",
                exc,
            )
        _finalize_run(
            repo, run_id, 0, 0, [], False,
            started_at=_run_start,
        )
        return

    # ── Batch fetch current prices ────────────────────
    tickers = list({
        r["ticker"] for r in due_recs
        if r.get("ticker")
    })
    price_map: dict[str, float] = {}
    if tickers:
        placeholders = ",".join(
            [f"'{t}'" for t in tickers],
        )
        try:
            price_df = query_iceberg_df(
                "stocks.ohlcv",
                "SELECT ticker, close, date "
                "FROM ohlcv "
                f"WHERE ticker IN ({placeholders}) "
                "QUALIFY ROW_NUMBER() OVER ("
                "PARTITION BY ticker "
                "ORDER BY date DESC) = 1",
            )
            if not price_df.empty:
                for _, row in price_df.iterrows():
                    price_map[row["ticker"]] = float(
                        row["close"],
                    )
        except Exception as exc:
            _logger.warning(
                "[rec-outcomes] Price fetch "
                "failed: %s",
                exc,
            )

    # ── Compute + persist outcomes ────────────────────
    done = 0
    errors: list[str] = []
    cancelled = False
    outcomes_to_insert: list[dict] = []

    for rec in due_recs:
        if cancel_event and cancel_event.is_set():
            cancelled = True
            break

        rec_id = rec.get("id", "")
        ticker = rec.get("ticker", "")
        action = rec.get("action", "hold")
        price_at_rec = rec.get("price_at_rec")
        days_due = rec.get("days_due", 30)

        current_price = price_map.get(ticker)
        if current_price is None or not price_at_rec:
            done += 1
            continue

        return_pct = (
            (current_price - price_at_rec)
            / price_at_rec
        ) * 100.0
        label = compute_outcome_label(
            action, return_pct,
        )
        bench_return = 0.0
        excess = return_pct - bench_return

        outcomes_to_insert.append({
            "id": str(_uuid.uuid4()),
            "recommendation_id": rec_id,
            "check_date": today,
            "days_elapsed": days_due,
            "actual_price": current_price,
            "return_pct": round(return_pct, 2),
            "benchmark_return_pct": round(
                bench_return, 2,
            ),
            "excess_return_pct": round(excess, 2),
            "outcome_label": label,
        })

        _logger.info(
            "[rec-outcomes] %s/%s: %.1f%% -> %s "
            "(%dd)",
            ticker,
            rec_id[:8],
            return_pct,
            label,
            days_due,
        )
        done += 1
        repo.update_scheduler_run(
            run_id,
            {"tickers_done": done},
        )

    # Bulk insert outcomes + expire stale
    if outcomes_to_insert:
        try:
            async def _bulk_insert():
                eng3 = create_async_engine(
                    get_settings().database_url,
                    poolclass=NullPool,
                )
                fac3 = async_sessionmaker(
                    eng3, class_=AsyncSession,
                )
                async with fac3() as s:
                    for o in outcomes_to_insert:
                        s.add(OutcomeModel(**o))
                    await s.commit()

                # Expire stale
                cutoff = today - timedelta(days=90)
                async with fac3() as s:
                    result = await s.execute(
                        sa_upd(RecModel)
                        .where(
                            RecModel.status == "active",
                            func.date(
                                RecModel.created_at
                            ) < cutoff,
                        )
                        .values(status="expired")
                    )
                    await s.commit()
                    expired = result.rowcount
                await eng3.dispose()
                return expired

            expired = asyncio.run(_bulk_insert())
            _logger.info(
                "[rec-outcomes] Inserted %d outcomes, "
                "expired %d stale",
                len(outcomes_to_insert),
                expired,
            )
        except Exception as exc:
            _logger.warning(
                "[rec-outcomes] Bulk insert "
                "failed: %s",
                exc,
            )
            errors.append(str(exc)[:200])

    _finalize_run(
        repo, run_id, done, total,
        errors, cancelled,
        started_at=_run_start,
    )

"""Daily EOD snapshot job for ``stocks.entry_quality_daily`` —
persists QM Score + Entry Strength Score (ESS) sub-factors for the
``allowed_tickers`` universe, per
``docs/superpowers/specs/2026-07-12-entry-strength-score-design.md``.

Standalone (not a pipeline step) scheduled job — one batched Iceberg
commit per run. Wired via ``@register_job("entry_quality_snapshot")``
in ``backend/jobs/executor.py``.

Scope note (Task 14, ASETPLTFRM Entry Strength Score plan): the
``qm_score``/``qm_*_pctile`` fields and the "QM Score >= 58" half of
the universe union (this job currently only covers
``allowed_tickers``) are intentionally left as ``None``/placeholder
in THIS task. Task 15 retrofits real values via
``compute_qm_scores(ohlcv_df) -> dict[str, QmResult]``, factored out
of ``insights_routes.py``'s post-loop QM Score block, into both this
job and the watchlist route. Shipping ESS persistence standalone
first is a deliberate, valid intermediate state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from functools import reduce
from typing import Any

import pandas as pd
import pyarrow as pa
from entry_strength_score import compute_ess, compute_nifty_market_context
from pyiceberg.expressions import And, EqualTo, Or
from sqlalchemy import text

from backend.algo._iceberg_retry import retry_iceberg_op
from backend.db.duckdb_engine import (
    invalidate_metadata,
)
from backend.db.duckdb_engine import query_iceberg_df as _query_iceberg_df_sync
from backend.db.engine import disposable_pg_session
from backend.market_utils import detect_market
from backend.tools._analysis_indicators import (
    _calculate_technical_indicators,
)

_logger = logging.getLogger(__name__)

_TABLE = "stocks.entry_quality_daily"

# Trailing per-ticker window — mirrors the live watchlist route's
# ROW_NUMBER() <= 300 window (Task 11, insights_routes.py
# get_watchlist_stocks) so this snapshot uses the same indicator
# input the user actually saw that day, not an approximation.
_TRAILING_BARS = 300
_MIN_BARS = 6

_ALLOWED_TICKERS_SQL = text("""
    SELECT DISTINCT jsonb_array_elements_text(lc.allowed_tickers) AS ticker
    FROM algo.live_caps lc
    JOIN algo.strategies s ON s.id = lc.strategy_id
    WHERE s.mode = 'live' AND s.archived_at IS NULL
    """)


async def _allowed_tickers_union() -> set[str]:
    """Distinct tickers across every live strategy's
    ``allowed_tickers`` whitelist (``algo.live_caps``, scoped to
    non-archived live-mode strategies)."""
    async with disposable_pg_session() as session:
        result = await session.execute(_ALLOWED_TICKERS_SQL)
        return {row[0] for row in result.fetchall()}


async def query_iceberg_df(
    table_name: str,
    sql: str,
    params: list | None = None,
) -> pd.DataFrame:
    """Async bridge over the sync DuckDB/Iceberg read helper.

    ``backend.db.duckdb_engine.query_iceberg_df`` is a blocking sync
    call — wrap with ``asyncio.to_thread`` so this job's own
    ``asyncio.run()`` loop isn't blocked for the batch read
    (CLAUDE.md §5.1 sync-I/O-in-async rule).
    """
    return await asyncio.to_thread(
        _query_iceberg_df_sync, table_name, sql, params
    )


def _delete_predicate(rows: list[dict[str, Any]]):
    """Build an exact OR-of-Ands predicate for the distinct
    ``(ticker, trade_date)`` pairs actually present in *rows*.

    Returns ``None`` when *rows* is empty (caller should skip the
    delete). Each term is an exact two-column conjunction — never
    a cross-product ``In(tickers) x In(trade_dates)``.

    ``trade_date`` is computed independently per ticker
    (``grp["date"].iloc[-1].date()`` — the ticker's own last
    available bar), not a single shared "as of" date for the run.
    A ticker whose OHLCV ingestion lagged behind the rest of the
    batch will have a different ``trade_date``, so
    ``And(In(tickers), In(trade_dates))`` would match
    ``(tickerA, dateB)`` combinations that were never written by
    any run and aren't part of the current batch — silently
    deleting valid historical rows with nothing to replace them.
    Mirrors ``backend/algo/stream/bars_writer.py::_dedup_predicate``.
    """
    keys = sorted({(r["ticker"], r["trade_date"]) for r in rows})
    if not keys:
        return None
    terms = [
        And(EqualTo("ticker", t), EqualTo("trade_date", d)) for (t, d) in keys
    ]
    if len(terms) == 1:
        return terms[0]
    return reduce(Or, terms)


def _append_snapshot_rows(rows: list[dict[str, Any]]) -> None:
    """NaN-replaceable upsert (Task 12 schema): scoped pre-delete on
    the incoming batch's exact ``(ticker, trade_date)`` pairs, then
    a single batched Iceberg append — mirrors
    ``daily_features_daily_compute.py``'s upsert pattern so a
    re-triggered/retried run (same ``trade_date``) never duplicates
    rows for a ticker.
    """
    from stocks.create_tables import _get_catalog

    pred = _delete_predicate(rows)

    def _do_append() -> None:
        cat = _get_catalog()
        tbl = cat.load_table(_TABLE)
        if pred is not None:
            try:
                tbl.delete(pred)
            except Exception as exc:  # noqa: BLE001
                _logger.debug(
                    "entry_quality_snapshot pre-delete skipped (%s): %s",
                    _TABLE,
                    exc,
                )
        arrow_tbl = pa.Table.from_pylist(rows, schema=tbl.schema().as_arrow())
        tbl.append(arrow_tbl)

    retry_iceberg_op(_TABLE, _do_append)
    invalidate_metadata(_TABLE)


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = await _allowed_tickers_union()

    # QM Score >= 58 universe: Task 15's exclusive scope (see module
    # docstring) — this job only covers allowed_tickers for now.
    qualifying = allowed
    if not qualifying:
        _logger.info("entry_quality_snapshot: no qualifying tickers — skip.")
        return {"rows_written": 0}

    ph = ",".join(f"'{t}'" for t in sorted(qualifying))
    ohlcv_df = await query_iceberg_df(
        "stocks.ohlcv",
        "SELECT ticker, date, open, high, low, close, volume FROM ("
        "  SELECT ticker, date, open, high, low, close, volume,"
        "  ROW_NUMBER() OVER ("
        "    PARTITION BY ticker ORDER BY date DESC"
        "  ) AS rn FROM ohlcv "
        f"  WHERE ticker IN ({ph}) AND close IS NOT NULL"
        f") WHERE rn <= {_TRAILING_BARS} "
        "ORDER BY ticker, date",
    )
    if ohlcv_df.empty:
        return {"rows_written": 0}

    nifty_df = await query_iceberg_df(
        "stocks.ohlcv",
        "SELECT date, close FROM ohlcv WHERE ticker = '^NSEI' "
        "AND close IS NOT NULL ORDER BY date DESC LIMIT 300",
    )
    nifty_ctx = compute_nifty_market_context(
        nifty_df.sort_values("date")["close"].astype(float)
    )

    written_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rows: list[dict[str, Any]] = []
    for ticker, grp in ohlcv_df.groupby("ticker"):
        grp = grp.sort_values("date")
        if len(grp) < _MIN_BARS:
            continue

        # MUST use the exact same indicator computation the
        # watchlist route uses (Task 11), not an approximation —
        # otherwise the persisted snapshot silently disagrees with
        # what the page showed that day, which breaks the §9
        # validation premise (comparing "what ESS said" against
        # real outcomes only works if it's the same number the
        # user actually saw).
        df_in = grp.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        ).set_index(pd.DatetimeIndex(grp["date"]))
        try:
            ind = _calculate_technical_indicators(df_in)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "entry_quality_snapshot: indicators failed ticker=%s: %s",
                ticker,
                exc,
                exc_info=True,
            )
            continue
        last = ind.iloc[-1]

        sma50_val = last.get("SMA_50")
        dist_sma50_pct: float | None = None
        if sma50_val is not None and not pd.isna(sma50_val) and sma50_val > 0:
            dist_sma50_pct = round(
                (float(last["Close"]) - float(sma50_val))
                / float(sma50_val)
                * 100,
                4,
            )

        sma200_val = last.get("SMA_200")
        sma200: float | None = (
            float(sma200_val)
            if sma200_val is not None and not pd.isna(sma200_val)
            else None
        )

        ess = compute_ess(
            open_=float(grp["open"].iloc[-1]),
            high=float(grp["high"].iloc[-1]),
            low=float(grp["low"].iloc[-1]),
            close=float(grp["close"].iloc[-1]),
            volume_series=grp["volume"].astype(float),
            sma50_series=ind["SMA_50"].dropna(),
            atr_series=ind["ATR_14"].dropna(),
            close_series=grp["close"].astype(float),
            sma200=sma200,
            dist_sma50_pct=dist_sma50_pct,
        )
        rows.append(
            {
                "trade_date": grp["date"].iloc[-1].date(),
                "ticker": ticker,
                "market": detect_market(ticker),
                # QM Score fields: Task 15 scope — see module
                # docstring. Left None here on purpose.
                "qm_score": None,
                "qm_sharpe_pctile": None,
                "qm_rs_pctile": None,
                "qm_mdd_pctile": None,
                "qm_atr_closeness": None,
                "qm_sma200_closeness": None,
                "ess_score": ess.ess_score,
                "ess_gate_passed": ess.gate_passed,
                "ess_gate_reason": ess.gate_reason,
                "ess_absorption_volume_score": (ess.absorption_volume_score),
                "ess_sma50_proximity_score": ess.sma50_proximity_score,
                "ess_trend_stability_score": ess.trend_stability_score,
                "ess_selling_deceleration_score": (
                    ess.selling_deceleration_score
                ),
                "ess_roc5_score": ess.roc5_score,
                "ess_atr_expansion_score": ess.atr_expansion_score,
                "nifty_return_pct": nifty_ctx.nifty_return_pct,
                "nifty_roc5_pct": nifty_ctx.nifty_roc5_pct,
                "nifty_below_sma200": nifty_ctx.nifty_below_sma200,
                "in_allowed_tickers": ticker in allowed,
                "written_at": written_at,
            }
        )

    if rows:
        _append_snapshot_rows(rows)
    return {"rows_written": len(rows)}


def run_entry_quality_snapshot_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sync entrypoint — scheduler / seed / backfill callers."""
    return asyncio.run(_run(payload or {}))

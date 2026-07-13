"""Daily EOD snapshot job for ``stocks.entry_quality_daily`` —
persists QM Score + Entry Strength Score (ESS) sub-factors for the
``allowed_tickers`` universe, per
``docs/superpowers/specs/2026-07-12-entry-strength-score-design.md``.

Standalone (not a pipeline step) scheduled job — one batched Iceberg
commit per run. Wired via ``@register_job("entry_quality_snapshot")``
in ``backend/jobs/executor.py``.

Scope note (Task 15, ASETPLTFRM Entry Strength Score plan): this job
now computes real ``qm_score``/``qm_*_pctile`` values via
``qm_score.compute_qm_scores`` (factored out of
``insights_routes.py``'s post-loop QM Score block, Task 11) instead of
the ``None`` placeholders Task 14 shipped. The five raw QM inputs
(Sharpe(6M), Blended RS, MDD(6M), ATR%, Dist-SMA200) are re-derived
per ticker here from the same OHLCV + indicators this job already
loads, mirroring the route's per-ticker formulas verbatim so the
persisted score agrees with what the Watchlist Stocks page showed
that ticker that day.

**Known limitation (design spec §8 "allowed_tickers ∪ QM Score >= 58"
universe):** this job's ticker universe is still scoped to
``allowed_tickers`` only (see ``_allowed_tickers_union`` below) — the
``qualifying`` union below can only ever equal ``allowed`` today,
because QM scores are computed from an ``ohlcv_df`` that was already
fetched scoped to ``allowed``. Discovering ADDITIONAL tickers purely
by QM Score >= 58 (i.e. tickers no live strategy currently allows)
requires a broader candidate universe fetched BEFORE this query — no
existing helper aggregates "all users' watchlist ∪ holdings"
system-wide, and scanning the full stock+ETF discovery universe
(``_full_universe`` in ``insights_routes.py``) would materially widen
this job's OHLCV read volume (CLAUDE.md §4.1 #6/#8). That candidate-
universe decision is left for a follow-up task rather than guessed at
here.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from functools import reduce
from typing import Any

import pandas as pd
import pyarrow as pa
from entry_strength_score import compute_ess, compute_nifty_market_context
from pyiceberg.expressions import And, EqualTo, Or
from qm_score import compute_qm_scores
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


def _safe(val: Any) -> float | None:
    """Convert to float or return ``None`` for NaN/inf — mirrors
    ``insights_routes.py::_safe`` verbatim so the QM raw inputs
    computed below (from ``last``/indicator Series values) round the
    same way the route's version does.
    """
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, 4)
    except (ValueError, TypeError):
        return None


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

    # QM Score >= 58 universe: see module docstring "Known
    # limitation" — this job's OHLCV fetch below is still scoped to
    # allowed_tickers only, so the union reassignment after the
    # per-ticker loop can only ever equal `allowed` today. Kept as
    # `allowed` here (rather than pre-declaring a wider set) since
    # the qualifying candidate universe is fetched, not computed,
    # and there is nothing broader to fetch yet.
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
    _nifty_close = nifty_df.sort_values("date")["close"].astype(float)
    nifty_ctx = compute_nifty_market_context(_nifty_close)

    # Nifty 6M/3M returns for RS(6M)/RS(3M) below — same series
    # already fetched above, no new query. Mirrors
    # insights_routes.py::_watchlist_stocks' verbatim.
    nifty_6m_return: float | None = None
    nifty_3m_return: float | None = None
    if len(_nifty_close) >= 20:
        nifty_6m_return = float(
            (_nifty_close.iloc[-1] - _nifty_close.iloc[0])
            / _nifty_close.iloc[0]
            * 100
        )
        if len(_nifty_close) >= 64:
            nifty_3m_return = float(
                (_nifty_close.iloc[-1] - _nifty_close.iloc[-64])
                / _nifty_close.iloc[-64]
                * 100
            )

    written_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rows: list[dict[str, Any]] = []
    qm_inputs: dict[str, dict[str, float | None]] = {}
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

        # QM Score raw inputs — Sharpe(6M), RS(6M)/RS(3M) vs. Nifty,
        # Blended RS, MDD(6M), ATR%, Dist-SMA200. Formulas copied
        # verbatim from insights_routes.py::_watchlist_stocks' per-
        # ticker block (Task 11) so this job's QM Score agrees with
        # what the route showed the user that day (see module
        # docstring).
        _close_s = grp["close"].astype(float)
        sharpe: float | None = None
        stock_6m_return: float | None = None
        stock_3m_return: float | None = None
        try:
            _rets = _close_s.pct_change().dropna()
            _rets6 = _rets.iloc[-126:]
            if len(_rets6) >= 20:
                _std = float(_rets6.std())
                if _std > 0:
                    sharpe = round(float(_rets6.mean()) / _std * (252**0.5), 4)
            _c6 = _close_s.iloc[-127:]
            if len(_c6) >= 2:
                stock_6m_return = float(
                    (_c6.iloc[-1] - _c6.iloc[0]) / _c6.iloc[0] * 100
                )
            _c3 = _close_s.iloc[-64:]
            if len(_c3) >= 2:
                stock_3m_return = float(
                    (_c3.iloc[-1] - _c3.iloc[0]) / _c3.iloc[0] * 100
                )
        except Exception:  # noqa: BLE001
            pass

        rs_6m: float | None = None
        if stock_6m_return is not None and nifty_6m_return is not None:
            rs_6m = round(stock_6m_return - nifty_6m_return, 4)

        rs_3m: float | None = None
        if stock_3m_return is not None and nifty_3m_return is not None:
            rs_3m = round(stock_3m_return - nifty_3m_return, 4)

        blended_rs: float | None = None
        if rs_3m is not None and rs_6m is not None:
            blended_rs = round(0.6 * rs_3m + 0.4 * rs_6m, 4)

        mdd_6m: float | None = None
        try:
            _c6m = _close_s.iloc[-126:]
            if len(_c6m) >= 2:
                _peak = _c6m.cummax()
                _dd = (_c6m - _peak) / _peak * 100
                mdd_6m = round(float(_dd.min()), 4)
        except Exception:  # noqa: BLE001
            pass

        _atr14 = _safe(last.get("ATR_14"))
        _close_val = _safe(last.get("Close"))
        atr_pct: float | None = None
        if _atr14 is not None and _close_val is not None and _close_val > 0:
            atr_pct = round(_atr14 / _close_val * 100, 4)

        _sma200_safe = _safe(last.get("SMA_200"))
        dist_sma200: float | None = None
        if (
            _close_val is not None
            and _sma200_safe is not None
            and _sma200_safe > 0
        ):
            dist_sma200 = round(
                (_close_val - _sma200_safe) / _sma200_safe * 100, 4
            )

        qm_inputs[ticker] = {
            "sharpe_ratio": sharpe,
            "blended_rs": blended_rs,
            "mdd_6m": mdd_6m,
            "atr_pct": atr_pct,
            "dist_sma200": dist_sma200,
        }

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
                # QM Score fields: patched below, after
                # compute_qm_scores runs once across the full batch
                # (percentile ranks need the whole cross-stock set,
                # not a single ticker in isolation).
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

    qm_results = compute_qm_scores(qm_inputs)
    for row in rows:
        qm = qm_results.get(row["ticker"])
        if qm is not None:
            row["qm_score"] = qm.score
            row["qm_sharpe_pctile"] = qm.sharpe_pctile
            row["qm_rs_pctile"] = qm.rs_pctile
            row["qm_mdd_pctile"] = qm.mdd_pctile
            row["qm_atr_closeness"] = qm.atr_closeness
            row["qm_sma200_closeness"] = qm.sma200_closeness

    # See module docstring "Known limitation" — today this can only
    # ever equal `allowed` (qm_results is keyed by the same tickers
    # `ohlcv_df` was already scoped to), but is written as an
    # explicit union so a future widened candidate-universe fetch
    # only needs to change what feeds `qm_inputs`, not this line.
    qualifying = allowed | {
        t
        for t, r in qm_results.items()
        if r.score is not None and r.score >= 58
    }
    _logger.debug(
        "entry_quality_snapshot: qualifying=%d (allowed=%d)",
        len(qualifying),
        len(allowed),
    )

    if rows:
        _append_snapshot_rows(rows)
    return {"rows_written": len(rows)}


def run_entry_quality_snapshot_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sync entrypoint — scheduler / seed / backfill callers."""
    return asyncio.run(_run(payload or {}))

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

**Full-universe candidate scope (Task 15 follow-up, design spec §8
"allowed_tickers ∪ QM Score >= 58" universe):** the OHLCV fetch below
is scoped to ``allowed_tickers UNION the full platform stock+ETF
universe`` (``insights_routes.py::_full_universe``), not
``allowed_tickers`` alone. Task 14/early-Task-15 shipped a version
scoped to ``allowed`` only, which made the ``qualifying`` union a
structural no-op — QM Score could never discover a ticker beyond what
was already allowed, since OHLCV (and therefore QM inputs) was never
fetched for anything else. This is a genuinely larger daily read
(~500 tickers x 300 bars instead of ~20-250 x 300) — an accepted,
deliberate cost of restoring the original design intent, not
something to truncate or work around (CLAUDE.md §4.1 #1/#8 batch-read
+ windowed-query rules are still honored: single ``WHERE ticker IN
(...)`` + ``ROW_NUMBER() OVER (PARTITION BY ticker) <= 300``, just
over a wider ticker set).

**QM Score cohort trade-off (deliberate, not a bug):** QM Score's
percentile-rank sub-factors (Sharpe/RS/MDD) are computed RELATIVE TO
whichever batch of tickers is scored together in one
``compute_qm_scores`` call. The Watchlist Stocks PAGE scores within
the user's watchlist-scoped cohort (small, ~20-100 tickers); this job
now scores the FULL candidate universe (~500 tickers) in ONE call, so
a persisted ``qm_score`` for an ``allowed_tickers`` row can differ
from what the user saw on the page that day. This is intentional: a
single, large, stable full-universe cohort makes the persisted
historical dataset comparable day-over-day (the page's cohort shifts
as the user edits their watchlist; the full universe doesn't), which
better serves this table's effectiveness-measurement goal (design
spec §9) than agreement with the page. Every persisted row — including
``allowed_tickers`` rows — uses the SAME full-universe-cohort score;
this job does not attempt to replicate the page's smaller cohort.
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


async def _full_universe_tickers() -> list[str]:
    """Platform-wide tradeable stock+ETF universe — mirrors
    ``insights_routes.py::_full_universe`` verbatim so this job's
    candidate scope agrees with what the Insights discovery tabs show
    pro/superuser users.

    ``StockRepository.get_all_registry()`` (reached via
    ``tools._stock_shared._require_repo``) is a synchronous, PG-backed
    call. It already handles running-loop offload internally
    (``stocks/repository.py::_run_pg`` detects a running loop and
    dispatches to a worker thread with its own NullPool engine), but
    per CLAUDE.md §5.1's sync-I/O-in-async-routes rule this is still
    wrapped in ``asyncio.to_thread`` here so it never blocks this
    job's own ``asyncio.run()`` loop while the PG round-trip is in
    flight.

    ``_require_repo`` is a process-wide module-level singleton getter
    with no FastAPI request-scoped dependency-injection state — safe
    to call from this scheduler-job context exactly as it's called
    from the Insights route.
    """
    from insights_routes import _full_universe
    from tools._stock_shared import _require_repo

    def _call() -> list[str]:
        return _full_universe(_require_repo())

    return await asyncio.to_thread(_call)


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
    try:
        universe = await _full_universe_tickers()
    except Exception:
        _logger.error(
            "entry_quality_snapshot: full-universe fetch failed — "
            "degrading to allowed_tickers only.",
            exc_info=True,
        )
        universe = []

    # Candidate universe for the OHLCV fetch + QM Score batch: the
    # full platform stock+ETF universe UNIONED with allowed_tickers
    # (see module docstring "Full-universe candidate scope"). Union
    # in `allowed` defensively — a live strategy's allowed_tickers
    # whitelist could in principle reference a ticker outside the
    # discovery registry (e.g. delisted after being allow-listed),
    # and today's behavior guarantees ESS/QM coverage for every
    # allowed ticker regardless; dropping that guarantee here would
    # be a silent regression.
    candidates = set(universe) | allowed
    if not candidates:
        _logger.info("entry_quality_snapshot: no candidate tickers — skip.")
        return {"rows_written": 0}

    ph = ",".join(f"'{t}'" for t in sorted(candidates))
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
    qm_inputs: dict[str, dict[str, float | None]] = {}
    # Per-ticker ESS inputs, cached from this same loop so the
    # (much smaller) `qualifying` pass below can call `compute_ess`
    # without a third OHLCV fetch or re-running indicators — see
    # module docstring. Indicators (`ind`) are needed for every
    # candidate ticker anyway (QM's raw inputs depend on them), so
    # caching just the ESS-relevant slices here is cheap relative to
    # the indicator computation itself.
    ess_ctx: dict[str, dict[str, Any]] = {}
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

        ess_ctx[ticker] = {
            "open_": float(grp["open"].iloc[-1]),
            "high": float(grp["high"].iloc[-1]),
            "low": float(grp["low"].iloc[-1]),
            "close": float(grp["close"].iloc[-1]),
            "volume_series": grp["volume"].astype(float),
            "sma50_series": ind["SMA_50"].dropna(),
            "atr_series": ind["ATR_14"].dropna(),
            "close_series": grp["close"].astype(float),
            "sma200": sma200,
            "dist_sma50_pct": dist_sma50_pct,
            "trade_date": grp["date"].iloc[-1].date(),
        }

    # QM Score computed ONCE across the full candidate-universe batch
    # — see module docstring "QM Score cohort trade-off". Every
    # persisted row (including allowed_tickers rows) uses this same
    # full-universe-cohort score; this job does not attempt to
    # replicate the Watchlist Stocks page's smaller, user-scoped
    # cohort.
    qm_results = compute_qm_scores(qm_inputs)

    # Genuine, non-trivial union: `qualifying` can now include
    # tickers that were never in `allowed` at all, purely because
    # their full-universe-cohort QM Score cleared the >=58 bar (the
    # bug this task fixes — Task 14/early-Task-15 could only ever
    # have `qualifying == allowed` since QM was never computed for
    # anything outside `allowed`).
    qualifying = allowed | {
        t
        for t, r in qm_results.items()
        if r.score is not None and r.score >= 58
    }
    _logger.debug(
        "entry_quality_snapshot: qualifying=%d (allowed=%d, "
        "universe=%d, candidates=%d)",
        len(qualifying),
        len(allowed),
        len(universe),
        len(candidates),
    )

    rows: list[dict[str, Any]] = []
    for ticker in sorted(qualifying):
        ctx = ess_ctx.get(ticker)
        if ctx is None:
            # In `qualifying` but no valid OHLCV/indicators this run
            # (e.g. fewer than `_MIN_BARS` bars, or indicator calc
            # failed) — nothing to persist without ESS inputs.
            continue

        ess = compute_ess(
            open_=ctx["open_"],
            high=ctx["high"],
            low=ctx["low"],
            close=ctx["close"],
            volume_series=ctx["volume_series"],
            sma50_series=ctx["sma50_series"],
            atr_series=ctx["atr_series"],
            close_series=ctx["close_series"],
            sma200=ctx["sma200"],
            dist_sma50_pct=ctx["dist_sma50_pct"],
        )
        qm = qm_results.get(ticker)
        rows.append(
            {
                "trade_date": ctx["trade_date"],
                "ticker": ticker,
                "market": detect_market(ticker),
                "qm_score": qm.score if qm is not None else None,
                "qm_sharpe_pctile": (
                    qm.sharpe_pctile if qm is not None else None
                ),
                "qm_rs_pctile": qm.rs_pctile if qm is not None else None,
                "qm_mdd_pctile": qm.mdd_pctile if qm is not None else None,
                "qm_atr_closeness": (
                    qm.atr_closeness if qm is not None else None
                ),
                "qm_sma200_closeness": (
                    qm.sma200_closeness if qm is not None else None
                ),
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

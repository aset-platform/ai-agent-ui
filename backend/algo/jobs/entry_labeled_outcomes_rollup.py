"""Daily rollup job — materializes ``algo.entry_labeled_outcomes``
(the Release-2 entry-strength calibration training set, PRE-6 /
ASETPLTFRM-480) from live data.

Runs off-hours (16:00 IST Mon-Fri, see the Task-2 seed script), well
after market close, so a filled candidate's exit has settled.

Per CLAUDE.md §5.1: reads Iceberg via ``query_iceberg_table`` (never
during market hours), writes Postgres via ``disposable_pg_session``
(NullPool, per-call).

Re-derives the labeled set from a trailing window every run (default
400 days, matching the OHLCV warmup convention) rather than carrying
forward state — a candidate can be rejected on day N and its
snapshot/rejection pair still needs to be joined against a fill that
settles days later. The idempotent unique key
(user_id, strategy_id, ticker, trade_date, mode) — constraint
``uq_entry_labeled_outcomes_signal`` — on ``algo.entry_labeled_
outcomes`` makes re-running safe: ``INSERT ... ON CONFLICT ...
DO UPDATE`` re-materializes rather than duplicating.

Grain: one row per candidate signal. A candidate is either FILLED
(a real trade closed in ``algo.closed_trades``) or REJECTED (an
``entry_strength_snapshot`` with no matching fill, paired with a
``signal_rejected`` reason if one was emitted). Every candidate row
REQUIRES an ``entry_strength_snapshot`` for its features — a fill
with no snapshot (pre-Release-2 history) still materializes with
features NULL, but for the recurring job every live fill has a
snapshot (the shadow snapshot is emitted unconditionally just before
every non-vetoed BUY, see ``live/runtime.py``).

v1 scope: rejected candidates get NULL outcome fields
(``outcome_kind='counterfactual'``, ``outcome_settled=false``) —
exit-simulated counterfactual outcome values for candidates that were
never filled are deferred to PRE-3. Do NOT compute them here.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

_logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_DAYS = 400
_INTRADAY_INTERVAL_SEC = 900


def _ist_today() -> date:
    return (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()


def _to_float(value: Any) -> float | None:
    """Best-effort numeric coercion.

    ``entry_strength_snapshot`` payload values round-trip through
    ``json.dumps(..., default=str)`` — some numeric fields (e.g.
    ``rsi2_forming``, ``dist_sma50``) arrive as decimal STRINGS
    (``"4.1581764..."``), not floats. NaN/Inf and unparseable
    values coerce to ``None`` rather than propagating a poisoned
    number into the labeled dataset.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _scale_pct(value: Any) -> float | None:
    """``dist_sma50``/``dist_sma200`` in the snapshot payload are
    FRACTIONS (e.g. ``0.2555`` = 25.55%) — scale ×100 into the
    ``*_pct`` columns. ``ret_3d_pct``/``gap_pct`` are already
    percent and must NOT be scaled (handled via ``_to_float``)."""
    f = _to_float(value)
    return f * 100 if f is not None else None


def _bare_ticker(ticker: str) -> str:
    """Strip a market suffix — ``algo.closed_trades`` and the
    snapshot/rejection payloads may carry either form; the final
    labeled row always stores the bare symbol (matches
    ``algo.closed_trades.ticker`` convention)."""
    if ticker.endswith(".NS") or ticker.endswith(".BO"):
        return ticker.rsplit(".", 1)[0]
    return ticker


def _to_ns_ticker(ticker: str) -> str:
    """Normalize to the ``.NS``-suffixed form ``stocks.*`` tables
    key on (India-only universe; see CLAUDE.md §4.3 #19)."""
    if ticker.endswith(".NS") or ticker.endswith(".BO"):
        return ticker
    return f"{ticker}.NS"


def run_entry_labeled_outcomes_rollup_job(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sync entrypoint (scheduler / seed / backfill callers).

    Payload keys (all optional):
      - ``window_days``: trailing lookback for both the Iceberg
        scans and the ``algo.closed_trades`` PG read. Default 400.
      - ``today``: ISO date override (testing). Default IST-today.
      - ``dry_run``: compute + log counts, skip the PG upsert.
    """
    import asyncio

    return asyncio.run(_run(payload or {}))


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.db.engine import disposable_pg_session

    today = (
        date.fromisoformat(payload["today"])
        if payload.get("today")
        else _ist_today()
    )
    window_days = payload.get("window_days", _DEFAULT_WINDOW_DAYS)
    dry_run = bool(payload.get("dry_run", False))
    start = today - timedelta(days=window_days)

    snapshots = _fetch_snapshots(start, today)
    rejections = _fetch_rejections(start, today)

    async with disposable_pg_session() as session:
        fills = await _fetch_closed_trades(session, start, today)

    candidate_tickers = sorted({
        _to_ns_ticker(t) for t in (
            {k[2] for k in snapshots} | {f["ticker"] for f in fills}
        )
    })
    eqd = _fetch_eqd(candidate_tickers, start, today)
    outcomes = _compute_outcomes(fills)

    rows = _assemble_rows(snapshots, rejections, fills, eqd, outcomes)

    _logger.info(
        "entry-labeled-outcomes-rollup: today=%s window_days=%s "
        "snapshots=%d rejections=%d fills=%d candidates=%d "
        "dry_run=%s",
        today.isoformat(), window_days, len(snapshots),
        len(rejections), len(fills), len(rows), dry_run,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "today": today.isoformat(),
            "window_days": window_days,
            "snapshots_scanned": len(snapshots),
            "rejections_scanned": len(rejections),
            "fills_computed": len(fills),
            "candidates_computed": len(rows),
        }

    upserted = 0
    if rows:
        async with disposable_pg_session() as session:
            for row in rows:
                result = await session.execute(text(_UPSERT_SQL), row)
                if result.rowcount:
                    upserted += 1
            await session.commit()

    return {
        "status": "ok",
        "today": today.isoformat(),
        "window_days": window_days,
        "snapshots_scanned": len(snapshots),
        "rejections_scanned": len(rejections),
        "fills_computed": len(fills),
        "candidates_computed": len(rows),
        "rows_upserted": upserted,
    }


# --------------------------------------------------------------- #
# Iceberg reads
# --------------------------------------------------------------- #


def _fetch_snapshots(
    start: date, today: date,
) -> dict[tuple[str, str, str, date], dict[str, Any]]:
    """Group ``entry_strength_snapshot`` events by (strategy_id,
    mode, ticker, ts_date); keep the FIRST (min ts_ns) per group as
    the representative row — rows are scanned in ``ts_ns`` order so
    the first dict-set wins."""
    from backend.db.duckdb_engine import query_iceberg_table

    sql = (
        "SELECT ts_ns, ts_date, user_id, strategy_id, mode, "
        "       payload_json "
        "FROM events "
        "WHERE type = 'entry_strength_snapshot' "
        "  AND ts_date >= ? AND ts_date <= ? "
        "ORDER BY ts_ns"
    )
    params = [start.isoformat(), today.isoformat()]
    try:
        raw = query_iceberg_table("algo.events", sql, params)
    except Exception:  # noqa: BLE001
        _logger.exception(
            "entry-labeled-outcomes-rollup: snapshot query failed",
        )
        return {}

    out: dict[tuple[str, str, str, date], dict[str, Any]] = {}
    for row in raw:
        if not row.get("strategy_id"):
            continue
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, ValueError):
            continue
        ticker = _bare_ticker(str(payload.get("ticker") or ""))
        if not ticker:
            continue
        ts_date = row["ts_date"]
        if isinstance(ts_date, str):
            ts_date = date.fromisoformat(ts_date)
        key = (str(row["strategy_id"]), row["mode"], ticker, ts_date)
        if key in out:
            continue
        out[key] = {
            "user_id": row.get("user_id"),
            "signal_ts_ns": row.get("ts_ns"),
            "trigger": payload.get("trigger"),
            "rsi2_at_entry": _to_float(payload.get("rsi2_forming")),
            "dist_sma50_pct": _scale_pct(payload.get("dist_sma50")),
            "dist_sma200_pct": _scale_pct(payload.get("dist_sma200")),
            "ret_3d_prior": _to_float(payload.get("ret_3d_pct")),
            "gap_pct": _to_float(payload.get("gap_pct")),
            "breadth_oversold": payload.get("breadth_oversold"),
            "breadth_total": payload.get("breadth_total"),
            "dry_run": bool(payload.get("dry_run", False)),
        }
    return out


def _fetch_rejections(
    start: date, today: date,
) -> dict[tuple[str, str, str, date], str]:
    """Per (strategy_id, mode, ticker, ts_date), take a
    representative ``signal_rejected`` reason (first by ts_ns)."""
    from backend.db.duckdb_engine import query_iceberg_table

    sql = (
        "SELECT ts_ns, ts_date, strategy_id, mode, payload_json "
        "FROM events "
        "WHERE type = 'signal_rejected' "
        "  AND ts_date >= ? AND ts_date <= ? "
        "ORDER BY ts_ns"
    )
    params = [start.isoformat(), today.isoformat()]
    try:
        raw = query_iceberg_table("algo.events", sql, params)
    except Exception:  # noqa: BLE001
        _logger.exception(
            "entry-labeled-outcomes-rollup: rejection query failed",
        )
        return {}

    out: dict[tuple[str, str, str, date], str] = {}
    for row in raw:
        if not row.get("strategy_id"):
            continue
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except (TypeError, ValueError):
            continue
        ticker = _bare_ticker(str(payload.get("ticker") or ""))
        reason = payload.get("reason")
        if not ticker or not reason:
            continue
        ts_date = row["ts_date"]
        if isinstance(ts_date, str):
            ts_date = date.fromisoformat(ts_date)
        key = (str(row["strategy_id"]), row["mode"], ticker, ts_date)
        out.setdefault(key, reason)
    return out


def _fetch_eqd(
    tickers_ns: list[str], start: date, today: date,
) -> dict[tuple[str, date], dict[str, Any]]:
    """QM/ESS join source, keyed by bare ticker + trade_date.

    Batched single read (CLAUDE.md §4.1 #1) scoped to the union of
    every candidate/fill ticker (``.NS``-normalized) in this run —
    never a full-universe scan."""
    from backend.db.duckdb_engine import query_iceberg_table

    if not tickers_ns:
        return {}
    placeholders = ", ".join("?" for _ in tickers_ns)
    sql = (
        "SELECT ticker, trade_date, qm_score, ess_score, "
        "       ess_gate_passed, qm_mdd_pctile, qm_rs_pctile, "
        "       qm_sharpe_pctile, ess_absorption_volume_score, "
        "       ess_selling_deceleration_score, "
        "       ess_trend_stability_score "
        "FROM entry_quality_daily "
        f"WHERE ticker IN ({placeholders}) "
        "  AND trade_date >= ? AND trade_date <= ?"
    )
    params = [*tickers_ns, start.isoformat(), today.isoformat()]
    try:
        raw = query_iceberg_table(
            "stocks.entry_quality_daily", sql, params,
        )
    except Exception:  # noqa: BLE001
        _logger.exception(
            "entry-labeled-outcomes-rollup: entry_quality_daily "
            "query failed",
        )
        return {}

    out: dict[tuple[str, date], dict[str, Any]] = {}
    for row in raw:
        ticker = _bare_ticker(str(row.get("ticker") or ""))
        trade_date = row.get("trade_date")
        if isinstance(trade_date, str):
            trade_date = date.fromisoformat(trade_date)
        if not ticker or trade_date is None:
            continue
        out[(ticker, trade_date)] = {
            k: row.get(k) for k in (
                "qm_score", "ess_score", "ess_gate_passed",
                "qm_mdd_pctile", "qm_rs_pctile", "qm_sharpe_pctile",
                "ess_absorption_volume_score",
                "ess_selling_deceleration_score",
                "ess_trend_stability_score",
            )
        }
    return out


def _fetch_intraday_bars(
    tickers_ns: list[str], ts_lo: int, ts_hi: int,
) -> dict[str, list[dict[str, Any]]]:
    """Batched single read (CLAUDE.md §4.1 #1) — ``ticker IN
    (...)`` bounded by the union of every filled trade's
    opened..closed window, never a per-trade query."""
    from backend.db.duckdb_engine import query_iceberg_table

    if not tickers_ns or ts_hi < ts_lo:
        return {}
    placeholders = ", ".join("?" for _ in tickers_ns)
    sql = (
        "SELECT ticker, bar_open_ts_ns, high, low "
        "FROM intraday_bars "
        f"WHERE ticker IN ({placeholders}) "
        "  AND interval_sec = ? "
        "  AND bar_open_ts_ns >= ? AND bar_open_ts_ns <= ?"
    )
    params = [*tickers_ns, _INTRADAY_INTERVAL_SEC, ts_lo, ts_hi]
    try:
        raw = query_iceberg_table(
            "stocks.intraday_bars", sql, params,
        )
    except Exception:  # noqa: BLE001
        _logger.exception(
            "entry-labeled-outcomes-rollup: intraday_bars query "
            "failed",
        )
        return {}

    out: dict[str, list[dict[str, Any]]] = {}
    for row in raw:
        out.setdefault(row["ticker"], []).append(row)
    return out


def _fetch_daily_ohlcv(
    tickers_ns: list[str], date_lo: date, date_hi: date,
) -> dict[str, list[dict[str, Any]]]:
    """Batched single read bounded by the union of every filled
    trade's opened..closed date range (daily fallback for MFE/MAE
    when no intraday coverage exists)."""
    from backend.db.duckdb_engine import query_iceberg_table

    if not tickers_ns:
        return {}
    placeholders = ", ".join("?" for _ in tickers_ns)
    sql = (
        "SELECT ticker, date, high, low FROM ohlcv "
        f"WHERE ticker IN ({placeholders}) "
        "  AND date >= ? AND date <= ?"
    )
    params = [*tickers_ns, date_lo.isoformat(), date_hi.isoformat()]
    try:
        raw = query_iceberg_table("stocks.ohlcv", sql, params)
    except Exception:  # noqa: BLE001
        _logger.exception(
            "entry-labeled-outcomes-rollup: ohlcv query failed",
        )
        return {}

    out: dict[str, list[dict[str, Any]]] = {}
    for row in raw:
        d = row.get("date")
        if isinstance(d, str):
            d = date.fromisoformat(d)
        row["date"] = d
        out.setdefault(row["ticker"], []).append(row)
    return out


# --------------------------------------------------------------- #
# Postgres read + aggregation (fills)
# --------------------------------------------------------------- #


async def _fetch_closed_trades(
    session: Any, start: date, today: date,
) -> list[dict[str, Any]]:
    """Read ``algo.closed_trades`` lots in the window and aggregate
    same-day multi-lot fills into one row per (user_id, strategy_id,
    ticker, opened_at, mode)."""
    result = await session.execute(
        text(
            "SELECT user_id, strategy_id, mode, ticker, qty, "
            "       avg_price, fill_price, opened_at, closed_at, "
            "       opened_at_ts_ns, closed_at_ts_ns, "
            "       realised_pnl_inr, exit_reason, dry_run, "
            "       buy_event_id, sell_event_id "
            "FROM algo.closed_trades "
            "WHERE opened_at >= :start AND opened_at <= :today"
        ),
        {"start": start, "today": today},
    )
    lots = [dict(row._mapping) for row in result]
    return _aggregate_fills(lots)


def _aggregate_fills(
    lots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pure aggregation helper (unit-testable with plain dicts).

    Groups ``algo.closed_trades`` lots by (user_id, strategy_id,
    ticker, opened_at, mode) and rolls a multi-lot same-day fill
    (e.g. KTKBANK 2026-06-24 x3, WABAG 2026-07-17 x2) into ONE
    aggregated row: ``qty=Σqty``, ``entry_price=Σ(avg_price·qty)/
    Σqty``, ``exit_price=Σ(fill_price·qty)/Σqty``,
    ``realised_pnl_inr=Σpnl``, ``return_pct=(exit/entry-1)·100``,
    ``opened_at_ts_ns=min``, ``closed_at_ts_ns=max``,
    ``exit_reason``/``closed_at``=last lot to close,
    ``buy_event_id``/``sell_event_id``=first lot to close.
    """
    groups: dict[tuple[str, str, str, date, str], list[dict]] = {}
    for lot in lots:
        key = (
            str(lot["user_id"]), str(lot["strategy_id"]),
            lot["ticker"], lot["opened_at"], lot["mode"],
        )
        groups.setdefault(key, []).append(lot)

    out: list[dict[str, Any]] = []
    for (user_id, strategy_id, ticker, opened_at, mode), grp in (
        groups.items()
    ):
        grp_sorted = sorted(
            grp, key=lambda r: int(r.get("closed_at_ts_ns") or 0),
        )
        total_qty = sum(int(r["qty"]) for r in grp_sorted)
        if total_qty <= 0:
            continue
        entry_price = (
            sum(
                float(r["avg_price"]) * int(r["qty"])
                for r in grp_sorted
            ) / total_qty
        )
        exit_price = (
            sum(
                float(r["fill_price"]) * int(r["qty"])
                for r in grp_sorted
            ) / total_qty
        )
        realised_pnl_inr = sum(
            float(r["realised_pnl_inr"]) for r in grp_sorted
        )
        return_pct = (
            (exit_price / entry_price - 1) * 100
            if entry_price else None
        )
        out.append({
            "user_id": user_id,
            "strategy_id": strategy_id,
            "mode": mode,
            "ticker": ticker,
            "trade_date": opened_at,
            "qty": total_qty,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "realised_pnl_inr": realised_pnl_inr,
            "return_pct": return_pct,
            "opened_at_ts_ns": min(
                int(r.get("opened_at_ts_ns") or 0)
                for r in grp_sorted
            ),
            "closed_at_ts_ns": max(
                int(r.get("closed_at_ts_ns") or 0)
                for r in grp_sorted
            ),
            "closed_at": max(r["closed_at"] for r in grp_sorted),
            "exit_reason": grp_sorted[-1]["exit_reason"],
            "buy_event_id": grp_sorted[0]["buy_event_id"],
            "sell_event_id": grp_sorted[0]["sell_event_id"],
            "dry_run": bool(grp_sorted[0].get("dry_run", False)),
        })
    return out


# --------------------------------------------------------------- #
# MFE / MAE (filled rows only)
# --------------------------------------------------------------- #


def _compute_outcomes(
    fills: list[dict[str, Any]],
) -> dict[tuple[str, str, str, date], dict[str, Any]]:
    """Intraday-15m MFE/MAE bounded by opened..closed ts_ns, daily
    fallback when no intraday coverage exists. ``outcome_src`` ∈
    {intraday15m, daily, none}."""
    if not fills:
        return {}

    tickers_ns = sorted({_to_ns_ticker(f["ticker"]) for f in fills})
    ts_lo = min(int(f.get("opened_at_ts_ns") or 0) for f in fills)
    ts_hi = max(int(f.get("closed_at_ts_ns") or 0) for f in fills)
    intraday_by_ticker = _fetch_intraday_bars(tickers_ns, ts_lo, ts_hi)

    date_lo = min(f["trade_date"] for f in fills)
    date_hi = max(
        f.get("closed_at") or f["trade_date"] for f in fills
    )
    daily_by_ticker = _fetch_daily_ohlcv(tickers_ns, date_lo, date_hi)

    out: dict[tuple[str, str, str, date], dict[str, Any]] = {}
    for f in fills:
        key = (
            f["strategy_id"], f["mode"], f["ticker"],
            f["trade_date"],
        )
        entry_price = f.get("entry_price")
        ticker_ns = _to_ns_ticker(f["ticker"])
        ts_lo_f = int(f.get("opened_at_ts_ns") or 0)
        ts_hi_f = int(f.get("closed_at_ts_ns") or 0)

        bars = [
            b for b in intraday_by_ticker.get(ticker_ns, [])
            if ts_lo_f <= b["bar_open_ts_ns"] <= ts_hi_f
        ]
        if bars and entry_price:
            high = max(b["high"] for b in bars)
            low = min(b["low"] for b in bars)
            out[key] = {
                "mfe_pct": (high / entry_price - 1) * 100,
                "mae_pct": (low / entry_price - 1) * 100,
                "outcome_src": "intraday15m",
            }
            continue

        day_lo = f["trade_date"]
        day_hi = f.get("closed_at") or f["trade_date"]
        dbars = [
            b for b in daily_by_ticker.get(ticker_ns, [])
            if day_lo <= b["date"] <= day_hi
        ]
        if dbars and entry_price:
            high = max(b["high"] for b in dbars)
            low = min(b["low"] for b in dbars)
            out[key] = {
                "mfe_pct": (high / entry_price - 1) * 100,
                "mae_pct": (low / entry_price - 1) * 100,
                "outcome_src": "daily",
            }
            continue

        out[key] = {
            "mfe_pct": None,
            "mae_pct": None,
            "outcome_src": "none",
        }
    return out


# --------------------------------------------------------------- #
# Assembly (pure — unit-testable with plain dicts)
# --------------------------------------------------------------- #


def _assemble_rows(
    snapshots: dict[tuple[str, str, str, date], dict[str, Any]],
    rejections: dict[tuple[str, str, str, date], str],
    fills: list[dict[str, Any]],
    eqd: dict[tuple[str, date], dict[str, Any]],
    outcomes: dict[tuple[str, str, str, date], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union candidates keyed on (strategy_id, mode, ticker,
    trade_date) from snapshots + fills, joining QM/ESS and (for
    filled rows) MFE/MAE. A candidate requires a snapshot for its
    features; a fill with no snapshot still materializes (features
    NULL) — see module docstring."""
    fills_by_key: dict[tuple[str, str, str, date], dict[str, Any]] = {
        (f["strategy_id"], f["mode"], f["ticker"], f["trade_date"]): f
        for f in fills
    }

    keys = set(snapshots) | set(fills_by_key)
    rows: list[dict[str, Any]] = []
    for key in keys:
        strategy_id, mode, ticker, trade_date = key
        snap = snapshots.get(key) or {}
        fill = fills_by_key.get(key)

        user_id = (fill or {}).get("user_id") or snap.get("user_id")
        if not user_id:
            _logger.warning(
                "entry-labeled-outcomes-rollup: skipping "
                "strategy_id=%s mode=%s ticker=%s trade_date=%s — "
                "no user_id on snapshot or fill",
                strategy_id, mode, ticker, trade_date,
            )
            continue

        row: dict[str, Any] = {
            "user_id": user_id,
            "strategy_id": strategy_id or None,
            "mode": mode,
            "ticker": ticker,
            "trade_date": trade_date,
            "signal_ts_ns": snap.get("signal_ts_ns"),
            "trigger": snap.get("trigger"),
            "rsi2_at_entry": snap.get("rsi2_at_entry"),
            "dist_sma50_pct": snap.get("dist_sma50_pct"),
            "dist_sma200_pct": snap.get("dist_sma200_pct"),
            "ret_1d_prior": None,
            "ret_3d_prior": snap.get("ret_3d_prior"),
            "gap_pct": snap.get("gap_pct"),
            "breadth_oversold": snap.get("breadth_oversold"),
            "breadth_total": snap.get("breadth_total"),
        }

        eqd_row = eqd.get((ticker, trade_date), {})
        row.update({
            "qm_score": eqd_row.get("qm_score"),
            "ess_score": eqd_row.get("ess_score"),
            "ess_gate_passed": eqd_row.get("ess_gate_passed"),
            "qm_mdd_pctile": eqd_row.get("qm_mdd_pctile"),
            "qm_rs_pctile": eqd_row.get("qm_rs_pctile"),
            "qm_sharpe_pctile": eqd_row.get("qm_sharpe_pctile"),
            "ess_absorption_volume_score": eqd_row.get(
                "ess_absorption_volume_score",
            ),
            "ess_selling_deceleration_score": eqd_row.get(
                "ess_selling_deceleration_score",
            ),
            "ess_trend_stability_score": eqd_row.get(
                "ess_trend_stability_score",
            ),
        })

        if fill is not None:
            realised = fill.get("realised_pnl_inr")
            row.update({
                "filled": True,
                "rejection_reason": None,
                "buy_event_id": fill.get("buy_event_id"),
                "sell_event_id": fill.get("sell_event_id"),
                "entry_price": fill.get("entry_price"),
                "exit_price": fill.get("exit_price"),
                "exit_reason": fill.get("exit_reason"),
                "outcome_kind": "real",
                "outcome_src": None,
                "mfe_pct": None,
                "mae_pct": None,
                "realised_pnl_inr": realised,
                "return_pct": fill.get("return_pct"),
                "outcome_settled": True,
                "label_win": (
                    realised is not None and float(realised) > 0
                ),
                "dry_run": bool(fill.get("dry_run", False)),
            })
            out = outcomes.get(key)
            if out:
                row["mfe_pct"] = out.get("mfe_pct")
                row["mae_pct"] = out.get("mae_pct")
                row["outcome_src"] = out.get("outcome_src")
        else:
            row.update({
                "filled": False,
                "rejection_reason": rejections.get(key),
                "buy_event_id": None,
                "sell_event_id": None,
                "entry_price": None,
                "exit_price": None,
                "exit_reason": None,
                "outcome_kind": "counterfactual",
                "outcome_src": None,
                "mfe_pct": None,
                "mae_pct": None,
                "realised_pnl_inr": None,
                "return_pct": None,
                "outcome_settled": False,
                "label_win": None,
                "dry_run": bool(snap.get("dry_run", False)),
            })

        rows.append(row)

    rows.sort(
        key=lambda r: (
            str(r["strategy_id"]), r["mode"], r["ticker"],
            r["trade_date"],
        ),
    )
    return rows


_UPSERT_SQL = """
INSERT INTO algo.entry_labeled_outcomes AS elo (
    user_id, strategy_id, mode, ticker, trade_date,
    signal_ts_ns, trigger, rsi2_at_entry, dist_sma50_pct,
    dist_sma200_pct, ret_1d_prior, ret_3d_prior, gap_pct,
    breadth_oversold, breadth_total, qm_score, ess_score,
    ess_gate_passed, qm_mdd_pctile, qm_rs_pctile, qm_sharpe_pctile,
    ess_absorption_volume_score, ess_selling_deceleration_score,
    ess_trend_stability_score, filled, rejection_reason,
    buy_event_id, sell_event_id, entry_price, exit_price,
    exit_reason, outcome_kind, outcome_src, mfe_pct, mae_pct,
    realised_pnl_inr, return_pct, outcome_settled, label_win,
    dry_run
) VALUES (
    :user_id, :strategy_id, :mode, :ticker, :trade_date,
    :signal_ts_ns, :trigger, :rsi2_at_entry, :dist_sma50_pct,
    :dist_sma200_pct, :ret_1d_prior, :ret_3d_prior, :gap_pct,
    :breadth_oversold, :breadth_total, :qm_score, :ess_score,
    :ess_gate_passed, :qm_mdd_pctile, :qm_rs_pctile,
    :qm_sharpe_pctile, :ess_absorption_volume_score,
    :ess_selling_deceleration_score, :ess_trend_stability_score,
    :filled, :rejection_reason, :buy_event_id, :sell_event_id,
    :entry_price, :exit_price, :exit_reason, :outcome_kind,
    :outcome_src, :mfe_pct, :mae_pct, :realised_pnl_inr,
    :return_pct, :outcome_settled, :label_win, :dry_run
)
ON CONFLICT ON CONSTRAINT uq_entry_labeled_outcomes_signal
DO UPDATE SET
    -- Enrichment (feature / QM / ESS) columns: COALESCE onto the
    -- existing row. A fill with no matching entry_strength_snapshot
    -- (or a trade_date outside stocks.entry_quality_daily coverage)
    -- has NULL EXCLUDED.* for these columns — a straight EXCLUDED
    -- assignment would overwrite a PRE-1 reconstructed value with
    -- NULL on every re-run (2026-08-10 data-loss bug: this job
    -- nulled all 70 PRE-1-seeded feature columns on its very first
    -- run). ``elo`` (the INSERT INTO alias) qualifies the existing
    -- row — a bare column name is AMBIGUOUS between the target row
    -- and EXCLUDED (confirmed via psql), not an implicit reference
    -- to the target row as the docs' phrasing suggests.
    signal_ts_ns = COALESCE(EXCLUDED.signal_ts_ns, elo.signal_ts_ns),
    trigger = COALESCE(EXCLUDED.trigger, elo.trigger),
    rsi2_at_entry = COALESCE(
        EXCLUDED.rsi2_at_entry, elo.rsi2_at_entry
    ),
    dist_sma50_pct = COALESCE(
        EXCLUDED.dist_sma50_pct, elo.dist_sma50_pct
    ),
    dist_sma200_pct = COALESCE(
        EXCLUDED.dist_sma200_pct, elo.dist_sma200_pct
    ),
    ret_1d_prior = COALESCE(EXCLUDED.ret_1d_prior, elo.ret_1d_prior),
    ret_3d_prior = COALESCE(EXCLUDED.ret_3d_prior, elo.ret_3d_prior),
    gap_pct = COALESCE(EXCLUDED.gap_pct, elo.gap_pct),
    breadth_oversold = COALESCE(
        EXCLUDED.breadth_oversold, elo.breadth_oversold
    ),
    breadth_total = COALESCE(
        EXCLUDED.breadth_total, elo.breadth_total
    ),
    qm_score = COALESCE(EXCLUDED.qm_score, elo.qm_score),
    ess_score = COALESCE(EXCLUDED.ess_score, elo.ess_score),
    ess_gate_passed = COALESCE(
        EXCLUDED.ess_gate_passed, elo.ess_gate_passed
    ),
    qm_mdd_pctile = COALESCE(
        EXCLUDED.qm_mdd_pctile, elo.qm_mdd_pctile
    ),
    qm_rs_pctile = COALESCE(
        EXCLUDED.qm_rs_pctile, elo.qm_rs_pctile
    ),
    qm_sharpe_pctile = COALESCE(
        EXCLUDED.qm_sharpe_pctile, elo.qm_sharpe_pctile
    ),
    ess_absorption_volume_score = COALESCE(
        EXCLUDED.ess_absorption_volume_score,
        elo.ess_absorption_volume_score
    ),
    ess_selling_deceleration_score = COALESCE(
        EXCLUDED.ess_selling_deceleration_score,
        elo.ess_selling_deceleration_score
    ),
    ess_trend_stability_score = COALESCE(
        EXCLUDED.ess_trend_stability_score,
        elo.ess_trend_stability_score
    ),
    filled = EXCLUDED.filled,
    rejection_reason = EXCLUDED.rejection_reason,
    buy_event_id = EXCLUDED.buy_event_id,
    sell_event_id = EXCLUDED.sell_event_id,
    entry_price = EXCLUDED.entry_price,
    exit_price = EXCLUDED.exit_price,
    exit_reason = EXCLUDED.exit_reason,
    outcome_kind = EXCLUDED.outcome_kind,
    outcome_src = EXCLUDED.outcome_src,
    mfe_pct = EXCLUDED.mfe_pct,
    mae_pct = EXCLUDED.mae_pct,
    realised_pnl_inr = EXCLUDED.realised_pnl_inr,
    return_pct = EXCLUDED.return_pct,
    outcome_settled = EXCLUDED.outcome_settled,
    label_win = EXCLUDED.label_win,
    dry_run = EXCLUDED.dry_run,
    computed_at = now()
"""

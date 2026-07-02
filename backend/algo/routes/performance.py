"""GET /v1/algo/performance/runs — strategy-vs-strategy aggregate
(legacy, kept for backward compatibility with any direct callers).

GET /v1/algo/performance/summary — mode-aware, strategy-scoped,
trade-level performance. Backtest/Walk-forward read
algo.runs.summary_json.trade_list (existing, already has a
capital-based equity curve + max_drawdown_pct). Paper/Live read
the new algo.closed_trades table (backend/algo/jobs/
closed_trades_rollup.py), which has no capital baseline, so
max_drawdown_pct is null for those two modes — the page instead
surfaces biggest_win / biggest_loss, which the user confirmed is
the metric they actually want ("which trade we book for max
loss").
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from cache import TTL_STABLE, get_cache
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_logger = logging.getLogger(__name__)

_BACKTEST_FAMILY = ("backtest", "walkforward")
_LOOKBACK_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def _get_session_factory():
    from backend.db.engine import get_session_factory

    return get_session_factory()


def _ist_today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _resolve_window(
    lookback: str | None,
    start: date | None,
    end: date | None,
) -> tuple[date, date]:
    """Resolve the effective (start, end) date window.

    ``lookback`` wins if both a preset and a custom range are
    somehow present (client bug) — logged, not raised, so a
    malformed request doesn't break the page.
    """
    today = _ist_today()
    if lookback and (start or end):
        _logger.warning(
            "performance/summary: both lookback=%s and "
            "start/end given — lookback wins",
            lookback,
        )
        start = end = None
    if lookback:
        if lookback == "all":
            return date(2000, 1, 1), today
        days = _LOOKBACK_DAYS[lookback]
        return today - timedelta(days=days), today
    if start and end:
        return start, end
    # Default when nothing is specified.
    return today - timedelta(days=30), today


def _aggregate_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute win/loss/biggest-win/biggest-loss/profit-factor from
    a list of trade dicts. Shared across the backtest/walkforward
    (from summary_json.trade_list) and paper/live (from
    algo.closed_trades) sources — both are coerced to a common
    shape with at least ``ticker``, ``realised_pnl_inr``,
    ``closed_at`` before reaching this function."""
    n = len(trades)
    if n == 0:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "total_pnl_inr": 0.0,
            "biggest_win": None,
            "biggest_loss": None,
            "avg_win_inr": None,
            "avg_loss_inr": None,
            "profit_factor": None,
        }
    pnls = [float(t["realised_pnl_inr"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls)
    best_idx = max(range(n), key=lambda i: pnls[i])
    worst_idx = min(range(n), key=lambda i: pnls[i])
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    def _trade_ref(idx: int) -> dict[str, Any]:
        t = trades[idx]
        return {
            "ticker": t["ticker"],
            "pnl_inr": round(pnls[idx], 2),
            "closed_at": str(t["closed_at"]),
        }

    return {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / n * 100, 2),
        "total_pnl_inr": round(total_pnl, 2),
        "biggest_win": _trade_ref(best_idx) if pnls[best_idx] > 0 else None,
        "biggest_loss": (
            _trade_ref(worst_idx) if pnls[worst_idx] < 0 else None
        ),
        "avg_win_inr": (round(gross_win / len(wins), 2) if wins else None),
        "avg_loss_inr": (
            round(sum(losses) / len(losses), 2) if losses else None
        ),
        "profit_factor": (
            round(gross_win / gross_loss, 2) if gross_loss > 0 else None
        ),
    }


def _holding_days(opened_at: Any, closed_at: Any) -> int:
    def _to_date(v: Any) -> date:
        if isinstance(v, date):
            return v
        return date.fromisoformat(str(v))

    return (_to_date(closed_at) - _to_date(opened_at)).days


def create_performance_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/performance",
        tags=["algo-trading"],
    )

    @router.get("/runs")
    async def list_runs(
        limit: int = Query(50, ge=1, le=200),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """Recent algo.runs rows for the caller (any mode), newest
        first. Legacy endpoint — kept for backward compatibility;
        the rebuilt Performance page uses /summary instead."""
        factory = _get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT r.id, r.strategy_id, "
                    "       s.name AS strategy_name, "
                    "       r.mode, r.status, "
                    "       r.period_start, r.period_end, "
                    "       r.started_at, r.completed_at, "
                    "       r.summary_json "
                    "FROM algo.runs r "
                    "LEFT JOIN algo.strategies s "
                    "  ON s.id = r.strategy_id "
                    "WHERE r.user_id = :uid "
                    "ORDER BY r.started_at DESC "
                    "LIMIT :lim"
                ),
                {"uid": UUID(user.user_id), "lim": limit},
            )
            rows = result.mappings().all()

        out: list[dict[str, Any]] = []
        for r in rows:
            sj: dict | None = r["summary_json"]
            out.append(
                {
                    "run_id": str(r["id"]),
                    "strategy_id": str(r["strategy_id"]),
                    "strategy_name": r["strategy_name"] or "Unknown",
                    "mode": r["mode"],
                    "status": r["status"],
                    "period_start": (
                        r["period_start"].isoformat()
                        if r["period_start"]
                        else None
                    ),
                    "period_end": (
                        r["period_end"].isoformat()
                        if r["period_end"]
                        else None
                    ),
                    "started_at": r["started_at"].isoformat(),
                    "completed_at": (
                        r["completed_at"].isoformat()
                        if r["completed_at"]
                        else None
                    ),
                    "total_pnl_inr": (
                        str(Decimal(str(sj["total_pnl_inr"])))
                        if sj and "total_pnl_inr" in sj
                        else None
                    ),
                    "total_pnl_pct": (
                        str(Decimal(str(sj["total_pnl_pct"])))
                        if sj and "total_pnl_pct" in sj
                        else None
                    ),
                    "total_trades": (
                        int(sj["total_trades"])
                        if sj and "total_trades" in sj
                        else None
                    ),
                    "win_rate_pct": (
                        str(Decimal(str(sj["win_rate_pct"])))
                        if sj and "win_rate_pct" in sj
                        else None
                    ),
                    "max_drawdown_pct": (
                        str(Decimal(str(sj["max_drawdown_pct"])))
                        if sj and "max_drawdown_pct" in sj
                        else None
                    ),
                }
            )
        return out

    @router.get("/summary")
    async def get_summary(
        mode: str = Query(
            ...,
            pattern="^(backtest|walkforward|paper|live)$",
        ),
        strategy_id: UUID | None = Query(None),
        lookback: str | None = Query(
            None,
            pattern="^(7d|30d|90d|all)$",
        ),
        start: date | None = Query(None),
        end: date | None = Query(None),
        user: UserContext = Depends(pro_or_superuser),
    ) -> JSONResponse:
        user_id = UUID(user.user_id)
        win_start, win_end = _resolve_window(lookback, start, end)

        cache = get_cache()
        cache_key = (
            f"cache:algo:perf:{user_id}:{mode}:"
            f"{strategy_id or 'all'}:"
            f"{lookback or f'{win_start}_{win_end}'}"
        )
        hit = cache.get(cache_key)
        if hit is not None:
            import json

            return JSONResponse(content=json.loads(hit))

        if mode in _BACKTEST_FAMILY:
            per_strategy_trades, names = await _load_backtest_trades(
                user_id,
                mode,
                strategy_id,
                win_start,
                win_end,
            )
        else:
            per_strategy_trades, names = await _load_closed_trades(
                user_id,
                mode,
                strategy_id,
                win_start,
                win_end,
            )

        strategies_out: list[dict[str, Any]] = []
        for sid, trades in per_strategy_trades.items():
            agg = _aggregate_trades(trades)
            max_dd = None
            if mode in _BACKTEST_FAMILY:
                dds = [
                    t["_max_drawdown_pct"]
                    for t in trades
                    if t.get("_max_drawdown_pct") is not None
                ]
                max_dd = max(dds) if dds else None
            strategies_out.append(
                {
                    "strategy_id": sid,
                    "strategy_name": names.get(sid, "Unknown"),
                    "max_drawdown_pct": max_dd,
                    **agg,
                }
            )
        strategies_out.sort(
            key=lambda s: s["total_pnl_inr"],
            reverse=True,
        )

        trades_out: list[dict[str, Any]] = []
        if strategy_id is not None:
            sid_s = str(strategy_id)
            for t in per_strategy_trades.get(sid_s, []):
                # Backtest/walkforward trade_list entries only
                # guarantee ticker/realised_pnl_inr/closed_at —
                # opened_at is absent from the backtest runner's
                # trade_list shape, unlike algo.closed_trades.
                opened_at = t.get("opened_at")
                closed_at = t["closed_at"]
                trades_out.append(
                    {
                        "ticker": t["ticker"],
                        "qty": t.get("qty"),
                        "avg_price": t.get("avg_price"),
                        "fill_price": t.get("fill_price"),
                        "opened_at": (
                            str(opened_at) if opened_at is not None else None
                        ),
                        "closed_at": str(closed_at),
                        "holding_days": (
                            _holding_days(opened_at, closed_at)
                            if opened_at is not None
                            else None
                        ),
                        "realised_pnl_inr": t["realised_pnl_inr"],
                        "return_pct": t.get("return_pct"),
                        "exit_reason": t.get("exit_reason", "signal"),
                        "opened_at_ts_ns": t.get("opened_at_ts_ns"),
                        "closed_at_ts_ns": t.get("closed_at_ts_ns"),
                    }
                )
            trades_out.sort(
                key=lambda t: t["closed_at"],
                reverse=True,
            )

        body = {
            "mode": mode,
            "window": {
                "start": win_start.isoformat(),
                "end": win_end.isoformat(),
            },
            "strategies": strategies_out,
            "trades": trades_out,
        }
        import json

        cache.set(cache_key, json.dumps(body), ttl=TTL_STABLE)
        return JSONResponse(content=body)

    return router


async def _load_backtest_trades(
    user_id: UUID,
    mode: str,
    strategy_id: UUID | None,
    win_start: date,
    win_end: date,
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    clauses = [
        "r.user_id = :uid",
        "r.mode = :mode",
        "r.status = 'completed'",
        "r.summary_json IS NOT NULL",
        "r.started_at::date >= :ws",
        "r.started_at::date <= :we",
    ]
    params: dict[str, Any] = {
        "uid": user_id,
        "mode": mode,
        "ws": win_start,
        "we": win_end,
    }
    if strategy_id is not None:
        clauses.append("r.strategy_id = :sid")
        params["sid"] = strategy_id
    where = " AND ".join(clauses)

    factory = _get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT r.strategy_id, s.name AS strategy_name, "
                "       r.summary_json "
                "FROM algo.runs r "
                "LEFT JOIN algo.strategies s ON s.id = r.strategy_id "
                f"WHERE {where}"
            ),
            params,
        )
        rows = result.mappings().all()

    per_strategy: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    for r in rows:
        sid = str(r["strategy_id"])
        names[sid] = r["strategy_name"] or "Unknown"
        sj = r["summary_json"] or {}
        max_dd = sj.get("max_drawdown_pct")
        for trade in sj.get("trade_list", []):
            per_strategy.setdefault(sid, []).append(
                {
                    **trade,
                    "_max_drawdown_pct": (
                        float(max_dd) if max_dd is not None else None
                    ),
                }
            )
    return per_strategy, names


async def _load_closed_trades(
    user_id: UUID,
    mode: str,
    strategy_id: UUID | None,
    win_start: date,
    win_end: date,
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    clauses = [
        "ct.user_id = :uid",
        "ct.mode = :mode",
        "ct.closed_at >= :ws",
        "ct.closed_at <= :we",
    ]
    params: dict[str, Any] = {
        "uid": user_id,
        "mode": mode,
        "ws": win_start,
        "we": win_end,
    }
    if mode == "live":
        clauses.append("ct.dry_run = false")
    if strategy_id is not None:
        clauses.append("ct.strategy_id = :sid")
        params["sid"] = strategy_id
    where = " AND ".join(clauses)

    factory = _get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT ct.strategy_id, s.name AS strategy_name, "
                "       ct.ticker, ct.qty, ct.avg_price, "
                "       ct.fill_price, ct.opened_at, ct.closed_at, "
                "       ct.opened_at_ts_ns, ct.closed_at_ts_ns, "
                "       ct.realised_pnl_inr, ct.return_pct, "
                "       ct.exit_reason "
                "FROM algo.closed_trades ct "
                "LEFT JOIN algo.strategies s ON s.id = ct.strategy_id "
                f"WHERE {where}"
            ),
            params,
        )
        rows = result.mappings().all()

    per_strategy: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    for r in rows:
        sid = str(r["strategy_id"])
        names[sid] = r["strategy_name"] or "Unknown"
        per_strategy.setdefault(sid, []).append(
            {
                "ticker": r["ticker"],
                "qty": r["qty"],
                "avg_price": (
                    float(r["avg_price"])
                    if r["avg_price"] is not None
                    else None
                ),
                "fill_price": (
                    float(r["fill_price"])
                    if r["fill_price"] is not None
                    else None
                ),
                "opened_at": r["opened_at"],
                "closed_at": r["closed_at"],
                "opened_at_ts_ns": r["opened_at_ts_ns"],
                "closed_at_ts_ns": r["closed_at_ts_ns"],
                "realised_pnl_inr": float(r["realised_pnl_inr"]),
                "return_pct": (
                    float(r["return_pct"])
                    if r["return_pct"] is not None
                    else None
                ),
                "exit_reason": r["exit_reason"],
            }
        )
    return per_strategy, names

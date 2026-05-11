"""Attribution API routes (REGIME-6).

GET /v1/algo/attribution/daily
    Per-day Brinson decomposition rows for the caller (filterable
    by ``strategy_id`` and date range). 60s Redis cache.

GET /v1/algo/attribution/trades
    Per-trade reason log built on the fly by joining today's
    BUY+SELL ``signal_generated`` events from algo.events with
    closed positions inferred from ``order_filled`` fills.
    300s Redis cache (per-user + per-strategy + per-day).

GET /v1/algo/attribution/regression
    Most recent monthly OLS factor regression rows for the
    caller. 300s Redis cache.

All endpoints gated by ``pro_or_superuser`` — they expose
trade-level data so general users are out of scope.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.attribution.trade_log import build_trade_reason
from cache import TTL_STABLE, TTL_VOLATILE, get_cache

_logger = logging.getLogger(__name__)


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def _ist_today() -> date:
    return (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).date()


def _iso_date(d) -> str | None:
    """Coerce date / datetime / ISO-string to ISO date string."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def _coerce_jsonb(v):
    """PG JSONB columns may surface as dict (asyncpg) or str
    (SQLAlchemy text + serialiser). Normalise to dict."""
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def create_attribution_router() -> APIRouter:
    """Build the /v1/algo/attribution router."""
    router = APIRouter(
        prefix="/algo/attribution", tags=["algo-trading"],
    )

    @router.get("/daily")
    async def get_daily(
        strategy_id: UUID | None = Query(None),
        start: date | None = Query(None),
        end: date | None = Query(None),
        limit: int = Query(60, ge=1, le=365),
        user: UserContext = Depends(pro_or_superuser),
    ) -> JSONResponse:
        user_id = UUID(user.user_id)
        cache = get_cache()
        cache_key = (
            "cache:algo:attribution:daily:"
            f"{user_id}:{strategy_id or 'all'}:"
            f"{start or '_'}:{end or '_'}:{limit}"
        )
        hit = cache.get(cache_key)
        if hit is not None:
            return JSONResponse(content=json.loads(hit))

        params: dict = {"uid": user_id, "lim": limit}
        clauses = ["user_id = :uid"]
        if strategy_id is not None:
            clauses.append("strategy_id = :sid")
            params["sid"] = strategy_id
        if start is not None:
            clauses.append("bar_date >= :ps")
            params["ps"] = start
        if end is not None:
            clauses.append("bar_date <= :pe")
            params["pe"] = end
        where = " AND ".join(clauses)

        factory = _get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT user_id, strategy_id, bar_date, "
                    " brinson_alloc, brinson_select, "
                    " brinson_interaction, total_active_return, "
                    " created_at "
                    "FROM algo.attribution_daily "
                    f"WHERE {where} "
                    "ORDER BY bar_date DESC LIMIT :lim"
                ),
                params,
            )
            rows = []
            for r in result.mappings().all():
                rows.append({
                    "user_id": str(r["user_id"]),
                    "strategy_id": str(r["strategy_id"]),
                    "bar_date": _iso_date(r["bar_date"]),
                    "brinson_alloc": _coerce_jsonb(
                        r["brinson_alloc"],
                    ),
                    "brinson_select": _coerce_jsonb(
                        r["brinson_select"],
                    ),
                    "brinson_interaction": _coerce_jsonb(
                        r["brinson_interaction"],
                    ),
                    "total_active_return": (
                        float(r["total_active_return"])
                        if r["total_active_return"] is not None
                        else 0.0
                    ),
                    "created_at": (
                        r["created_at"].isoformat()
                        if r["created_at"] else None
                    ),
                })

        body = {"rows": rows, "total": len(rows)}
        cache.set(cache_key, json.dumps(body), ttl=TTL_VOLATILE)
        return JSONResponse(content=body)

    @router.get("/trades")
    async def get_trades(
        strategy_id: UUID | None = Query(None),
        as_of: date | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
        mode: str | None = Query(
            None,
            description=(
                "Filter by run mode: 'live' restricts to "
                "order_filled_live (real Kite fills). 'paper' "
                "or 'backtest' restricts to order_filled with "
                "matching events.mode. Omit for all modes."
            ),
        ),
        dry_run: bool | None = Query(
            None,
            description=(
                "Filter by payload.dry_run. Pass true to see "
                "synthetic-Kite rehearsal fills only; false "
                "for real-money fills. Omit for both. Only "
                "meaningful when mode='live'."
            ),
        ),
        user: UserContext = Depends(pro_or_superuser),
    ) -> JSONResponse:
        """Build the per-trade reason log on the fly from today's
        signal_generated + order_filled events.

        Pairs entry / exit by ticker in chronological order: each
        BUY signal opens a position, the next SELL signal on the
        same ticker closes it, and we synthesise a TradeReason
        from the joined payloads.

        ``mode`` and ``dry_run`` are scope filters added so the
        Live Trading page can show only real-money trades and the
        Dry-run tab can show only synthetic ones. Without them
        the panel would intermingle paper + backtest + dry-run +
        live fills.
        """
        user_id = UUID(user.user_id)
        as_of = as_of or _ist_today()
        cache = get_cache()
        cache_key = (
            "cache:algo:attribution:trades:"
            f"{user_id}:{strategy_id or 'all'}:"
            f"{as_of.isoformat()}:{limit}:"
            f"{mode or 'any'}:{dry_run if dry_run is not None else 'any'}"
        )
        hit = cache.get(cache_key)
        if hit is not None:
            return JSONResponse(content=json.loads(hit))

        from backend.db.duckdb_engine import query_iceberg_table

        # Event-type filter: live mode reads only the live event
        # type; paper/backtest read only the generic order_filled
        # type with an events.mode predicate; default keeps the
        # pre-2026-05-11 behaviour and intermingles everything.
        if mode == "live":
            type_predicate = (
                "type IN ('signal_generated', 'order_filled_live')"
            )
            mode_clause = ""
            mode_params: list = []
        elif mode in ("paper", "backtest"):
            type_predicate = (
                "type IN ('signal_generated', 'order_filled')"
            )
            mode_clause = " AND mode = ?"
            mode_params = [mode]
        else:
            type_predicate = (
                "type IN ('signal_generated', "
                "         'order_filled', "
                "         'order_filled_live')"
            )
            mode_clause = ""
            mode_params = []

        dry_run_clause = ""
        dry_run_params: list = []
        if dry_run is not None:
            # payload_json is text JSON — match the
            # /algo/paper/events extraction shape so the
            # row's payload.dry_run bool matches.
            wanted = "true" if dry_run else "false"
            dry_run_clause = (
                " AND (type = 'signal_generated' "
                "   OR json_extract_string("
                "        payload_json, '$.dry_run') = ?)"
            )
            dry_run_params = [wanted]

        sql = (
            "SELECT user_id, strategy_id, type, payload_json, ts_ns "
            "FROM events "
            "WHERE user_id = ? "
            "  AND ts_date = ? "
            f"  AND {type_predicate}"
            f"{mode_clause}"
            f"{dry_run_clause} "
            "ORDER BY ts_ns"
        )
        try:
            events = query_iceberg_table(
                "algo.events", sql,
                [str(user_id), as_of.isoformat()]
                + mode_params + dry_run_params,
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "attribution.trades: events query failed",
            )
            events = []

        # Filter by strategy if requested
        if strategy_id is not None:
            sid_s = str(strategy_id)
            events = [
                e for e in events
                if str(e.get("strategy_id")) == sid_s
            ]

        # Pair up BUY/SELL signal_generated events per ticker
        signals_by_ticker: dict[str, list[dict]] = {}
        fills_by_ticker: dict[str, list[dict]] = {}
        for ev in events:
            try:
                payload = json.loads(ev.get("payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            ticker = payload.get("ticker")
            if not ticker:
                continue
            ev_kind = ev.get("type")
            if ev_kind == "signal_generated":
                signals_by_ticker.setdefault(ticker, []).append({
                    **ev, "_payload": payload,
                })
            elif ev_kind in ("order_filled", "order_filled_live"):
                fills_by_ticker.setdefault(ticker, []).append({
                    **ev, "_payload": payload,
                })

        out: list[dict] = []
        for ticker, sigs in signals_by_ticker.items():
            buys = [s for s in sigs if s["_payload"].get(
                "side",
            ) == "BUY"]
            sells = [s for s in sigs if s["_payload"].get(
                "side",
            ) == "SELL"]
            fills = fills_by_ticker.get(ticker, [])
            buy_fills = [
                f for f in fills
                if f["_payload"].get("side") == "BUY"
            ]
            sell_fills = [
                f for f in fills
                if f["_payload"].get("side") == "SELL"
            ]
            for i in range(min(len(buys), len(sells))):
                entry_event = buys[i]
                exit_event = sells[i]
                buy_fill = (
                    buy_fills[i] if i < len(buy_fills) else None
                )
                sell_fill = (
                    sell_fills[i] if i < len(sell_fills)
                    else None
                )
                if buy_fill is None or sell_fill is None:
                    # No fills yet — skip this pair until both
                    # fills land (next refresh).
                    continue
                avg_entry = float(
                    buy_fill["_payload"].get("fill_price") or 0,
                )
                avg_exit = float(
                    sell_fill["_payload"].get("fill_price") or 0,
                )
                qty = int(
                    buy_fill["_payload"].get("qty") or 0,
                )
                pnl_inr = (avg_exit - avg_entry) * qty
                opened_at = _ts_ns_to_date(
                    int(buy_fill["ts_ns"]),
                )
                closed_at = _ts_ns_to_date(
                    int(sell_fill["ts_ns"]),
                )
                trade = {
                    "ticker": ticker,
                    "opened_at": opened_at,
                    "closed_at": closed_at,
                    "qty": qty,
                    "avg_entry_price": avg_entry,
                    "avg_exit_price": avg_exit,
                    "realised_pnl_inr": pnl_inr,
                }
                reason = build_trade_reason(
                    trade, entry_event, exit_event,
                )
                out.append({
                    "ticker": reason.ticker,
                    "opened_at": _iso_date(reason.opened_at),
                    "closed_at": _iso_date(reason.closed_at),
                    "qty": reason.qty,
                    "entry_price": reason.entry_price,
                    "exit_price": reason.exit_price,
                    "pnl_inr": reason.pnl_inr,
                    "pnl_pct": reason.pnl_pct,
                    "entry_regime": reason.entry_regime,
                    "stress_prob": reason.stress_prob,
                    "entry_factor_exposures": (
                        reason.entry_factor_exposures
                    ),
                    "exit_reason": reason.exit_reason,
                    "reason_text": reason.reason_text,
                })

        out.sort(
            key=lambda r: r.get("closed_at") or "",
            reverse=True,
        )
        out = out[:limit]
        body = {
            "rows": out,
            "total": len(out),
            "as_of": as_of.isoformat(),
        }
        cache.set(cache_key, json.dumps(body), ttl=TTL_STABLE)
        return JSONResponse(content=body)

    @router.get("/regression")
    async def get_regression(
        strategy_id: UUID | None = Query(None),
        limit: int = Query(12, ge=1, le=60),
        user: UserContext = Depends(pro_or_superuser),
    ) -> JSONResponse:
        user_id = UUID(user.user_id)
        cache = get_cache()
        cache_key = (
            "cache:algo:attribution:regression:"
            f"{user_id}:{strategy_id or 'all'}:{limit}"
        )
        hit = cache.get(cache_key)
        if hit is not None:
            return JSONResponse(content=json.loads(hit))

        params: dict = {"uid": user_id, "lim": limit}
        clauses = ["user_id = :uid"]
        if strategy_id is not None:
            clauses.append("strategy_id = :sid")
            params["sid"] = strategy_id
        where = " AND ".join(clauses)

        factory = _get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT user_id, strategy_id, period_start, "
                    " period_end, alpha, betas, r_squared, "
                    " n_observations, created_at "
                    "FROM algo.factor_regression "
                    f"WHERE {where} "
                    "ORDER BY period_end DESC LIMIT :lim"
                ),
                params,
            )
            rows = []
            for r in result.mappings().all():
                betas = _coerce_jsonb(r["betas"])
                is_mock = bool(betas.pop("__mock_data__", None))
                rows.append({
                    "user_id": str(r["user_id"]),
                    "strategy_id": str(r["strategy_id"]),
                    "period_start": _iso_date(r["period_start"]),
                    "period_end": _iso_date(r["period_end"]),
                    "alpha": (
                        float(r["alpha"])
                        if r["alpha"] is not None else 0.0
                    ),
                    "betas": betas,
                    "r_squared": (
                        float(r["r_squared"])
                        if r["r_squared"] is not None else 0.0
                    ),
                    "n_observations": int(
                        r["n_observations"] or 0,
                    ),
                    "mock_data": is_mock,
                    "created_at": (
                        r["created_at"].isoformat()
                        if r["created_at"] else None
                    ),
                })

        body = {"rows": rows, "total": len(rows)}
        cache.set(cache_key, json.dumps(body), ttl=TTL_STABLE)
        return JSONResponse(content=body)

    return router


def _ts_ns_to_date(ts_ns: int) -> date:
    """Project an ``algo.events.ts_ns`` integer into a UTC date."""
    return datetime.fromtimestamp(
        ts_ns / 1_000_000_000, tz=timezone.utc,
    ).date()

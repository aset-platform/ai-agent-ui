"""GET /v1/algo/kill-switch
POST /v1/algo/kill-switch/arm
POST /v1/algo/kill-switch/disarm

Per spec § 5.4. Re-arming requires a confirm dialog UI-side;
backend just exposes the toggle. Reason string optional.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.paper.kill_switch_repo import KillSwitchRepo
from backend.algo.paper.types import KillSwitchState

_logger = logging.getLogger(__name__)


class ArmRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=256)


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


def _get_redis():
    """Returns an async Redis client (or None).

    Wired in Slice 8c via backend/algo/redis_async.py — uses
    redis.asyncio.from_url(REDIS_URL). Returns None gracefully
    when REDIS_URL is empty so the repo runs PG-only.
    """
    from backend.algo.redis_async import get_async_redis
    return get_async_redis()


def create_kill_switch_router() -> APIRouter:
    router = APIRouter(prefix="/algo", tags=["algo-trading"])

    @router.get(
        "/kill-switch", response_model=KillSwitchState,
    )
    async def get_state(
        user: UserContext = Depends(pro_or_superuser),
    ) -> KillSwitchState:
        repo = KillSwitchRepo(redis_client=_get_redis())
        factory = _get_session_factory()
        async with factory() as session:
            row = await repo.get(
                session, user_id=UUID(user.user_id),
            )
        return KillSwitchState(**row)

    @router.post(
        "/kill-switch/arm", response_model=KillSwitchState,
    )
    async def arm(
        body: ArmRequest,
        user: UserContext = Depends(pro_or_superuser),
    ) -> KillSwitchState:
        repo = KillSwitchRepo(redis_client=_get_redis())
        factory = _get_session_factory()
        async with factory() as session:
            await repo.arm(
                session,
                user_id=UUID(user.user_id),
                set_by=UUID(user.user_id),
                reason=body.reason,
            )
            await session.commit()
            row = await repo.get(
                session, user_id=UUID(user.user_id),
            )
        return KillSwitchState(**row)

    @router.post(
        "/kill-switch/disarm", response_model=KillSwitchState,
    )
    async def disarm(
        user: UserContext = Depends(pro_or_superuser),
    ) -> KillSwitchState:
        repo = KillSwitchRepo(redis_client=_get_redis())
        factory = _get_session_factory()
        async with factory() as session:
            await repo.disarm(
                session, user_id=UUID(user.user_id),
            )
            await session.commit()
            row = await repo.get(
                session, user_id=UUID(user.user_id),
            )
        return KillSwitchState(**row)

    @router.post("/kill-switch/panic-close-all")
    async def panic_close_all(
        user: UserContext = Depends(pro_or_superuser),
    ) -> dict:
        """Submit SELL orders for every open position the algo
        has on the user's Kite account.

        Scoping rules (defensive — we never touch positions the
        algo didn't open):
          - Only sells positions for tickers that have at least
            one ``order_filled_live`` event in algo.events for
            this user.
          - Reads current Kite positions/holdings via REST so
            quantities reflect what's actually on the exchange,
            not what algo.runs THINKS is open (postback drift,
            partial fills, manual user trades on Kite all reflect
            here).
          - LIMIT orders priced at LTP - 30bps (BUY-style buffer
            inverted) so they are marketable enough to fill but
            capped against runaway slippage.

        Side effects:
          1. Arms the kill switch first (so no NEW BUYs sneak in
             between this call and exit fills landing).
          2. Records each SELL as a synthetic
             ``order_submitted_live`` event tagged with
             ``source: 'panic_close'``.
          3. Returns summary {tickers_closed, orders_submitted,
             errors}.

        This is intentionally NOT idempotent at the HTTP layer —
        each call submits a fresh round of SELLs. Two clicks =
        two SELL waves. The frontend confirm dialog must guard
        against accidental double-fire.
        """
        from decimal import Decimal
        from uuid import uuid4
        from datetime import datetime, UTC

        from backend.algo.broker.credentials_repo import (
            BrokerCredentialsRepo,
        )
        from backend.algo.broker.kite_client import KiteClient
        from backend.cache import get_cache
        from backend.db.duckdb_engine import query_iceberg_table
        from backend.algo.backtest.event_writer import (
            event_row, flush_events,
        )
        import asyncio
        import json

        user_id = UUID(user.user_id)

        # 1. Arm kill switch first to prevent new BUYs.
        repo_ks = KillSwitchRepo(redis_client=_get_redis())
        factory = _get_session_factory()
        async with factory() as session:
            await repo_ks.arm(
                session,
                user_id=user_id,
                set_by=user_id,
                reason="panic_close_all triggered",
            )
            await session.commit()

        # 2. Resolve which tickers the algo has touched.
        # `order_filled_live` events are emitted only for orders
        # the algo actually executed (real or via postback
        # reconciliation). Anything not in this set is left alone.
        try:
            evt_rows = query_iceberg_table(
                "algo.events",
                "SELECT payload_json FROM events "
                "WHERE user_id = ? AND mode = 'live' "
                "  AND type = 'order_filled_live'",
                [str(user_id)],
            )
        except FileNotFoundError:
            evt_rows = []
        algo_tickers: set[str] = set()
        for r in evt_rows:
            try:
                p = json.loads(r["payload_json"])
            except Exception:  # noqa: BLE001
                continue
            sym = p.get("symbol") or ""
            if sym:
                algo_tickers.add(sym.upper())

        if not algo_tickers:
            return {
                "tickers_closed": [],
                "orders_submitted": 0,
                "errors": [],
                "note": "No algo-opened positions found",
            }

        # 3. Load Kite credentials + connect.
        creds_repo = BrokerCredentialsRepo()
        async with factory() as session:
            creds = await creds_repo.load(session, user_id)
        if not creds or creds.get("access_token_expired"):
            return {
                "tickers_closed": [],
                "orders_submitted": 0,
                "errors": ["Kite token expired"],
            }
        # IMPORTANT — panic close ALWAYS hits real Kite, never
        # synthetic. The user has real positions on the exchange;
        # synthetic SELLs do nothing. Override the env-default
        # ALGO_LIVE_DRY_RUN even if the per-user Redis flag is
        # armed — when someone hits "Yes, close all", they mean
        # close the real money exposure, full stop.
        kite = KiteClient(
            api_key=creds["api_key"],
            access_token=creds["access_token"],
            dry_run=False,
        )

        # 4. Pull current Kite positions + holdings to find open
        # quantities. Holdings = T+1 settled (CNC); positions =
        # intraday net. Both can hold algo-opened qty depending on
        # session age.
        try:
            kc_holdings = await asyncio.to_thread(
                kite._kc.holdings,
            )
        except Exception:  # noqa: BLE001
            kc_holdings = []
        try:
            kc_positions = await asyncio.to_thread(
                kite._kc.positions,
            )
            net_positions = (
                kc_positions.get("net", [])
                if isinstance(kc_positions, dict) else []
            )
        except Exception:  # noqa: BLE001
            net_positions = []

        # Net long qty per ticker = holdings.qty + positions.net_qty
        open_qty: dict[str, int] = {}
        for h in kc_holdings:
            sym = (h.get("tradingsymbol") or "").upper()
            qty = int(h.get("quantity") or 0)
            if sym and qty:
                open_qty[sym] = open_qty.get(sym, 0) + qty
        for p in net_positions:
            sym = (p.get("tradingsymbol") or "").upper()
            qty = int(p.get("quantity") or 0)
            if sym and qty:
                open_qty[sym] = open_qty.get(sym, 0) + qty

        # 5. For each ticker the algo opened, submit SELL.
        cache = get_cache()
        events: list[dict] = []
        orders_submitted = 0
        tickers_closed: list[str] = []
        errors: list[str] = []
        for sym in sorted(algo_tickers):
            qty = open_qty.get(sym, 0)
            if qty <= 0:
                # Either no position, or short — skip (we don't
                # auto-cover shorts; user must do that manually).
                continue
            # Resolve LIMIT price from Redis tick cache.
            cache_key = f"cache:ltp:{sym}.NS"
            try:
                raw = cache.get(cache_key)
                ltp = (
                    Decimal(str(raw)) if raw is not None
                    else Decimal("0")
                )
            except Exception:  # noqa: BLE001
                ltp = Decimal("0")
            # SELL LIMIT 30 bps below LTP for marketability.
            order_kwargs: dict
            if ltp > 0:
                buf = ltp * Decimal("30") / Decimal("10000")
                limit_price = ltp - buf
                tick = Decimal("0.05")
                limit_price = (
                    (limit_price / tick).quantize(Decimal("1"))
                    * tick
                )
                order_kwargs = {
                    "order_type": "LIMIT",
                    "price": float(limit_price),
                }
            else:
                order_kwargs = {"order_type": "MARKET"}

            try:
                kite_order_id = await asyncio.to_thread(
                    kite.place_order,
                    tradingsymbol=sym,
                    exchange="NSE",
                    transaction_type="SELL",
                    quantity=qty,
                    product="CNC",
                    variety="regular",
                    tag="algo-panic",
                    **order_kwargs,
                )
                orders_submitted += 1
                tickers_closed.append(sym)
                events.append(event_row(
                    session_id=uuid4(),
                    user_id=user_id,
                    strategy_id=None,
                    mode="live",
                    type_="order_submitted_live",
                    payload={
                        "dry_run": False,
                        "internal_order_id": str(uuid4()),
                        "kite_order_id": kite_order_id,
                        "symbol": sym,
                        "side": "SELL",
                        "qty": qty,
                        "order_type": order_kwargs.get(
                            "order_type",
                        ),
                        "limit_price": order_kwargs.get("price"),
                        "ref_last_price": str(ltp) if ltp else None,
                        "source": "panic_close",
                    },
                ))
                _logger.info(
                    "panic_close: SELL %d %s @ %s "
                    "kite_order_id=%s",
                    qty, sym,
                    order_kwargs.get("price", "MKT"),
                    kite_order_id,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{sym}: {exc}")
                _logger.exception(
                    "panic_close: SELL failed sym=%s qty=%d",
                    sym, qty,
                )

        # 6. Flush all events in one Iceberg commit.
        if events:
            await asyncio.to_thread(flush_events, events)

        return {
            "tickers_closed": tickers_closed,
            "orders_submitted": orders_submitted,
            "errors": errors,
        }

    return router

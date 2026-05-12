"""Paper-trading routes.

Slice 8b: GET /events (events timeline).
Slice 8c: POST /runs (start), DELETE /runs/{strategy_id} (stop),
          GET /runs (list active).
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
)
from pydantic import BaseModel, Field
from typing import Literal

from auth.dependencies import pro_or_superuser
from auth.models import UserContext

_logger = logging.getLogger(__name__)


class StartRunRequest(BaseModel):
    strategy_id: UUID
    fixture_path: str = Field(
        default="", max_length=200,
    )
    """Replay fixture filename. Required when source='replay'."""
    source: Literal["replay", "live-ws"] = "replay"
    """Tick source: 'replay' uses a JSONL fixture; 'live-ws' streams
    from the user's connected Kite WebSocket multiplexer."""
    mode: Literal["paper", "dryrun", "live"] = "paper"
    """Runtime selector (ASETPLTFRM-377 — three first-class values):
      * 'paper'  → PaperRuntime (synthetic broker, no Kite calls).
      * 'dryrun' → LiveRuntime with KiteClient(dry_run=True).
                   Real Kite WS ticks but order placement is
                   short-circuited to synthetic responses. Used
                   by the Strategies → Dry-run tab.
      * 'live'   → LiveRuntime with KiteClient(dry_run=False).
                   Real money. Used by the Live page.
    The dry_run flag is pinned at KiteClient construction; the
    per-user Redis flag (algo:dry_run:{user_id}) is no longer
    consulted from this endpoint."""
    initial_capital_inr: Decimal = Field(
        default=Decimal("100000.00"), ge=Decimal("1000.00"),
    )


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


async def _build_live_ws_source(
    *,
    user: UserContext,
    strategy,  # Strategy AST
    session_factory,
) -> Any:
    """Build a LiveWsTickSource for the user's Kite WS multiplexer.

    Steps:
    1. Load Kite credentials (api_key + access_token) from DB.
    2. Verify the token is not expired.
    3. Resolve tickers from the user's PG portfolio / watchlist.
    4. Look up instrument tokens for those tickers.
    5. Get-or-create the per-user KiteWsMultiplexer.
    6. Subscribe the strategy; return a LiveWsTickSource.

    Raises HTTPException(400/503) on any failure.
    """
    from backend.algo.broker.credentials_repo import (
        BrokerCredentialsRepo,
    )
    from backend.algo.broker.ws_registry import (
        get_or_create_multiplexer,
    )
    from backend.algo.instruments.repo import InstrumentsRepo
    from backend.algo.stream.sources import LiveWsTickSource

    user_id = UUID(user.user_id)

    # --- 1. Credentials ------------------------------------------------
    creds_repo = BrokerCredentialsRepo()
    async with session_factory() as session:
        creds = await creds_repo.load(session, user_id)
    if creds is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Kite credentials found. "
                "Please connect Zerodha first."
            ),
        )
    if creds.get("access_token_expired"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Kite access token expired. "
                "Please re-authenticate with Zerodha."
            ),
        )
    api_key = creds["api_key"]
    access_token = creds["access_token"]
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Kite access token. "
                "Please complete the OAuth handshake."
            ),
        )

    # --- 2. Resolve tickers from user scope ---------------------------
    # Reuse the v1 ``_scoped_tickers`` helper (same one Insights
    # tabs use) — gives us watchlist ∪ holdings without the
    # raw-SQL detour.  Even if the strategy AST asks for
    # ``scope=discovery``, live-WS is capped at the user's curated
    # set: subscribing to thousands of NSE tokens is impractical.
    from backend.insights_routes import _scoped_tickers
    tickers = await _scoped_tickers(user, "watchlist")
    if not tickers:
        raise HTTPException(
            status_code=400,
            detail=(
                "No instruments found for live-WS source. "
                "Add tickers to your portfolio or watchlist."
            ),
        )

    # --- 3. Token lookup ----------------------------------------------
    instr_repo = InstrumentsRepo()
    async with session_factory() as session:
        token_to_ticker = await instr_repo.get_tokens_for_tickers(
            session, tickers,
        )
    if not token_to_ticker:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Kite instrument tokens found for your tickers. "
                "Ensure the instruments master is loaded."
            ),
        )
    tokens = list(token_to_ticker.keys())

    # --- 4. Multiplexer -----------------------------------------------
    try:
        mux = await get_or_create_multiplexer(
            user_id=user_id,
            api_key=api_key,
            access_token=access_token,
        )
    except Exception as exc:
        _logger.exception(
            "live-ws mux init failed user=%s", user_id,
        )
        raise HTTPException(
            status_code=503,
            detail=f"Failed to start live tick source: {exc}",
        )

    # --- 5. Subscribe strategy ----------------------------------------
    queue = mux.subscribe(
        strategy.id, tokens, token_to_ticker,
    )
    return LiveWsTickSource(
        user_id=user_id,
        strategy_id=strategy.id,
        queue=queue,
        mux=mux,
    )




def create_paper_router() -> APIRouter:
    router = APIRouter(prefix="/algo/paper", tags=["algo-trading"])

    @router.get("/events")
    async def list_events(
        response: Response,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        type: str | None = Query(
            None,
            description=(
                "Filter by event type "
                "(e.g. position_drift_detected)"
            ),
        ),
        mode: str | None = Query(
            None,
            description=(
                "Filter by run mode: 'paper', 'live', "
                "or 'backtest'. Combines with type."
            ),
        ),
        dry_run: bool | None = Query(
            None,
            description=(
                "Filter by payload.dry_run: pass true to see "
                "only dry-run orders (LiveRuntime stamps the "
                "flag on every event); false for real-money "
                "live orders. Omit for both."
            ),
        ),
        since_date: str | None = Query(
            None,
            description=(
                "Restrict to events on or after this IST date "
                "(YYYY-MM-DD). Matches the events table's "
                "``ts_date`` partition column directly — "
                "useful for `today only` widgets like "
                "RecentFillsTape that should never bleed "
                "prior sessions into the panel."
            ),
        ),
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """Recent algo events for the caller (newest first).

        Returns both ``mode='paper'`` and ``mode='live'``
        events so the reconciliation drift panel can poll
        for ``type=position_drift_detected`` regardless of
        which mode emitted them.

        ``X-Total-Count`` response header carries the unfiltered-
        by-pagination total so the frontend pager can render
        ``Page N / M``.
        """
        from backend.db.duckdb_engine import query_iceberg_table
        user_id_str = str(UUID(user.user_id))

        clauses = ["user_id = ?"]
        base_params: list = [user_id_str]
        if type is not None:
            clauses.append("type = ?")
            base_params.append(type)
        if mode is not None:
            clauses.append("mode = ?")
            base_params.append(mode)
        if dry_run is not None:
            # ``payload_json`` is stored as text JSON; DuckDB's
            # JSON funcs read string-typed members. LiveRuntime
            # only stamps ``dry_run`` when the run IS dry — Live
            # events omit the field (ASETPLTFRM-374 epic). So:
            #   dry_run=true  → payload.dry_run = 'true'
            #   dry_run=false → payload.dry_run = 'false' OR
            #                   payload.dry_run is missing (NULL)
            if dry_run:
                clauses.append(
                    "json_extract_string(payload_json, "
                    "'$.dry_run') = 'true'"
                )
            else:
                clauses.append(
                    "(json_extract_string(payload_json, "
                    "'$.dry_run') = 'false' "
                    "OR json_extract_string(payload_json, "
                    "'$.dry_run') IS NULL)"
                )
        if since_date is not None:
            clauses.append("ts_date >= ?")
            base_params.append(since_date)
        where = " AND ".join(clauses)

        sql = (
            f"SELECT event_id, ts_ns, ts_date, "
            f"       strategy_id, type, payload_json "
            f"FROM events WHERE {where} "
            f"ORDER BY ts_ns DESC "
            f"LIMIT ? OFFSET ?"
        )
        params = base_params + [limit, offset]
        count_sql = f"SELECT COUNT(*) AS n FROM events WHERE {where}"
        count_params = base_params

        try:
            rows = query_iceberg_table(
                "algo.events", sql, params,
            )
        except FileNotFoundError:
            response.headers["X-Total-Count"] = "0"
            response.headers["Access-Control-Expose-Headers"] = (
                "X-Total-Count"
            )
            return []

        # Total respects the type filter but ignores limit/offset
        # so the pager can render Page N / M correctly.
        try:
            count_rows = query_iceberg_table(
                "algo.events", count_sql, count_params,
            )
            total = int(count_rows[0]["n"]) if count_rows else 0
        except Exception:  # noqa: BLE001
            total = 0

        response.headers["X-Total-Count"] = str(total)
        response.headers["Access-Control-Expose-Headers"] = (
            "X-Total-Count"
        )

        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:  # noqa: BLE001
                payload = {}
            out.append({
                "event_id": r["event_id"],
                "ts_ns": int(r["ts_ns"]),
                "ts_date": r["ts_date"],
                "strategy_id": r.get("strategy_id"),
                "type": r["type"],
                "payload": payload,
            })
        return out

    @router.post("/runs", status_code=201)
    async def start_run(
        body: StartRunRequest,
        user: UserContext = Depends(pro_or_superuser),
    ):
        from backend.algo.paper.kill_switch_repo import (
            KillSwitchRepo,
        )
        from backend.algo.paper.supervisor import (
            build_replay_source, get_supervisor,
        )
        from backend.algo.redis_async import get_async_redis
        from backend.algo.strategy.repo import get_strategy

        user_id = UUID(user.user_id)
        factory = _get_session_factory()
        async with factory() as session:
            strategy = await get_strategy(
                session, user_id, body.strategy_id,
            )
        if strategy is None:
            raise HTTPException(
                status_code=404, detail="Strategy not found",
            )

        ks_repo = KillSwitchRepo(redis_client=get_async_redis())
        kill_active = await ks_repo.is_active(user_id)

        if body.source == "live-ws":
            # Resolve Kite credentials and get/create the user's
            # WS multiplexer.  The strategy universe tokens must
            # already be subscribed before starting the runtime.
            source = await _build_live_ws_source(
                user=user,
                strategy=strategy,
                session_factory=factory,
            )
        else:
            # Replay fixture source (default / backward-compatible).
            if not body.fixture_path:
                raise HTTPException(
                    status_code=400,
                    detail="fixture_path required for source=replay",
                )
            try:
                source = build_replay_source(body.fixture_path)
            except FileNotFoundError as exc:
                raise HTTPException(
                    status_code=400, detail=str(exc),
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=str(exc),
                )

        sv = get_supervisor()

        # Route explicitly on the request's ``mode`` field. v2
        # earlier sniffed live_caps.live_orders_enabled to decide,
        # but that conflated UX intent with config state. Now the
        # frontend sends mode=paper or mode=live based on which
        # view the user is in (Paper / Dry run / Live segments).
        from backend.algo.live.caps_repo import CapsRepo
        from backend.algo.live.runtime import LiveNotEnabledError

        if body.mode in ("live", "dryrun"):
            caps_repo = CapsRepo()
            caps = await caps_repo.get(user_id, body.strategy_id)
            live_enabled = bool(
                caps and caps.get("live_orders_enabled"),
            )
            if not live_enabled:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Live trading not enabled for this "
                        "strategy. Configure caps + flip the "
                        "Live mode toggle first."
                    ),
                )
            from datetime import date as _date
            from backend.algo.broker.credentials_repo import (
                BrokerCredentialsRepo,
            )
            from backend.algo.broker.kite_client import KiteClient
            from backend.algo.backtest.runs_repo import (
                BacktestRunsRepo,
            )
            from backend.algo.paper.kill_switch_repo import (
                KillSwitchRepo,
            )

            # Load Kite credentials.
            creds_repo = BrokerCredentialsRepo()
            async with factory() as session:
                creds = await creds_repo.load(session, user_id)
            if not creds or not creds.get("access_token") \
                    or creds.get("access_token_expired"):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Kite not connected or token expired. "
                        "Reconnect Zerodha first."
                    ),
                )

            # ASETPLTFRM-377 — pin dry_run at construction time.
            # mode='live'   → dry_run=False (real money).
            # mode='dryrun' → dry_run=True  (synthetic, real WS).
            # No user_id kwarg: that would re-introduce the
            # per-user Redis resolver and re-couple this path to
            # the Strategies → Dry-run toggle. Wrong UX surface.
            pinned_dry_run = body.mode == "dryrun"
            kite = KiteClient(
                api_key=creds["api_key"],
                access_token=creds["access_token"],
                dry_run=pinned_dry_run,
            )

            # Defence-in-depth: on the Live (real-money) path,
            # confirm the KiteClient really did pin dry_run=False
            # before we hand it to LiveRuntime. Future regressions
            # that accidentally re-introduce Redis resolution
            # surface here as a 500 instead of silently leaking
            # dry-run into real money.
            if body.mode == "live" and kite._dry_run is not False:
                _logger.error(
                    "dry-run leak detected on Live path for "
                    "user=%s strategy=%s — refusing to spawn",
                    user_id, body.strategy_id,
                )
                raise HTTPException(
                    status_code=500,
                    detail="dry-run leak on live path",
                )

            # Create algo.runs row up-front so LiveRuntime has
            # a run_id for in-flight tracking. Live mode has no
            # meaningful period boundaries — use today as a
            # placeholder; meaningful values are populated as
            # orders fire.
            runs_repo = BacktestRunsRepo()
            today = _date.today()
            async with factory() as session:
                run = await runs_repo.create_pending(
                    session,
                    user_id=user_id,
                    strategy_id=body.strategy_id,
                    period_start=today,
                    period_end=today,
                    mode="live",
                )
                await runs_repo.mark_running(
                    session, run_id=run.run_id,
                )
                await session.commit()
            run_id = run.run_id

            ks_for_runtime = KillSwitchRepo(
                redis_client=get_async_redis(),
            )
            _logger.info(
                "start_run: dispatching LiveRuntime user=%s "
                "strat=%s source=%s",
                user_id, body.strategy_id, body.source,
            )
            try:
                row = await sv.start_live_run(
                    user_id=user_id,
                    strategy=strategy,
                    source=source,
                    initial_capital_inr=body.initial_capital_inr,
                    kite=kite,
                    caps=caps,
                    run_id=run_id,
                    caps_repo=caps_repo,
                    kill_switch_repo=ks_for_runtime,
                )
            except LiveNotEnabledError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=409, detail=str(exc),
                )
            return row

        try:
            row = await sv.start_run(
                user_id=user_id,
                strategy=strategy,
                source=source,
                initial_capital_inr=body.initial_capital_inr,
                kill_switch_active=kill_active,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return row

    @router.delete("/runs/{strategy_id}")
    async def stop_run(
        strategy_id: UUID,
        user: UserContext = Depends(pro_or_superuser),
    ):
        from backend.algo.paper.supervisor import get_supervisor

        sv = get_supervisor()
        stopped = await sv.stop_run(
            user_id=UUID(user.user_id), strategy_id=strategy_id,
        )
        if not stopped:
            raise HTTPException(
                status_code=404, detail="No active run found",
            )
        return {"stopped": True}

    @router.get("/runs")
    async def list_runs(
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        from backend.algo.paper.supervisor import get_supervisor

        sv = get_supervisor()
        return sv.list_active(user_id=UUID(user.user_id))

    @router.get("/fixtures")
    async def list_fixtures(
        user: UserContext = Depends(pro_or_superuser),
    ) -> list[dict[str, Any]]:
        """Replay fixtures available to the start-run form.

        Reads the same directory build_replay_source validates
        against, so the dropdown can never offer a path that
        the start-run endpoint would later reject.
        """
        from backend.algo.paper.supervisor import (
            list_replay_fixtures,
        )
        return list_replay_fixtures()

    @router.get("/strategies/{strategy_id}/summary")
    async def paper_session_summary(
        strategy_id: UUID,
        mode: str = Query(
            "paper",
            description=(
                "Event mode to aggregate: 'paper' (PaperRuntime), "
                "'live' (LiveRuntime — both real-money and "
                "dry-run synthetic). Default 'paper' for "
                "backwards compatibility."
            ),
        ),
        dry_run: bool | None = Query(
            None,
            description=(
                "When mode='live', filter to only dry-run "
                "synthetic fills (true) or only real-money "
                "fills (false). Omit for both. Ignored when "
                "mode='paper'."
            ),
        ),
        user: UserContext = Depends(pro_or_superuser),
    ) -> dict[str, Any]:
        """Aggregate fills for a strategy into a P&L summary.

        Walks every ``order_filled*`` event for the
        (user, strategy, mode) tuple in chronological order,
        tracks per-ticker FIFO position state, and returns:

        - open positions with mark-to-market unrealised P&L
          (using the latest close from stocks.ohlcv as the mark)
        - closed positions with aggregated realised P&L
        - totals + signal/rejection counts

        Read-side replacement for the missing algo.runs row
        on paper sessions, AND a unified view across both
        Paper and Live (incl. dry-run) so the Trading tab
        shows P&L regardless of which segment a user runs in.
        """
        from backend.db.duckdb_engine import query_iceberg_table

        user_id_str = str(UUID(user.user_id))
        sid_str = str(strategy_id)
        # Normalise mode — accept either 'paper' or 'live'.
        # Anything else returns an empty summary so the frontend
        # never crashes on a typo.
        mode_norm = "live" if mode == "live" else "paper"

        # Event type names diverge by runtime: PaperRuntime emits
        # `order_filled`, LiveRuntime emits `order_filled_live`
        # (both real-money and synthetic dry-run carry the same
        # type with `dry_run` payload bool to differentiate).
        fill_type = (
            "order_filled_live" if mode_norm == "live"
            else "order_filled"
        )

        # Optional dry_run filter only applies to live-mode rows.
        # `payload_json` is text, so we extract via DuckDB JSON
        # functions and string-compare against 'true' / 'false'.
        extra_clauses = ""
        params: list = [user_id_str, sid_str, mode_norm]
        if mode_norm == "live" and dry_run is not None:
            wanted = "true" if dry_run else "false"
            extra_clauses = (
                " AND json_extract_string(payload_json, "
                "'$.dry_run') = ?"
            )
            params.append(wanted)

        try:
            rows = query_iceberg_table(
                "algo.events",
                "SELECT type, payload_json, ts_ns "
                "FROM events "
                "WHERE user_id = ? AND strategy_id = ? "
                "  AND mode = ?"
                + extra_clauses + " "
                "ORDER BY ts_ns ASC",
                params,
            )
        except FileNotFoundError:
            rows = []

        # Per-ticker FIFO buys + realised pnl from sells.
        from collections import defaultdict, deque

        buys: dict[str, deque] = defaultdict(deque)
        realised: dict[str, float] = defaultdict(float)
        n_closed_trades: dict[str, int] = defaultdict(int)
        n_signals = 0
        n_rejected = 0
        n_fills = 0
        first_ts: int | None = None
        last_ts: int | None = None
        rejection_reasons: dict[str, int] = defaultdict(int)

        for r in rows:
            t = r["type"]
            try:
                p = json.loads(r["payload_json"])
            except Exception:  # noqa: BLE001
                p = {}
            ts = int(r["ts_ns"])
            if first_ts is None:
                first_ts = ts
            last_ts = ts

            if t == "signal_generated":
                n_signals += 1
            elif t == "signal_rejected":
                n_rejected += 1
                reason = str(p.get("reason") or "unknown")
                rejection_reasons[reason] += 1
            elif t in ("order_filled", "order_filled_live"):
                # Field names diverge across runtimes:
                # - PaperRuntime  : ticker (`SBIN.NS`),  fill_price, fee_inr
                # - LiveRuntime   : symbol (`SBIN`),     price,      fees_inr
                # Normalise here so the FIFO loop below stays
                # one path. Live also tags `.NS` back on so the
                # OHLCV mark lookup hits.
                n_fills += 1
                ticker = str(
                    p.get("ticker")
                    or (
                        f"{p.get('symbol')}.NS"
                        if p.get("symbol") else ""
                    )
                )
                side = str(p.get("side") or "")
                qty = int(p.get("qty") or 0)
                price = float(
                    p.get("fill_price") or p.get("price") or 0.0,
                )
                fee = float(
                    p.get("fee_inr") or p.get("fees_inr") or 0.0,
                )
                if not ticker or qty <= 0 or price <= 0:
                    continue
                if side == "BUY":
                    buys[ticker].append([qty, price, fee])
                elif side == "SELL":
                    rem = qty
                    while rem > 0 and buys[ticker]:
                        lot = buys[ticker][0]
                        take = min(rem, lot[0])
                        # FIFO match — gain = (sell - buy) * qty
                        # minus the proportional buy fee + the
                        # proportional sell fee. Sell fees are
                        # charged on this side of the round trip.
                        proportional_buy_fee = (
                            lot[2] * take / qty
                            if qty > 0 else 0.0
                        )
                        proportional_sell_fee = (
                            fee * take / qty if qty > 0 else 0.0
                        )
                        realised[ticker] += (
                            (price - lot[1]) * take
                            - proportional_buy_fee
                            - proportional_sell_fee
                        )
                        lot[0] -= take
                        rem -= take
                        if lot[0] <= 0:
                            buys[ticker].popleft()
                            n_closed_trades[ticker] += 1

        # Track each ticker's last seen fill price so paper-replay
        # sessions (no live ticks) can mark open positions to the
        # most recent fixture print. Accept both runtime variants:
        # PaperRuntime emits `order_filled` with `ticker` +
        # `fill_price`; LiveRuntime emits `order_filled_live`
        # with `symbol` + `price` (and re-suffixes `.NS` so
        # the OHLCV mark fallback still hits).
        last_fill_price: dict[str, float] = {}
        for r in rows:
            if r["type"] not in ("order_filled", "order_filled_live"):
                continue
            try:
                p = json.loads(r["payload_json"])
            except Exception:  # noqa: BLE001
                continue
            t = str(
                p.get("ticker")
                or (
                    f"{p.get('symbol')}.NS"
                    if p.get("symbol") else ""
                )
            )
            fp = float(
                p.get("fill_price") or p.get("price") or 0.0,
            )
            if t and fp > 0:
                last_fill_price[t] = fp

        # Live-LTP cache (Redis, written by WS multiplexer +
        # PaperRuntime/LiveRuntime on every bar close, 60s TTL).
        # Reading is best-effort — None on cache miss / no Redis.
        try:
            from backend.cache import get_cache
            _cache = get_cache()
        except Exception:  # noqa: BLE001
            _cache = None

        # For dry-run synthetic sessions (rehearsal against a
        # replay fixture or live-ws-with-synthetic-fills), the
        # `last_fill` price IS the most reliable mark because:
        # - Replay fixtures emit historic ticks for the ticker
        #   that wouldn't match the current real-world price.
        # - The Redis cache:ltp:{ticker} key gets overwritten
        #   by live Kite WS ticks regardless of which session
        #   wrote the bar close — so a fixture session's marks
        #   get clobbered by real-world prices and the user
        #   sees nonsensical P&L like a 26% drawdown that's
        #   really just (live_now - fixture_then).
        # Real-money live sessions (mode=live + dry_run=false)
        # want the live tick — keep that path.
        prefer_last_fill = (
            mode_norm == "live" and dry_run is True
        )

        def _resolve_mark(ticker: str) -> tuple[float | None, str]:
            """Return (price, source) for the open-position mark.
            Source ∈ {'live_ltp', 'last_fill', 'ohlcv_close'}.
            """
            if prefer_last_fill and ticker in last_fill_price:
                return last_fill_price[ticker], "last_fill"
            if _cache is not None:
                try:
                    raw = _cache.get(f"cache:ltp:{ticker}")
                    if raw is not None:
                        return float(raw), "live_ltp"
                except Exception:  # noqa: BLE001
                    pass
            if ticker in last_fill_price:
                return last_fill_price[ticker], "last_fill"
            try:
                mk = query_iceberg_table(
                    "stocks.ohlcv",
                    "SELECT close FROM ohlcv "
                    "WHERE ticker = ? "
                    "ORDER BY date DESC LIMIT 1",
                    [ticker],
                )
                if mk:
                    return float(mk[0]["close"]), "ohlcv_close"
            except Exception:  # noqa: BLE001
                pass
            return None, "unknown"

        # Open positions = sum of remaining BUY lots per ticker.
        open_positions: list[dict[str, Any]] = []
        for ticker, lots in buys.items():
            if not lots:
                continue
            qty = sum(l[0] for l in lots)
            if qty <= 0:
                continue
            cost = sum(l[0] * l[1] for l in lots)
            avg_price = cost / qty if qty > 0 else 0.0
            last_price, mark_source = _resolve_mark(ticker)
            unrealised = (
                (last_price - avg_price) * qty
                if last_price is not None else 0.0
            )
            unrealised_pct = (
                (last_price / avg_price - 1.0) * 100.0
                if last_price is not None and avg_price > 0
                else 0.0
            )
            open_positions.append({
                "ticker": ticker,
                "qty": qty,
                "avg_price": avg_price,
                "last_price": last_price,
                "mark_source": mark_source,
                "unrealised_pnl_inr": unrealised,
                "unrealised_pnl_pct": unrealised_pct,
            })

        closed_positions = [
            {
                "ticker": ticker,
                "realised_pnl_inr": realised[ticker],
                "round_trips": n_closed_trades[ticker],
            }
            for ticker in sorted(realised.keys())
            if n_closed_trades[ticker] > 0
        ]

        total_realised = sum(realised.values())
        total_unrealised = sum(
            p["unrealised_pnl_inr"] for p in open_positions
        )

        return {
            "strategy_id": sid_str,
            "first_event_ts_ns": first_ts,
            "last_event_ts_ns": last_ts,
            "n_signals_generated": n_signals,
            "n_signals_rejected": n_rejected,
            "n_fills": n_fills,
            "rejection_reasons": dict(rejection_reasons),
            "open_positions": open_positions,
            "closed_positions": closed_positions,
            "total_realised_pnl_inr": total_realised,
            "total_unrealised_pnl_inr": total_unrealised,
            "total_pnl_inr": total_realised + total_unrealised,
        }

    return router

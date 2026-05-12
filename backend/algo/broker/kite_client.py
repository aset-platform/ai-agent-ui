# backend/algo/broker/kite_client.py
"""Thin wrapper over the official ``kiteconnect.KiteConnect`` client.

v1 = READ-ONLY. ``place_order`` is wired to raise so an accidental
import in the strategy runtime can't push a real order. Instrument
list + profile + WebSocket ticker are the only live paths.

Constructor takes the per-user api_key + (optional) access_token —
both decrypted at the call site by ``credentials_repo``.

Dry-run mode
------------
Set ``ALGO_LIVE_DRY_RUN=true`` (or pass ``dry_run=True`` to the
constructor) to short-circuit all write paths.  No real Kite REST
calls are made; place_order returns a synthetic ``DRY_<hex>`` id and
the runtime synthesises a fill event after ~100 ms.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable
from uuid import uuid4

from kiteconnect import KiteConnect

from backend.algo.broker.exceptions import LtpStaleError

_logger = logging.getLogger(__name__)

UTC = timezone.utc

# Default chosen high enough to be effectively disabled on PR #1 ship.
# Follow-up commit lowers to 5 after 24h soak (per spec §6 rollout).
_DEFAULT_MAX_LTP_AGE_S = 999999


def _read_max_ltp_age_s() -> int:
    """Read ALGO_MAX_LTP_AGE_S env var; fall back to default."""
    raw = os.environ.get("ALGO_MAX_LTP_AGE_S", "").strip()
    if not raw:
        return _DEFAULT_MAX_LTP_AGE_S
    try:
        return int(raw)
    except ValueError:
        _logger.warning(
            "ALGO_MAX_LTP_AGE_S=%r is not an int — using default %d",
            raw, _DEFAULT_MAX_LTP_AGE_S,
        )
        return _DEFAULT_MAX_LTP_AGE_S


def _read_dry_run_env() -> bool:
    """Read ALGO_LIVE_DRY_RUN from env.  Default False.

    DEPRECATED for trading callsites — prefer the per-user
    Redis flag via ``resolve_dry_run_for_user(user_id)``. Env
    is now only the last-resort fallback when no user context
    is available (e.g. read-only OAuth / instrument loader).
    """
    return os.environ.get(
        "ALGO_LIVE_DRY_RUN", "false",
    ).lower() in ("true", "1", "yes")


def resolve_dry_run_for_user(
    user_id: object | None,
) -> bool:
    """Single source of truth for dry-run resolution. Order:

    1. Per-user Redis flag (`algo:dry_run:{user_id}`) — set by
       the Dry-run / Live segment toggle in the UI. THIS IS THE
       AUTHORITATIVE SOURCE for any trading-path callsite.
    2. ``ALGO_LIVE_DRY_RUN`` env var — fallback when user_id is
       None (admin tools, instrument loader, OAuth) OR Redis is
       unavailable.

    Sync wrapper around the async ``dry_run_flag.is_armed`` —
    uses the sync redis client so this can be called from
    KiteClient.__init__ without an event loop. Failures fall
    through to env so the system stays operational under Redis
    outages.
    """
    if user_id is None:
        return _read_dry_run_env()
    try:
        from auth.token_store import get_redis_client
        redis_url = os.environ.get("REDIS_URL", "").strip()
        if not redis_url:
            return _read_dry_run_env()
        client = get_redis_client(redis_url)
        if client is None:
            return _read_dry_run_env()
        raw = client.get(f"algo:dry_run:{user_id}")
        if raw is None:
            return _read_dry_run_env()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        return str(raw).strip().lower() in (
            "1", "true", "yes",
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "resolve_dry_run_for_user: redis read failed "
            "user=%s: %s — falling back to env", user_id, exc,
        )
        return _read_dry_run_env()


class KiteClient:
    """Per-user Kite SDK wrapper.

    Construct with ``api_key``; pass ``access_token`` once you've
    completed the OAuth handshake. The ``api_secret`` is only
    required for the request_token → access_token exchange and
    must NOT be persisted.

    Parameters
    ----------
    dry_run:
        When True (or when ``ALGO_LIVE_DRY_RUN=true`` in env),
        all write paths (place/cancel/modify) are short-circuited.
        No real Kite REST calls are made.  Defaults to the env var
        value; an explicit kwarg overrides env.
    """

    def __init__(
        self,
        api_key: str,
        access_token: str | None = None,
        *,
        dry_run: bool | None = None,
        user_id: object | None = None,
    ) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._kc = KiteConnect(api_key=api_key)
        if access_token:
            self._kc.set_access_token(access_token)
        # Resolution priority for dry_run:
        #   1. Explicit kwarg (highest — used by panic-close +
        #      tests that need to lock in real or synthetic).
        #   2. Per-user Redis flag if user_id provided — the
        #      authoritative source for trading-path callsites.
        #      Single source of truth across the codebase.
        #   3. ALGO_LIVE_DRY_RUN env var (read-only callsites
        #      with no user context: OAuth, instrument loader).
        if dry_run is not None:
            self._dry_run = dry_run
        elif user_id is not None:
            self._dry_run = resolve_dry_run_for_user(user_id)
        else:
            self._dry_run = _read_dry_run_env()
        if self._dry_run:
            _logger.info(
                "KiteClient initialised in DRY_RUN mode — "
                "no real Kite REST calls will be made.",
            )

    @property
    def dry_run(self) -> bool:
        """True when dry-run mode is active."""
        return self._dry_run

    # ---- OAuth ----------------------------------------------------

    def login_url(self) -> str:
        """Public Kite login URL the user clicks to authenticate."""
        return self._kc.login_url()

    def generate_session(
        self, request_token: str, api_secret: str,
    ) -> dict[str, Any]:
        """Exchange a request_token for an access_token + user_id.

        Returns the SDK's ``session`` dict; the caller persists
        ``access_token`` (Fernet-encrypted) and the
        ``access_token_expires_at`` derived from Kite's docs
        (tokens expire daily ~06:00 IST, so we set
        ``next 06:00 IST`` as the expiry).
        """
        return self._kc.generate_session(
            request_token, api_secret=api_secret,
        )

    # ---- Read paths ----------------------------------------------

    def profile(self) -> dict[str, Any]:
        """Authenticated user's Kite profile."""
        return self._kc.profile()

    def instruments(
        self, exchange: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full instrument dump (or filtered by exchange).

        Kite returns a list of ~80 000 entries — caller is expected
        to bulk-upsert into ``algo.instruments``.
        """
        return self._kc.instruments(exchange=exchange) if exchange \
            else self._kc.instruments()

    async def stream_ticks(
        self,
        instrument_tokens: list[int],
        token_to_ticker: dict[int, str],
    ) -> AsyncIterator[dict]:
        """Live KiteTicker WebSocket → Tick stream.

        Caller owns the lifecycle — wrap in ``async for`` and
        break / cancel to stop. Yields ``Tick`` instances (the
        AsyncIterator[dict] type hint is loose to satisfy the
        ABC; concrete return is the Tick model).
        """
        from backend.algo.stream.sources import LiveTickSource

        if self._access_token is None:
            raise RuntimeError(
                "stream_ticks requires an access_token; "
                "complete the OAuth handshake first.",
            )
        src = LiveTickSource(
            api_key=self._api_key,
            access_token=self._access_token,
            instrument_tokens=instrument_tokens,
            token_to_ticker=token_to_ticker,
        )
        async for tick in src:
            yield tick

    # ---- Write paths (V2-5) -------------------------------------

    _ALLOWED_ORDER_TYPES = frozenset({"MARKET", "LIMIT"})
    _ALLOWED_PRODUCTS = frozenset({"CNC"})
    _ALLOWED_VARIETIES = frozenset({"regular"})

    def place_order(
        self,
        *,
        tradingsymbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        order_type: str,
        product: str = "CNC",
        variety: str = "regular",
        price: float = 0.0,
        tag: str = "",
        # ── Order-safety hardening (PR #1) ──────────────────
        last_price: float | None = None,
        last_price_ts: datetime | None = None,
        liquidity_bucket: str | None = None,
        slippage_bps_applied: int | None = None,
        chunk_index: int | None = None,
        chunk_total: int | None = None,
        events_sink: Callable[[dict], None] | None = None,
        session_id: Any = None,
        user_id: Any = None,
        strategy_id: Any = None,
        internal_order_id: str | None = None,
    ) -> str:
        """Place a live order on Kite.  Returns ``kite_order_id``.

        Constraints (v2):
        - order_type MUST be MARKET or LIMIT.  SL / SLM / BO / CO
          are rejected at this layer with a clear ValueError —
          the caller must never reach the SDK for unsupported types.
        - product MUST be CNC (delivery equity).
        - variety MUST be regular.

        Order-safety kwargs (PR #1 — 2026-05-12 spec §3.1, §3.6):
        - ``last_price`` / ``last_price_ts``: reference LTP and its
          timestamp. When ``last_price_ts`` is older than
          ``ALGO_MAX_LTP_AGE_S`` seconds → emits an
          ``order_ltp_stale_blocked`` event and raises
          ``LtpStaleError``. None → gate skipped (legacy callers).
        - ``liquidity_bucket`` / ``slippage_bps_applied``: pre-trade
          decision trace (audit only — populated by PR #2).
        - ``chunk_index`` / ``chunk_total``: accepted-but-unused
          on PR #1; PR #4 will populate them on freeze-chunked
          orders. Pass-through to the submitted event payload.
        - ``events_sink``: callable that receives one
          ``event_row(...)`` dict per emission. Typically the
          runtime's ``self._events.append``. ``None`` → log only.
        - ``session_id`` / ``user_id`` / ``strategy_id``:
          stamped on the emitted event_row. ``None`` → uuid4()
          fallback so panic-close / kill-switch entry points (no
          strategy context) still get a well-formed row.
        - ``internal_order_id``: caller-generated UUID linking
          this submission to the eventual fill / postback. Auto-
          generated when ``None``.

        Raises:
            ValueError: if an unsupported order_type / product /
                variety is passed.
            RuntimeError: if no access_token is set.
            LtpStaleError: if last_price_ts exceeds budget.
        """
        if self._access_token is None:
            raise RuntimeError(
                "place_order requires an access_token; "
                "complete the OAuth handshake first.",
            )
        if internal_order_id is None:
            internal_order_id = str(uuid4())

        if self._dry_run:
            synthetic_id = f"DRY_{uuid4().hex[:12]}"
            _logger.info(
                "[DRY_RUN] place_order symbol=%s side=%s qty=%d "
                "type=%s limit_price=%s product=%s variety=%s "
                "-> %s",
                tradingsymbol, transaction_type, quantity,
                order_type, price, product, variety, synthetic_id,
            )
            self._emit_submitted_event(
                events_sink=events_sink,
                session_id=session_id,
                user_id=user_id,
                strategy_id=strategy_id,
                internal_order_id=internal_order_id,
                kite_order_id=synthetic_id,
                dry_run=True,
                tradingsymbol=tradingsymbol,
                exchange=exchange,
                transaction_type=transaction_type,
                quantity=quantity,
                order_type=order_type,
                product=product,
                variety=variety,
                price=price,
                tag=tag,
                last_price=last_price,
                last_price_ts=last_price_ts,
                liquidity_bucket=liquidity_bucket,
                slippage_bps_applied=slippage_bps_applied,
                chunk_index=chunk_index,
                chunk_total=chunk_total,
                response_raw={"dry_run": True},
            )
            return synthetic_id

        # ── LTP staleness gate (real-money path only) ──────────
        max_age_s = _read_max_ltp_age_s()
        if last_price_ts is not None:
            # Tolerate tz-naive timestamps from legacy callers by
            # assuming UTC — matches Iceberg convention (CLAUDE.md
            # §5.1 iceberg-tz-naive-timestamps).
            ts = (
                last_price_ts
                if last_price_ts.tzinfo is not None
                else last_price_ts.replace(tzinfo=UTC)
            )
            age = (datetime.now(UTC) - ts).total_seconds()
            if age > max_age_s:
                _logger.warning(
                    "place_order BLOCKED: stale LTP symbol=%s "
                    "age=%.1fs max=%ds last_price_ts=%s",
                    tradingsymbol, age, max_age_s, ts.isoformat(),
                )
                self._emit_blocked_event(
                    events_sink=events_sink,
                    session_id=session_id,
                    user_id=user_id,
                    strategy_id=strategy_id,
                    internal_order_id=internal_order_id,
                    symbol=tradingsymbol,
                    side=transaction_type,
                    qty=quantity,
                    last_price_ts=ts,
                    age_seconds=age,
                    max_age_seconds=max_age_s,
                )
                raise LtpStaleError(
                    f"LTP age {age:.1f}s exceeds {max_age_s}s "
                    f"for symbol={tradingsymbol!r}",
                )
        else:
            _logger.warning(
                "place_order: last_price_ts not supplied for "
                "symbol=%s — staleness gate skipped",
                tradingsymbol,
            )

        if order_type not in self._ALLOWED_ORDER_TYPES:
            raise ValueError(
                f"order_type={order_type!r} not supported in v2. "
                f"Only {sorted(self._ALLOWED_ORDER_TYPES)} are "
                f"allowed. SL/SLM/BO/CO are deferred to v3.",
            )
        if product not in self._ALLOWED_PRODUCTS:
            raise ValueError(
                f"product={product!r} not supported in v2. "
                f"Only CNC (delivery equity) is allowed.",
            )
        if variety not in self._ALLOWED_VARIETIES:
            raise ValueError(
                f"variety={variety!r} not supported in v2. "
                f"Only 'regular' is allowed.",
            )
        params: dict[str, Any] = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": order_type,
            "product": product,
        }
        if order_type == "LIMIT":
            params["price"] = price
        # NOTE: kiteconnect-python SDK does not accept
        # market_protection as a kwarg in this version. MARKET
        # orders without market_protection are rejected by Kite
        # at the REST layer (see commit 13001fb). The fix is to
        # ALWAYS use LIMIT orders — every callsite must compute
        # a price (LTP / bar close / OHLCV close) and pass
        # order_type='LIMIT' + price=<float>. This file no longer
        # tries to inject market_protection.
        if tag:
            params["tag"] = tag
        resp = self._kc.place_order(variety=variety, **params)
        # SDK returns {"order_id": "<id>"} or raises KiteException.
        order_id: str = (
            resp.get("order_id", "") if isinstance(resp, dict)
            else str(resp)
        )
        _logger.info(
            "place_order: symbol=%s side=%s qty=%d "
            "order_type=%s kite_order_id=%s",
            tradingsymbol, transaction_type, quantity,
            order_type, order_id,
        )
        self._emit_submitted_event(
            events_sink=events_sink,
            session_id=session_id,
            user_id=user_id,
            strategy_id=strategy_id,
            internal_order_id=internal_order_id,
            kite_order_id=order_id,
            dry_run=False,
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type=order_type,
            product=product,
            variety=variety,
            price=price,
            tag=tag,
            last_price=last_price,
            last_price_ts=last_price_ts,
            liquidity_bucket=liquidity_bucket,
            slippage_bps_applied=slippage_bps_applied,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
            response_raw=resp if isinstance(resp, dict) else {
                "raw": str(resp),
            },
        )
        return order_id

    # ---- Order-safety event helpers (PR #1) ---------------------

    @staticmethod
    def _build_context_block(
        *,
        last_price: float | None,
        last_price_ts: datetime | None,
        liquidity_bucket: str | None,
        slippage_bps_applied: int | None,
        chunk_index: int | None,
        chunk_total: int | None,
    ) -> dict[str, Any]:
        ltp_age: float | None = None
        ts_iso: str | None = None
        if last_price_ts is not None:
            ts = (
                last_price_ts
                if last_price_ts.tzinfo is not None
                else last_price_ts.replace(tzinfo=UTC)
            )
            ts_iso = ts.isoformat()
            ltp_age = (datetime.now(UTC) - ts).total_seconds()
        return {
            "last_price": last_price,
            "last_price_ts": ts_iso,
            "ltp_age_seconds": ltp_age,
            "liquidity_bucket": liquidity_bucket,
            "slippage_bps_applied": slippage_bps_applied,
            "chunk_index": chunk_index,
            "chunk_total": chunk_total,
        }

    def _emit_submitted_event(
        self,
        *,
        events_sink: Callable[[dict], None] | None,
        session_id: Any,
        user_id: Any,
        strategy_id: Any,
        internal_order_id: str,
        kite_order_id: str,
        dry_run: bool,
        tradingsymbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        order_type: str,
        product: str,
        variety: str,
        price: float,
        tag: str,
        last_price: float | None,
        last_price_ts: datetime | None,
        liquidity_bucket: str | None,
        slippage_bps_applied: int | None,
        chunk_index: int | None,
        chunk_total: int | None,
        response_raw: dict[str, Any],
    ) -> None:
        """Build + dispatch a full-payload order_submitted_live row.

        Top-level keys (kite_order_id, dry_run, side, qty, symbol,
        internal_order_id) are preserved for legacy consumers
        (PaperEventsTimeline). Nested request/context/response
        blocks carry the new full-payload audit trail per spec §3.6.
        """
        if events_sink is None:
            return
        # Local import — avoids circular dep if event_writer ever
        # grows a dep on broker. Hot path: micro-cost.
        from backend.algo.backtest.event_writer import event_row

        request_block: dict[str, Any] = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": order_type,
            "product": product,
            "variety": variety,
            "tag": tag,
        }
        if order_type == "LIMIT":
            request_block["price"] = price
        context_block = self._build_context_block(
            last_price=last_price,
            last_price_ts=last_price_ts,
            liquidity_bucket=liquidity_bucket,
            slippage_bps_applied=slippage_bps_applied,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
        )
        payload: dict[str, Any] = {
            # Top-level legacy keys (PaperEventsTimeline reads
            # these at the root; do not nest under `request.*` to
            # avoid breaking the existing renderer).
            "dry_run": dry_run,
            "internal_order_id": internal_order_id,
            "kite_order_id": kite_order_id,
            "symbol": tradingsymbol,
            "side": transaction_type,
            "qty": quantity,
            # Full audit blocks (spec §3.6).
            "request": request_block,
            "context": context_block,
            "response": {"raw": response_raw},
            "submitted_at": datetime.now(UTC).isoformat(),
        }
        sid = session_id if session_id is not None else uuid4()
        uid = user_id if user_id is not None else uuid4()
        try:
            row = event_row(
                session_id=sid,
                user_id=uid,
                strategy_id=strategy_id,
                mode="live",
                type_="order_submitted_live",
                payload=payload,
            )
            events_sink(row)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "order_submitted_live emit failed for "
                "kite_order_id=%s — order placed successfully but "
                "audit row dropped", kite_order_id,
                exc_info=True,
            )

    def _emit_blocked_event(
        self,
        *,
        events_sink: Callable[[dict], None] | None,
        session_id: Any,
        user_id: Any,
        strategy_id: Any,
        internal_order_id: str,
        symbol: str,
        side: str,
        qty: int,
        last_price_ts: datetime,
        age_seconds: float,
        max_age_seconds: int,
    ) -> None:
        if events_sink is None:
            return
        from backend.algo.backtest.event_writer import event_row

        payload = {
            "internal_order_id": internal_order_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "last_price_ts": last_price_ts.isoformat(),
            "age_seconds": age_seconds,
            "max_age_seconds": max_age_seconds,
            "reason": "ltp_stale",
        }
        sid = session_id if session_id is not None else uuid4()
        uid = user_id if user_id is not None else uuid4()
        try:
            row = event_row(
                session_id=sid,
                user_id=uid,
                strategy_id=strategy_id,
                mode="live",
                type_="order_ltp_stale_blocked",
                payload=payload,
            )
            events_sink(row)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "order_ltp_stale_blocked emit failed for symbol=%s",
                symbol, exc_info=True,
            )

    def cancel_order(
        self,
        order_id: str,
        variety: str = "regular",
    ) -> str:
        """Cancel an open order on Kite.  Returns the order_id.

        Raises ``RuntimeError`` if no access_token is set.
        """
        if self._access_token is None:
            raise RuntimeError(
                "cancel_order requires an access_token; "
                "complete the OAuth handshake first.",
            )
        if self._dry_run:
            _logger.info(
                "[DRY_RUN] cancel_order kite_order_id=%s "
                "variety=%s",
                order_id, variety,
            )
            return order_id
        self._kc.cancel_order(variety=variety, order_id=order_id)
        _logger.info(
            "cancel_order: kite_order_id=%s variety=%s",
            order_id, variety,
        )
        return order_id

    def modify_order(
        self,
        order_id: str,
        *,
        variety: str = "regular",
        order_type: str | None = None,
        price: float | None = None,
        quantity: int | None = None,
    ) -> str:
        """Modify price and/or quantity of a LIMIT order.

        Only LIMIT orders can be modified (MARKET orders are
        immediately sent to the exchange).  Returns the order_id.

        Raises:
            ValueError: if order_type is provided and is not LIMIT.
            RuntimeError: if no access_token is set.
        """
        if self._access_token is None:
            raise RuntimeError(
                "modify_order requires an access_token; "
                "complete the OAuth handshake first.",
            )
        if self._dry_run:
            _logger.info(
                "[DRY_RUN] modify_order kite_order_id=%s "
                "variety=%s price=%s qty=%s",
                order_id, variety, price, quantity,
            )
            return order_id
        if order_type is not None and order_type != "LIMIT":
            raise ValueError(
                f"modify_order only supports LIMIT orders; "
                f"got order_type={order_type!r}.",
            )
        params: dict[str, Any] = {}
        if price is not None:
            params["price"] = price
        if quantity is not None:
            params["quantity"] = quantity
        if order_type is not None:
            params["order_type"] = order_type
        self._kc.modify_order(
            variety=variety, order_id=order_id, **params,
        )
        _logger.info(
            "modify_order: kite_order_id=%s variety=%s "
            "price=%s qty=%s",
            order_id, variety, price, quantity,
        )
        return order_id

    def get_positions(self) -> list[dict]:
        """Fetch the user's net positions from Kite.

        Returns the ``net`` list from the broker response.  Each
        element is a dict with at least ``tradingsymbol`` and
        ``quantity``.  Only V2-3+ code paths call this; the method
        is intentionally read-only.

        Raises ``RuntimeError`` if no access_token is set.
        """
        if self._access_token is None:
            raise RuntimeError(
                "get_positions requires an access_token; "
                "complete the OAuth handshake first.",
            )
        resp = self._kc.positions()
        return resp.get("net", [])

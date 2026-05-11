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
from typing import Any, AsyncIterator
from uuid import uuid4

from kiteconnect import KiteConnect

_logger = logging.getLogger(__name__)


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
    ) -> str:
        """Place a live order on Kite.  Returns ``kite_order_id``.

        Constraints (v2):
        - order_type MUST be MARKET or LIMIT.  SL / SLM / BO / CO
          are rejected at this layer with a clear ValueError —
          the caller must never reach the SDK for unsupported types.
        - product MUST be CNC (delivery equity).
        - variety MUST be regular.

        Raises:
            ValueError: if an unsupported order_type / product /
                variety is passed.
            RuntimeError: if no access_token is set.
        """
        if self._access_token is None:
            raise RuntimeError(
                "place_order requires an access_token; "
                "complete the OAuth handshake first.",
            )
        if self._dry_run:
            synthetic_id = f"DRY_{uuid4().hex[:12]}"
            _logger.info(
                "[DRY_RUN] place_order symbol=%s side=%s qty=%d "
                "type=%s limit_price=%s product=%s variety=%s "
                "-> %s",
                tradingsymbol, transaction_type, quantity,
                order_type, price, product, variety, synthetic_id,
            )
            return synthetic_id
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
        return order_id

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

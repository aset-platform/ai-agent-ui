# backend/algo/broker/kite_client.py
"""Thin wrapper over the official ``kiteconnect.KiteConnect`` client.

v1 = READ-ONLY. ``place_order`` is wired to raise so an accidental
import in the strategy runtime can't push a real order. Instrument
list + profile + WebSocket ticker are the only live paths.

Constructor takes the per-user api_key + (optional) access_token —
both decrypted at the call site by ``credentials_repo``.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from kiteconnect import KiteConnect

_logger = logging.getLogger(__name__)


class KiteClient:
    """Per-user Kite SDK wrapper.

    Construct with ``api_key``; pass ``access_token`` once you've
    completed the OAuth handshake. The ``api_secret`` is only
    required for the request_token → access_token exchange and
    must NOT be persisted.
    """

    def __init__(
        self,
        api_key: str,
        access_token: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._kc = KiteConnect(api_key=api_key)
        if access_token:
            self._kc.set_access_token(access_token)

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

    # ---- Write paths (BLOCKED in v1) -----------------------------

    def place_order(self, intent) -> str:  # noqa: ANN001
        raise NotImplementedError(
            "Live trading is v2 — see epic spec § 1 non-goals.",
        )

    def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError(
            "Live trading is v2 — see epic spec § 1 non-goals.",
        )

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

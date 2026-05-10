"""Mock Kite WebSocket server + KiteTicker shim for CI tests.

Instead of standing up a real WebSocket server we monkey-patch
the ``kiteconnect.KiteTicker`` class used inside
``KiteWsMultiplexer._build_ticker`` with a fake
``KiteTickerShim`` that:
 - immediately calls the ``on_connect`` callback when
   ``connect(threaded=True)`` is invoked.
 - exposes ``inject_ticks(ticks)`` to pump ticks into the
   ``on_ticks`` callback.
 - exposes ``force_disconnect()`` to simulate a WS drop.
 - tracks ``subscribed_tokens`` / ``unsubscribed_tokens`` for
   assertions.

Usage in tests::

    from backend.algo.tests.fixtures.mock_kite_ws_server import (
        patch_multiplexer_ticker,
    )

    async def test_something():
        async with patch_multiplexer_ticker() as shim:
            mux = KiteWsMultiplexer(...)
            await mux.start()
            shim.inject_ticks([Tick(...)])
            assert mux.connected
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from unittest.mock import patch

from backend.algo.stream.types import Tick

_logger = logging.getLogger(__name__)


class KiteTickerShim:
    """Minimal shim that mimics kiteconnect.KiteTicker behaviour.

    Attributes mirrored from the real SDK:
        MODE_LTP, on_ticks, on_connect, on_close, on_error,
        subscribe, set_mode, unsubscribe, connect, close.
    """

    MODE_LTP = "ltp"
    MODE_QUOTE = "quote"
    MODE_FULL = "full"

    def __init__(self, api_key: str, access_token: str) -> None:
        self._api_key = api_key
        self._access_token = access_token

        self.subscribed_tokens: list[int] = []
        self.unsubscribed_tokens: list[int] = []
        self._running = False

        # Callbacks set by the multiplexer after construction.
        self.on_ticks = None
        self.on_connect = None
        self.on_close = None
        self.on_error = None

    # -- Kite API surface -------------------------------------------

    def subscribe(self, tokens: list[int]) -> None:
        for t in tokens:
            if t not in self.subscribed_tokens:
                self.subscribed_tokens.append(t)

    def unsubscribe(self, tokens: list[int]) -> None:
        for t in tokens:
            if t in self.subscribed_tokens:
                self.subscribed_tokens.remove(t)
            if t not in self.unsubscribed_tokens:
                self.unsubscribed_tokens.append(t)

    def set_mode(self, mode: str, tokens: list[int]) -> None:
        pass  # accepted, no-op

    def connect(self, threaded: bool = True) -> None:
        """Simulate an immediate successful connect.

        Calls ``on_connect`` synchronously so tests can assert
        ``mux.connected`` immediately after ``await mux.start()``.
        """
        self._running = True
        if self.on_connect:
            self.on_connect(self, None)

    def close(self) -> None:
        if self._running:
            self._running = False
            if self.on_close:
                self.on_close(self, 0, "closed")

    # -- Test helpers -----------------------------------------------

    def inject_ticks(self, ticks: list[Tick]) -> None:
        """Push Tick objects through the on_ticks callback.

        Converts Tick → raw dict (the format KiteTicker delivers).
        """
        if not self.on_ticks:
            return
        raw = [
            {
                "instrument_token": _ticker_to_token(t.ticker),
                "last_price": t.ltp,
                "last_traded_quantity": t.volume,
            }
            for t in ticks
        ]
        self.on_ticks(self, raw)

    def inject_raw(self, raw_ticks: list[dict]) -> None:
        """Push pre-built raw tick dicts (for edge-case tests)."""
        if not self.on_ticks:
            return
        self.on_ticks(self, raw_ticks)

    def force_disconnect(
        self, code: int = 1006, reason: str = "simulated drop",
    ) -> None:
        """Simulate a WS disconnect (calls on_close)."""
        self._running = False
        if self.on_close:
            self.on_close(self, code, reason)


def _ticker_to_token(ticker: str) -> int:
    """Deterministic mock token derived from ticker name (tests only).

    Uses the same mapping as the test fixtures so that token→ticker
    lookups resolve correctly in the multiplexer's _token_to_ticker.
    """
    return abs(hash(ticker)) % (2**20)


# Per-test shim registry — set after the shim is instantiated.
_current_shim: KiteTickerShim | None = None


class _ShimFactory:
    """Callable that captures the shim after construction.

    Used as the ``KiteTicker`` replacement: the multiplexer calls
    ``KiteTicker(api_key, access_token)`` and gets a shim instead.
    """

    def __call__(
        self, api_key: str, access_token: str,
    ) -> KiteTickerShim:
        global _current_shim
        shim = KiteTickerShim(
            api_key=api_key, access_token=access_token,
        )
        _current_shim = shim
        return shim


@asynccontextmanager
async def patch_multiplexer_ticker():
    """Context manager: patches ``kiteconnect.KiteTicker`` inside
    ``_build_ticker`` with ``KiteTickerShim``.

    The real ``_build_ticker`` runs normally and assigns all
    callbacks to the shim — only the underlying WS class is swapped.

    Yields a ``_ShimProxy`` that defers attribute access until the
    shim is created (happens inside ``mux.start()``).

    Usage::

        async with patch_multiplexer_ticker() as shim:
            mux = KiteWsMultiplexer(...)
            await mux.start()
            shim.inject_ticks([...])
    """
    global _current_shim
    _current_shim = None
    factory = _ShimFactory()
    with patch(
        "kiteconnect.KiteTicker",
        factory,
    ):
        yield _ShimProxy()
    _current_shim = None


class _ShimProxy:
    """Deferred proxy — resolves to _current_shim on attribute access.

    This lets ``async with patch_multiplexer_ticker() as shim:``
    return a proxy immediately; the underlying shim is set once
    ``mux.start()`` calls ``_build_ticker`` which instantiates
    ``KiteTickerShim`` via the factory.
    """

    def __getattr__(self, name: str):
        if _current_shim is None:
            raise RuntimeError(
                "KiteTickerShim not yet created — "
                "call mux.start() first",
            )
        return getattr(_current_shim, name)

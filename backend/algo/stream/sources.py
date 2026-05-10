"""Tick sources — Replay (CI fixture) and Live (KiteTicker / WS mux).

A TickSource is an async iterator yielding Tick. Implementations
encapsulate where ticks come from; the resampler doesn't care.

Available sources:
  - ReplayTickSource  — streams from a JSONL fixture (CI / backtests)
  - LiveTickSource    — per-strategy KiteTicker (v1, kept for compat)
  - LiveWsTickSource  — reads from a KiteWsMultiplexer subscriber queue
                        (v2; many strategies share one WS connection)
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator, Protocol

from backend.algo.stream.types import Tick

_logger = logging.getLogger(__name__)


class TickSource(Protocol):
    def __aiter__(self) -> AsyncIterator[Tick]: ...


class ReplayTickSource:
    """Stream ticks from a JSONL fixture.

    Lines beginning with ``#`` or empty lines are skipped, so the
    fixture file can carry inline comments for clarity.

    ``pace`` controls emit rate:
      - ``"fast"`` → emit immediately (CI default)
      - ``"realtime"`` → sleep based on tick ts_ns deltas (manual demo)
    """

    def __init__(
        self, path: Path, pace: str = "fast",
    ) -> None:
        self._path = Path(path)
        self._pace = pace

    async def __aiter__(self) -> AsyncIterator[Tick]:
        prev_ts_ns: int | None = None
        with self._path.open(encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                payload = json.loads(stripped)
                tick = Tick.model_validate(payload)
                if (
                    self._pace == "realtime"
                    and prev_ts_ns is not None
                ):
                    delay_s = max(
                        0.0,
                        (tick.ts_ns - prev_ts_ns) / 1_000_000_000,
                    )
                    await asyncio.sleep(delay_s)
                prev_ts_ns = tick.ts_ns
                yield tick


class LiveTickSource:
    """KiteTicker WebSocket → Tick stream.

    The kiteconnect.KiteTicker library is callback-based; we adapt
    it into an async iterator by accumulating ticks into an asyncio
    queue inside the on_ticks callback and yielding from there.

    Connection lifecycle: pass an instrument_tokens list at
    construction; when ``__aiter__`` starts, we ``connect()`` (in a
    background thread) and unwind on the on_close sentinel or
    explicit ``close()``.
    """

    def __init__(
        self,
        api_key: str,
        access_token: str,
        instrument_tokens: list[int],
        token_to_ticker: dict[int, str],
    ) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._instrument_tokens = instrument_tokens
        self._token_to_ticker = token_to_ticker
        self._queue: asyncio.Queue[Tick | None] = asyncio.Queue()
        self._kt = None  # KiteTicker, lazy-imported

    def _build_ticker(self):
        import time

        from kiteconnect import KiteTicker
        kt = KiteTicker(self._api_key, self._access_token)
        loop = asyncio.get_running_loop()

        def on_ticks(_ws, ticks):
            now_ns = int(time.time() * 1_000_000_000)
            for raw in ticks:
                tok = raw.get("instrument_token")
                ticker = self._token_to_ticker.get(tok)
                if not ticker:
                    continue
                tick = Tick(
                    ticker=ticker,
                    ts_ns=now_ns,
                    ltp=float(raw.get("last_price", 0) or 0),
                    volume=int(
                        raw.get("last_traded_quantity", 0) or 0,
                    ),
                )
                loop.call_soon_threadsafe(
                    self._queue.put_nowait, tick,
                )

        def on_connect(ws, _resp):
            ws.subscribe(self._instrument_tokens)
            ws.set_mode(ws.MODE_LTP, self._instrument_tokens)

        def on_close(_ws, *_args):
            loop.call_soon_threadsafe(
                self._queue.put_nowait, None,
            )

        kt.on_ticks = on_ticks
        kt.on_connect = on_connect
        kt.on_close = on_close
        return kt

    async def __aiter__(self) -> AsyncIterator[Tick]:
        if self._kt is None:
            self._kt = self._build_ticker()
            self._kt.connect(threaded=True)
        while True:
            tick = await self._queue.get()
            if tick is None:
                return
            yield tick

    def close(self) -> None:
        if self._kt is not None:
            try:
                self._kt.close()
            except Exception:  # noqa: BLE001
                _logger.exception("KiteTicker close failed")


class LiveWsTickSource:
    """Adapts a KiteWsMultiplexer subscriber queue to TickSource.

    This is the v2 live tick source. One multiplexer per user; each
    strategy gets its own asyncio.Queue via ``mux.subscribe()``.
    On stop, ``stop()`` must be called to unsubscribe from the mux.

    Unlike ``LiveTickSource``, this source does NOT own the WS
    connection — the multiplexer is shared across strategies.
    """

    def __init__(
        self,
        *,
        user_id,  # UUID — kept as Any to avoid circular import
        strategy_id,  # UUID
        queue,  # asyncio.Queue[Tick | None]
        mux,  # KiteWsMultiplexer reference for unsubscribe
    ) -> None:
        self._user_id = user_id
        self._strategy_id = strategy_id
        self._queue = queue
        self._mux = mux
        self._stopped = False

    async def __aiter__(self) -> "AsyncIterator[Tick]":
        while not self._stopped:
            tick = await self._queue.get()
            if tick is None:
                # EOF sentinel — multiplexer closed or strategy
                # unsubscribed.
                return
            yield tick

    async def stop(self) -> None:
        """Unsubscribe from the multiplexer; drains queue EOF."""
        if not self._stopped:
            self._stopped = True
            await self._mux.unsubscribe(self._strategy_id)

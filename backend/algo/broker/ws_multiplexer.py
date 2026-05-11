"""KiteWsMultiplexer — per-user persistent Kite WS connection.

Maintains a single KiteTicker WebSocket per user and fans out
ticks to per-(user, strategy) asyncio.Queue subscribers.

Lifecycle:
  1. Constructed with api_key + access_token (decrypted by caller).
  2. ``start()`` — opens the WS connection, starts the on_ticks
     callback pump.
  3. ``subscribe(strategy_id, tokens, token_to_ticker)`` — increments
     ref-counts, calls ``kite_ticker.subscribe(new_tokens)``.
  4. ``unsubscribe(strategy_id)`` — decrements ref-counts;
     calls ``kite_ticker.unsubscribe(tokens_no_longer_needed)``.
  5. When ref-count for all tokens reaches 0 the connection is
     still held open (it will be torn down by ``close()`` once the
     registry removes the entry).
  6. ``close()`` — cancels reconnect loop, closes WS.

Gap-fill on reconnect:
  On each successful reconnect the multiplexer notes the last tick
  timestamp per token and requests missing 1m bars from Kite
  historical API, replaying them into each subscriber's queue.

Backpressure:
  Each subscriber queue is bounded (``QUEUE_MAX_SIZE``).  When full
  the oldest item is dropped and a WARNING is logged.  A
  ``ws_backpressure_drop`` event is recorded via event_row helper.

Thread-safety:
  KiteTicker callbacks run in a background thread.  All cross-thread
  queue puts use ``loop.call_soon_threadsafe``.  Subscribe/unsubscribe
  are called from async context only.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

UTC = timezone.utc

from backend.algo.stream.types import Tick

_logger = logging.getLogger(__name__)

QUEUE_MAX_SIZE = 1_000
_MAX_BACKOFF_S = 60.0
_MIN_BACKOFF_S = 1.0
_GAP_TOO_LARGE_S = 3_600  # 1 hour: abandon gap-fill


class KiteWsMultiplexer:
    """Single Kite WebSocket per user, fan-out to many strategies.

    Args:
        user_id: The owning user's UUID.
        api_key: Kite API key (plaintext, decrypted by caller).
        access_token: Active Kite session access token.
    """

    def __init__(
        self,
        *,
        user_id: UUID,
        api_key: str,
        access_token: str,
    ) -> None:
        self._user_id = user_id
        self._api_key = api_key
        self._access_token = access_token

        # strategy_id → asyncio.Queue[Tick | None]
        self._queues: dict[UUID, asyncio.Queue[Tick | None]] = {}
        # token → {strategy_ids} (ref-count set)
        self._token_subs: dict[int, set[UUID]] = {}
        # strategy_id → {tokens}
        self._strategy_tokens: dict[UUID, set[int]] = {}
        # token → ticker
        self._token_to_ticker: dict[int, str] = {}
        # last tick ns per token (gap-fill tracking)
        self._last_tick_ns: dict[int, int] = {}

        self._kt = None          # KiteTicker instance
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        self._closed = False
        self._reconnect_task: asyncio.Task | None = None
        self._backoff_s: float = _MIN_BACKOFF_S

        # session_id for event rows (WS-level session)
        from uuid import uuid4
        self._session_id: UUID = uuid4()
        self._ws_events: list[dict[str, Any]] = []

        # Health observability — OBS-1.
        # ``last_tick_at`` tracks the wall-clock time of the most
        # recent tick across all subscribed tokens (tz-naive UTC,
        # Iceberg convention per CLAUDE.md §5.1). ``tick_count_today``
        # is a process-local counter zeroed daily at IST midnight by
        # the ``algo_ws_tick_count_reset`` job.
        self.last_tick_at: datetime | None = None
        self.tick_count_today: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the WS connection loop."""
        if self._closed:
            raise RuntimeError("Multiplexer already closed")
        self._loop = asyncio.get_running_loop()
        await self._connect()

    def subscribe(
        self,
        strategy_id: UUID,
        tokens: list[int],
        token_to_ticker: dict[int, str],
    ) -> asyncio.Queue[Tick | None]:
        """Register a strategy. Returns its dedicated tick queue.

        Idempotent for same strategy_id — returns existing queue.
        """
        if strategy_id not in self._queues:
            self._queues[strategy_id] = asyncio.Queue(
                maxsize=QUEUE_MAX_SIZE,
            )
            self._strategy_tokens[strategy_id] = set()

        new_tokens: list[int] = []
        for tok in tokens:
            if tok not in self._token_subs:
                self._token_subs[tok] = set()
                new_tokens.append(tok)
            self._token_subs[tok].add(strategy_id)
            self._strategy_tokens[strategy_id].add(tok)
            # Merge token→ticker map
            if tok in token_to_ticker:
                self._token_to_ticker[tok] = token_to_ticker[tok]

        if new_tokens and self._kt is not None and self._connected:
            try:
                self._kt.subscribe(new_tokens)
                self._kt.set_mode(
                    self._kt.MODE_LTP, new_tokens,
                )
            except Exception:
                _logger.warning(
                    "subscribe failed for tokens %s", new_tokens,
                    exc_info=True,
                )
        return self._queues[strategy_id]

    async def unsubscribe(self, strategy_id: UUID) -> None:
        """Deregister a strategy and decrement token ref-counts.

        Tokens with zero remaining subscribers are unsubscribed
        from Kite.
        """
        if strategy_id not in self._queues:
            return

        tokens = self._strategy_tokens.pop(strategy_id, set())
        dead_tokens: list[int] = []
        for tok in tokens:
            subs = self._token_subs.get(tok, set())
            subs.discard(strategy_id)
            if not subs:
                dead_tokens.append(tok)
                self._token_subs.pop(tok, None)
                self._token_to_ticker.pop(tok, None)
                self._last_tick_ns.pop(tok, None)

        if dead_tokens and self._kt is not None and self._connected:
            try:
                self._kt.unsubscribe(dead_tokens)
            except Exception:
                _logger.warning(
                    "unsubscribe failed for tokens %s", dead_tokens,
                    exc_info=True,
                )

        # Signal queue EOF to let the consumer drain cleanly.
        q = self._queues.pop(strategy_id, None)
        if q is not None:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def close(self) -> None:
        """Tear down the connection and signal all queues EOF."""
        self._closed = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reconnect_task = None

        self._disconnect_kt()
        # Signal EOF to all waiting queues.
        for q in self._queues.values():
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

        # Flush any pending WS events.
        if self._ws_events:
            self._flush_events()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    @property
    def subscribed_tokens(self) -> int:
        """Total distinct instrument tokens currently subscribed."""
        return len(self._token_subs)

    def health_snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable health view (OBS-1).

        ``last_tick_at`` is left as a ``datetime | None`` — the
        endpoint serialiser converts to ISO 8601 UTC ``Z``.
        """
        return {
            "connected": self._connected,
            "subscriber_count": len(self._queues),
            "subscribed_tokens": len(self._token_subs),
            "last_tick_at": self.last_tick_at,
            "tick_count_today": self.tick_count_today,
        }

    def reset_tick_count(self) -> None:
        """Zero the per-day counter (called by the IST-midnight job).

        ``last_tick_at`` is intentionally preserved so the endpoint
        keeps reporting the most recent tick wall-clock even after
        the day rollover.
        """
        self.tick_count_today = 0

    # ------------------------------------------------------------------
    # Internal connection management
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        """Build and connect a KiteTicker instance (non-blocking)."""
        if self._closed:
            return
        try:
            self._kt = self._build_ticker()
            self._kt.connect(threaded=True)
            # _connected flag is set inside on_connect callback.
        except Exception:
            _logger.exception(
                "KiteWsMultiplexer: connect() raised for user=%s",
                self._user_id,
            )
            await self._schedule_reconnect()

    def _build_ticker(self):
        """Import KiteTicker and wire callbacks.

        The import is deferred (not module-level) so that the module
        can be imported in environments without kiteconnect.

        Tests patch ``kiteconnect.KiteTicker`` to inject the shim.
        """
        import kiteconnect as _kc
        KiteTicker = _kc.KiteTicker
        kt = KiteTicker(self._api_key, self._access_token)
        loop = self._loop

        # Per-process LTP cache writer — lazy-imported so the
        # multiplexer test doubles (which don't load backend.cache)
        # keep working. Writes to Redis under `cache:ltp:{ticker}`
        # with a 60s TTL so the paper P&L summary endpoint can
        # mark open positions to live ticks instead of yesterday's
        # OHLCV close. Best-effort — never blocks the tick path.
        try:
            from backend.cache import get_cache
            _ltp_cache = get_cache()
        except Exception:  # noqa: BLE001
            _ltp_cache = None

        def on_ticks(_ws, ticks):
            now_ns = int(time.time() * 1_000_000_000)
            # Health: stamp arrival of any tick batch and increment
            # the per-day counter regardless of whether we have a
            # ticker mapping for it. Tz-naive UTC (Iceberg convention).
            if ticks:
                self.last_tick_at = datetime.now(UTC).replace(
                    tzinfo=None,
                )
                self.tick_count_today += len(ticks)
            for raw in ticks:
                tok = raw.get("instrument_token")
                if tok is None:
                    continue
                ticker = self._token_to_ticker.get(tok)
                if not ticker:
                    continue
                ltp_val = float(raw.get("last_price", 0) or 0)
                tick = Tick(
                    ticker=ticker,
                    ts_ns=now_ns,
                    ltp=ltp_val,
                    volume=int(
                        raw.get("last_traded_quantity", 0) or 0,
                    ),
                )
                # Best-effort live-LTP cache write. 60s TTL covers
                # market gaps; reads return None outside that window
                # and the summary endpoint falls back to OHLCV.
                if _ltp_cache is not None and ltp_val > 0:
                    try:
                        _ltp_cache.set(
                            f"cache:ltp:{ticker}",
                            str(ltp_val),
                            ttl=60,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                self._last_tick_ns[tok] = now_ns
                subs = self._token_subs.get(tok, set())
                for sid in subs:
                    q = self._queues.get(sid)
                    if q is None:
                        continue
                    if q.full():
                        # Drop oldest to make room.
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        _logger.warning(
                            "ws_backpressure_drop user=%s "
                            "strategy=%s token=%s",
                            self._user_id, sid, tok,
                        )
                        loop.call_soon_threadsafe(
                            self._record_backpressure_event,
                            sid, tok,
                        )
                    loop.call_soon_threadsafe(
                        q.put_nowait, tick,
                    )

        def on_connect(ws, _resp):
            self._connected = True
            self._backoff_s = _MIN_BACKOFF_S
            _logger.info(
                "KiteWsMultiplexer: connected user=%s",
                self._user_id,
            )
            # Re-subscribe all known tokens.
            all_tokens = list(self._token_subs.keys())
            if all_tokens:
                ws.subscribe(all_tokens)
                ws.set_mode(ws.MODE_LTP, all_tokens)
            # Emit ws_connected event.
            loop.call_soon_threadsafe(
                self._emit_ws_event,
                "ws_connected",
                {"token_count": len(all_tokens)},
            )
            # Kick off gap-fill in the event loop.
            loop.call_soon_threadsafe(
                self._schedule_gap_fill_sync,
            )

        def on_close(_ws, code, reason):
            self._connected = False
            _logger.warning(
                "KiteWsMultiplexer: disconnected user=%s "
                "code=%s reason=%s",
                self._user_id, code, reason,
            )
            loop.call_soon_threadsafe(
                self._emit_ws_event,
                "ws_disconnected",
                {"code": code, "reason": str(reason)},
            )
            if not self._closed:
                loop.call_soon_threadsafe(
                    self._trigger_reconnect,
                )

        def on_error(_ws, code, reason):
            _logger.error(
                "KiteWsMultiplexer: error user=%s code=%s %s",
                self._user_id, code, reason,
            )

        kt.on_ticks = on_ticks
        kt.on_connect = on_connect
        kt.on_close = on_close
        kt.on_error = on_error
        return kt

    def _disconnect_kt(self) -> None:
        if self._kt is not None:
            try:
                self._kt.close()
            except Exception:
                _logger.debug(
                    "KiteTicker.close() raised", exc_info=True,
                )
            self._kt = None
        self._connected = False

    def _trigger_reconnect(self) -> None:
        """Schedule reconnect from the event loop thread."""
        if self._closed:
            return
        if (
            self._reconnect_task is None
            or self._reconnect_task.done()
        ):
            self._reconnect_task = asyncio.ensure_future(
                self._schedule_reconnect(),
            )

    async def _schedule_reconnect(self) -> None:
        """Exponential-backoff reconnect loop."""
        while not self._closed:
            backoff = self._backoff_s
            _logger.info(
                "KiteWsMultiplexer: reconnecting in %.1fs user=%s",
                backoff, self._user_id,
            )
            await asyncio.sleep(backoff)
            if self._closed:
                return
            self._backoff_s = min(
                self._backoff_s * 2, _MAX_BACKOFF_S,
            )
            self._disconnect_kt()
            try:
                self._kt = self._build_ticker()
                self._kt.connect(threaded=True)
                return  # on_connect callback will set connected flag
            except Exception:
                _logger.exception(
                    "KiteWsMultiplexer: reconnect attempt failed "
                    "user=%s",
                    self._user_id,
                )
                continue

    # ------------------------------------------------------------------
    # Gap-fill
    # ------------------------------------------------------------------

    def _schedule_gap_fill_sync(self) -> None:
        """Called via call_soon_threadsafe — schedule gap-fill task."""
        if self._loop is not None:
            self._loop.create_task(self._run_gap_fill())

    async def _run_gap_fill(self) -> None:
        """Pull missing 1m bars for each token from Kite historical."""
        from backend.algo.broker.ws_gap_fill import gap_fill_token

        from backend.algo.broker import ws_gap_fill as _gf_mod

        now_ns = int(time.time() * 1_000_000_000)
        for tok, last_ns in list(self._last_tick_ns.items()):
            ticker = self._token_to_ticker.get(tok)
            if ticker is None:
                continue
            missing_s = (now_ns - last_ns) / 1_000_000_000
            if missing_s < 60:
                # Less than 1m gap — KiteTicker fills it in-stream.
                continue
            if missing_s > _GAP_TOO_LARGE_S:
                _logger.warning(
                    "ws_gap_too_large user=%s token=%s "
                    "missing_s=%.0f — abandoning gap-fill",
                    self._user_id, tok, missing_s,
                )
                self._emit_ws_event(
                    "ws_gap_too_large",
                    {
                        "token": tok,
                        "ticker": ticker,
                        "missing_s": int(missing_s),
                    },
                )
                continue

            try:
                ticks = await asyncio.to_thread(
                    _gf_mod.gap_fill_token,
                    api_key=self._api_key,
                    access_token=self._access_token,
                    token=tok,
                    ticker=ticker,
                    last_ns=last_ns,
                    now_ns=now_ns,
                )
            except Exception:
                _logger.warning(
                    "gap_fill_token failed token=%s", tok,
                    exc_info=True,
                )
                continue

            subs = self._token_subs.get(tok, set())
            for sid in subs:
                q = self._queues.get(sid)
                if q is None:
                    continue
                for tick in ticks:
                    try:
                        q.put_nowait(tick)
                    except asyncio.QueueFull:
                        pass

            self._emit_ws_event(
                "ws_gap_filled",
                {
                    "token": tok,
                    "ticker": ticker,
                    "missing_s": int(missing_s),
                    "ticks_replayed": len(ticks),
                },
            )
            _logger.info(
                "ws_gap_filled user=%s token=%s "
                "missing_s=%.0f ticks=%d",
                self._user_id, tok, missing_s, len(ticks),
            )

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    def _emit_ws_event(
        self,
        type_: str,
        payload: dict[str, Any],
    ) -> None:
        """Queue a WS-lifecycle event for batch flush."""
        from backend.algo.backtest.event_writer import event_row
        row = event_row(
            session_id=self._session_id,
            user_id=self._user_id,
            strategy_id=None,
            mode="live-ws",
            type_=type_,
            payload=payload,
        )
        self._ws_events.append(row)
        # Flush in batches of 50 to bound memory.
        if len(self._ws_events) >= 50:
            self._flush_events()

    def _flush_events(self) -> None:
        if not self._ws_events:
            return
        try:
            from backend.algo.backtest.event_writer import (
                flush_events,
            )
            flush_events(self._ws_events)
            self._ws_events = []
        except Exception:
            _logger.warning(
                "ws event flush failed", exc_info=True,
            )
            self._ws_events = []

    def _record_backpressure_event(
        self, strategy_id: UUID, token: int,
    ) -> None:
        self._emit_ws_event(
            "ws_backpressure_drop",
            {
                "strategy_id": str(strategy_id),
                "token": token,
            },
        )

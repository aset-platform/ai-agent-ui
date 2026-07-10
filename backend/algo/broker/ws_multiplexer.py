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
  the oldest item is dropped.  Drops are aggregated per (user,
  strategy) and a ``ws_backpressure_drop`` summary is recorded to the
  per-user Redis store (``ws_event_store``) at most once per window.

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
# Backpressure-drop events are aggregated per (user, strategy) into a
# single summary event at most once per this window. Under a tick
# firehose the per-drop rate can hit ~50/s — one Iceberg commit per
# drop bloated algo.events to millions of rows + GB of metadata. The
# first drop in a window emits immediately (onset visibility); the
# rest are counted and surface on the next window / on close.
_BP_AGG_WINDOW_S = 60
_BP_AGG_WINDOW_NS = _BP_AGG_WINDOW_S * 1_000_000_000
_MAX_BACKOFF_S = 60.0
_MIN_BACKOFF_S = 1.0
_GAP_TOO_LARGE_S = 3_600  # 1 hour: abandon gap-fill
_CONNECT_TIMEOUT_S = 15.0
_MAX_FAST_REBUILD_ATTEMPTS = 2
_STALENESS_CHECK_INTERVAL_S = 60.0
_STALE_TICK_THRESHOLD_S = 90.0


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
        # Universe tokens — subscribed at connect time to feed the
        # global cache:ltp:{ticker} Redis cache for portfolio /
        # dashboard widgets. Membership here protects the token
        # from unsubscribe cleanup when a strategy that *also*
        # used it is torn down (the strategy can come and go but
        # the universe subscription is sticky for the WS lifetime).
        self._universe_tokens: set[int] = set()

        self._kt = None          # KiteTicker instance
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        self._closed = False
        # Set when the WS upgrade is rejected with a non-retryable
        # auth error (403 — expired/invalid Kite token). Reconnecting
        # cannot fix it, so we halt the reconnect loop and require a
        # fresh Zerodha login rather than hammering Kite forever.
        self._auth_failed = False
        self._reconnect_task: asyncio.Task | None = None
        self._backoff_s: float = _MIN_BACKOFF_S
        # Timestamp of the last successful connect (monotonic). Used
        # to decide whether a connection was "stable" before resetting
        # the exponential backoff — a brief connect that immediately
        # drops should NOT reset backoff to MIN (that causes a
        # reconnect storm and Kite rate-limiting).
        self._connect_ts: float = 0.0

        # ASETPLTFRM-470 — connect-timeout watchdog. Every existing
        # reconnect mechanism below is triggered by a Kite callback
        # (on_close/on_error) — in the documented wedge incident, NO
        # callback ever fired for 16 hours. This watchdog is timer-
        # based instead, so it doesn't depend on a callback that may
        # never come.
        self._connect_timeout_task: asyncio.Task | None = None
        self._rebuild_attempts: int = 0
        self._wedge_escalated: bool = False
        self._staleness_task: asyncio.Task | None = None

        # Backpressure-drop aggregation — per strategy_id rolling
        # counter + last-emit timestamp (ns). See _BP_AGG_WINDOW_S.
        self._bp_drops: dict[UUID, int] = {}
        self._bp_last_emit_ns: dict[UUID, int] = {}

        # Gap-fill single-flight task (5.2).
        self._gap_fill_task: asyncio.Task | None = None

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
        self._staleness_task = asyncio.ensure_future(
            self._watch_staleness_loop(),
        )

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

    def subscribe_universe(
        self,
        token_to_ticker: dict[int, str],
    ) -> int:
        """Subscribe a sticky universe of tokens for the global
        LTP Redis cache. Idempotent — only new tokens are sent
        to Kite. No per-strategy queue fanout: ticks for these
        tokens land in ``on_ticks`` only for the
        ``cache:ltp:{ticker}`` write side-effect.

        Returns the count of NEW tokens registered (excluding
        those already known).
        """
        new_tokens: list[int] = []
        for tok, ticker in token_to_ticker.items():
            if tok in self._universe_tokens:
                continue
            self._universe_tokens.add(tok)
            self._token_to_ticker[tok] = ticker
            new_tokens.append(tok)

        if new_tokens and self._kt is not None and self._connected:
            try:
                self._kt.subscribe(new_tokens)
                self._kt.set_mode(
                    self._kt.MODE_LTP, new_tokens,
                )
            except Exception:
                _logger.warning(
                    "subscribe_universe failed for %d tokens",
                    len(new_tokens),
                    exc_info=True,
                )
                # Best-effort: leave them registered locally so
                # a subsequent reconnect's resubscribe picks them
                # up via the on_connect handler.
        _logger.info(
            "ws_multiplexer: universe subscribed %d new tokens "
            "(%d total) user=%s",
            len(new_tokens), len(self._universe_tokens),
            self._user_id,
        )
        return len(new_tokens)

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
                # Universe-level subscription pins the token so
                # the global LTP cache keeps refreshing even when
                # no strategy is using it.
                if tok in self._universe_tokens:
                    self._token_subs.pop(tok, None)
                    continue
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

        # Prune backpressure tracking so dicts don't leak (5.3).
        self._bp_drops.pop(strategy_id, None)
        self._bp_last_emit_ns.pop(strategy_id, None)

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
            except asyncio.CancelledError:
                pass
            except Exception:
                _logger.warning(
                    "ws_multiplexer: reconnect_task raised on close",
                    exc_info=True,
                )
            self._reconnect_task = None

        if self._connect_timeout_task is not None:
            self._connect_timeout_task.cancel()
            try:
                await self._connect_timeout_task
            except asyncio.CancelledError:
                pass
            except Exception:
                _logger.warning(
                    "ws_multiplexer: connect_timeout_task raised "
                    "on close",
                    exc_info=True,
                )
            self._connect_timeout_task = None

        if self._staleness_task is not None:
            self._staleness_task.cancel()
            try:
                await self._staleness_task
            except asyncio.CancelledError:
                pass
            except Exception:
                _logger.warning(
                    "ws_multiplexer: staleness_task raised on close",
                    exc_info=True,
                )
            self._staleness_task = None

        # Cancel any in-flight gap-fill (5.2).
        if self._gap_fill_task is not None and not (
            self._gap_fill_task.done()
        ):
            self._gap_fill_task.cancel()
            try:
                await self._gap_fill_task
            except asyncio.CancelledError:
                pass
            except Exception:
                _logger.warning(
                    "ws_multiplexer: gap_fill_task raised on close",
                    exc_info=True,
                )
            self._gap_fill_task = None

        self._disconnect_kt()
        # Signal EOF to all waiting queues.
        for q in self._queues.values():
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

        # Flush any pending backpressure counts (→ Redis store).
        self._flush_backpressure_residual()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def auth_failed(self) -> bool:
        """True once a non-retryable 403 halted this multiplexer.

        Such an instance holds a stale access token and will not
        reconnect — the registry must rebuild a fresh multiplexer
        (with a new token) rather than hand this one back.
        """
        return self._auth_failed

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
            "wedge_escalated": self._wedge_escalated,
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
            self._arm_connect_timeout()
        except Exception:
            _logger.exception(
                "KiteWsMultiplexer: connect() raised for user=%s",
                self._user_id,
            )
            await self._schedule_reconnect()

    def _arm_connect_timeout(self) -> None:
        """Start (or restart) the connect-timeout watchdog task."""
        if self._connect_timeout_task is not None:
            self._connect_timeout_task.cancel()
        self._connect_timeout_task = asyncio.ensure_future(
            self._watch_connect_timeout(),
        )

    async def _watch_connect_timeout(self) -> None:
        """Timer-based wedge detection — does NOT depend on any
        Kite callback firing. See ASETPLTFRM-470."""
        await asyncio.sleep(_CONNECT_TIMEOUT_S)
        if self._connected or self._closed or self._auth_failed:
            return
        _logger.error(
            "KiteWsMultiplexer: connect() wedged (no callback "
            "within %.0fs) user=%s attempt=%d",
            _CONNECT_TIMEOUT_S, self._user_id,
            self._rebuild_attempts + 1,
        )
        self._emit_ws_event("ws_connect_timeout", {
            "timeout_s": _CONNECT_TIMEOUT_S,
            "attempt": self._rebuild_attempts + 1,
        })
        await self._handle_wedge()

    async def _watch_staleness_loop_once(self) -> None:
        """One staleness check — split out from the sleep loop so
        tests can invoke it directly without waiting real wall-clock
        time. See ASETPLTFRM-470."""
        from backend.algo.live.reconciliation import (
            is_market_open_ist,
        )

        if not is_market_open_ist():
            return
        if not self._connected:
            return  # the connect-timeout watchdog owns this case
        has_tokens = bool(self._token_subs or self._universe_tokens)
        if not has_tokens:
            return
        if self.last_tick_at is None:
            return  # freshly connected, no tick expected yet
        age_s = (
            datetime.now(UTC).replace(tzinfo=None)
            - self.last_tick_at
        ).total_seconds()
        if age_s < _STALE_TICK_THRESHOLD_S:
            return
        _logger.error(
            "KiteWsMultiplexer: tick stream stale (%.0fs, "
            "threshold %.0fs) despite connected=True user=%s — "
            "treating as silent stall",
            age_s, _STALE_TICK_THRESHOLD_S, self._user_id,
        )
        self._emit_ws_event("ws_stale_stream_detected", {
            "age_s": int(age_s),
        })
        await self._handle_wedge()

    async def _watch_staleness_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(_STALENESS_CHECK_INTERVAL_S)
            if self._closed:
                return
            try:
                await self._watch_staleness_loop_once()
            except Exception:
                _logger.exception(
                    "KiteWsMultiplexer: staleness check raised "
                    "user=%s — will retry next interval",
                    self._user_id,
                )

    async def _handle_wedge(self) -> None:
        """Shared rebuild/escalate logic for BOTH the connect-timeout
        watchdog and the staleness watchdog (Task 2). Fast in-process
        rebuild for the first _MAX_FAST_REBUILD_ATTEMPTS attempts,
        then escalate (alert, once) and fall back to the existing
        exponential-backoff reconnect loop — escalation is an
        ALERTING signal, not a give-up signal; retries continue."""
        if self._closed or self._auth_failed:
            return
        self._rebuild_attempts += 1
        if self._rebuild_attempts <= _MAX_FAST_REBUILD_ATTEMPTS:
            _logger.warning(
                "KiteWsMultiplexer: fast-rebuild attempt %d/%d "
                "user=%s",
                self._rebuild_attempts, _MAX_FAST_REBUILD_ATTEMPTS,
                self._user_id,
            )
            self._disconnect_kt()
            try:
                self._kt = self._build_ticker()
                self._kt.connect(threaded=True)
                # Skip re-arming if connect() already fired
                # on_connect synchronously (e.g. the test shim) —
                # on_connect already cleared/cancelled the watchdog
                # and set _connected=True, so arming here would only
                # create an immediately-superfluous task.
                if not self._connected:
                    self._arm_connect_timeout()
            except Exception:
                _logger.exception(
                    "KiteWsMultiplexer: fast-rebuild connect() "
                    "raised user=%s", self._user_id,
                )
                await self._handle_wedge()
            return
        if not self._wedge_escalated:
            self._wedge_escalated = True
            _logger.error(
                "KiteWsMultiplexer: wedge ESCALATED after %d fast "
                "rebuild attempts user=%s — falling back to "
                "backoff reconnect loop, manual restart may be "
                "required",
                self._rebuild_attempts, self._user_id,
            )
            self._emit_ws_event("ws_wedge_escalated", {
                "attempts": self._rebuild_attempts,
            })
        self._disconnect_kt()
        self._trigger_reconnect()

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
            # 5.3: accumulate valid ltp values per ticker so we can
            # flush to Redis in ONE pipeline after the full batch.
            ltp_batch: dict[str, str] = {}
            for raw in ticks:
                # 5.1: wrap the per-tick body so one bad packet can
                # never kill the loop. Caught exceptions are logged
                # at WARNING with full traceback for debugging.
                try:
                    tok = raw.get("instrument_token")
                    if tok is None:
                        continue
                    ticker = self._token_to_ticker.get(tok)
                    if not ticker:
                        continue
                    ltp_val = float(raw.get("last_price", 0) or 0)
                    # 5.1: drop zero/negative LTP before constructing
                    # Tick (Tick.ltp has Field(gt=0) — a ValidationError
                    # from a legit pre-open/illiquid 0-price tick would
                    # propagate out of the WS thread and kill delivery).
                    if ltp_val <= 0:
                        continue
                    # ASETPLTFRM-372 — capture the authoritative
                    # exchange-emission timestamp when Kite supplies
                    # it (full/quote-mode packets, NOT LTP-mode).
                    # kiteconnect SDK sets ``exchange_timestamp`` to a
                    # naive datetime from ``fromtimestamp(epoch_s)`` —
                    # round-trip via ``.timestamp()`` to ns.
                    # Parse failures collapse to None so the tick
                    # loop never crashes.
                    exch_ts_ns: int | None = None
                    exch_raw = raw.get("exchange_timestamp")
                    if exch_raw is not None:
                        try:
                            exch_ts_ns = int(
                                exch_raw.timestamp() * 1_000_000_000,
                            )
                        except (
                            AttributeError, TypeError, ValueError,
                        ):
                            exch_ts_ns = None
                    tick = Tick(
                        ticker=ticker,
                        ts_ns=now_ns,
                        exchange_ts_ns=exch_ts_ns,
                        ltp=ltp_val,
                        volume=int(
                            raw.get("last_traded_quantity", 0) or 0,
                        ),
                    )
                    # 5.3: stage the LTP update for the batch write.
                    ltp_batch[f"cache:ltp:{ticker}"] = str(ltp_val)
                    self._last_tick_ns[tok] = now_ns
                    subs = self._token_subs.get(tok, set())
                    for sid in subs:
                        q = self._queues.get(sid)
                        if q is None:
                            continue
                        # on_ticks runs on the KiteTicker WS thread,
                        # but asyncio.Queue is NOT thread-safe — every
                        # queue op MUST happen on the loop thread. Do
                        # the full-check, drop-oldest, and put
                        # atomically in one scheduled callback so a
                        # stalled drain yields a graceful backpressure
                        # drop, never an uncaught QueueFull traceback.
                        loop.call_soon_threadsafe(
                            self._enqueue_tick, q, tick, sid, tok,
                        )
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "on_ticks: error processing packet %r — "
                        "skipping (user=%s)",
                        raw, self._user_id,
                        exc_info=True,
                    )
            # 5.3: flush all staged LTP values in one pipeline call.
            # Best-effort — skip when Redis is unavailable (pipe=None).
            # 60s TTL uses raw Redis ``ex=`` kwarg, NOT CacheService
            # ``ttl=``, because pipeline() returns the raw redis-py
            # Pipeline object.
            if _ltp_cache is not None and ltp_batch:
                try:
                    pipe = _ltp_cache.pipeline()
                    if pipe is not None:
                        for key, val in ltp_batch.items():
                            pipe.set(key, val, ex=60)
                        pipe.execute()
                except Exception:  # noqa: BLE001
                    pass

        def on_connect(ws, _resp):
            if ws is not self._kt:
                return  # stale callback from an already-replaced kt
            self._connected = True
            self._rebuild_attempts = 0
            self._wedge_escalated = False
            if self._connect_timeout_task is not None:
                self._connect_timeout_task.cancel()
                self._connect_timeout_task = None
            self._connect_ts = time.monotonic()
            # Reset backoff only if the PREVIOUS connection was stable
            # (≥ 30s). A brief connect-then-drop must keep the growing
            # backoff so we don't hammer Kite with rapid reconnects.
            # _connect_ts is set here and checked in on_close.
            _logger.info(
                "KiteWsMultiplexer: connected user=%s",
                self._user_id,
            )
            # Re-subscribe all known tokens — strategy
            # subscriptions PLUS the universe subscription so the
            # global LTP cache rebuilds after a reconnect.
            all_tokens = sorted(
                set(self._token_subs.keys())
                | self._universe_tokens
            )
            if all_tokens:
                ws.subscribe(all_tokens)
                ws.set_mode(ws.MODE_LTP, all_tokens)
            # Emit ws_connected event.
            loop.call_soon_threadsafe(
                self._emit_ws_event,
                "ws_connected",
                {
                    "token_count": len(all_tokens),
                    "universe_count": len(self._universe_tokens),
                },
            )
            # Kick off gap-fill in the event loop.
            loop.call_soon_threadsafe(
                self._schedule_gap_fill_sync,
            )

        def on_close(ws, code, reason):
            if ws is not self._kt:
                return  # stale callback from an already-replaced kt
            self._connected = False
            uptime_s = time.monotonic() - self._connect_ts
            # Only reset backoff when the connection was genuinely
            # stable (≥ 30s). Brief flaps must keep the growing
            # backoff to avoid hammering Kite with rapid reconnects.
            if uptime_s >= 30.0:
                self._backoff_s = _MIN_BACKOFF_S
            reason_str = str(reason)
            _logger.warning(
                "KiteWsMultiplexer: disconnected user=%s "
                "code=%s uptime=%.1fs reason=%s",
                self._user_id, code, uptime_s, reason_str,
            )
            loop.call_soon_threadsafe(
                self._emit_ws_event,
                "ws_disconnected",
                {"code": code, "reason": reason_str},
            )
            # A 403 on the WS upgrade means the access token is
            # expired/invalid — non-retryable. Halt reconnects and
            # tear down so we stop hammering Kite; a fresh Zerodha
            # login spawns a new multiplexer.
            if "403" in reason_str or "Forbidden" in reason_str:
                self._auth_failed = True
                _logger.error(
                    "KiteWsMultiplexer: non-retryable auth failure "
                    "(403) user=%s — halting reconnect; reconnect "
                    "Zerodha to resume",
                    self._user_id,
                )
                loop.call_soon_threadsafe(
                    self._emit_ws_event,
                    "ws_auth_failed",
                    {"code": code, "reason": reason_str},
                )
                loop.call_soon_threadsafe(self._disconnect_kt)
                return
            if not self._closed:
                loop.call_soon_threadsafe(
                    self._trigger_reconnect,
                )

        def on_error(ws, code, reason):
            if ws is not self._kt:
                return  # stale callback from an already-replaced kt
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
        """Schedule reconnect from the event loop thread.

        ASETPLTFRM-470 follow-up: ``on_close`` schedules this via
        ``call_soon_threadsafe``, so it can run one loop-tick AFTER
        it was queued. When the close being reported was a
        *deliberate* teardown of a wedged ticker inside
        ``_handle_wedge()``'s fast-rebuild (the FIRST — and, for a
        wedge, only — time ``.close()`` ever runs on that ticker,
        since it never got as far as a real on_close), the
        immediately-following rebuild can already have connected
        successfully by the time this callback actually runs. The
        on_connect/on_close/on_error identity guards above catch a
        genuinely-stale callback (fired for a ticker generation that
        no longer matches ``self._kt``), but this queued call has
        no ticker reference at all to check — so guard on current
        state instead: never schedule a reconnect while already
        connected, or a single successful wedge auto-recovery would
        immediately tear itself back down into a self-sustaining
        reconnect storm, invisible to rebuild_attempts/
        wedge_escalated (both already reset by the fast on_connect).
        """
        if self._closed or self._auth_failed or self._connected:
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
        while not self._closed and not self._auth_failed:
            backoff = self._backoff_s
            _logger.info(
                "KiteWsMultiplexer: reconnecting in %.1fs user=%s",
                backoff, self._user_id,
            )
            await asyncio.sleep(backoff)
            if self._closed:
                return
            if self._connected:
                _logger.info(
                    "KiteWsMultiplexer: skipping scheduled "
                    "reconnect — already connected (user=%s)",
                    self._user_id,
                )
                return
            self._backoff_s = min(
                self._backoff_s * 2, _MAX_BACKOFF_S,
            )
            self._disconnect_kt()
            try:
                self._kt = self._build_ticker()
                self._kt.connect(threaded=True)
                self._arm_connect_timeout()
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
        """Called via call_soon_threadsafe — single-flight gap-fill.

        Cancels any prior in-flight task before scheduling a new one
        so reconnect flaps do not pile up concurrent historical fetches
        that hammer the Kite historical API (5.2).
        """
        if self._loop is None:
            return
        # Cancel the prior task if still running.
        if self._gap_fill_task is not None and not (
            self._gap_fill_task.done()
        ):
            self._gap_fill_task.cancel()
        self._gap_fill_task = self._loop.create_task(
            self._run_gap_fill(),
        )

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
                    # Route through _enqueue_tick so backpressure
                    # accounting is consistent with live ticks (5.2).
                    self._enqueue_tick(q, tick, sid, tok)

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
        """Persist a WS-lifecycle event to the per-user Redis store.

        7-day observability records — NOT written to the algo.events
        Iceberg log (incident 2026-06-18). Best-effort: a Redis failure
        is swallowed inside record_ws_event."""
        from uuid import uuid4

        from backend.algo.broker.ws_event_store import record_ws_event

        ts_ns = int(time.time() * 1_000_000_000)
        record_ws_event(
            user_id=self._user_id,
            event_id=str(uuid4()),
            ts_ns=ts_ns,
            type_=type_,
            strategy_id=payload.get("strategy_id"),
            payload=payload,
        )

    def _enqueue_tick(self, q, tick, sid, tok) -> None:
        """Push a tick onto a subscriber queue. Runs on the loop
        thread (scheduled via call_soon_threadsafe) so all queue ops
        are thread-safe.

        On a full queue: drop the oldest item to make room and retry;
        if still full, drop the incoming tick. Either path records a
        backpressure event rather than raising QueueFull.
        """
        try:
            q.put_nowait(tick)
            return
        except asyncio.QueueFull:
            pass
        try:
            q.get_nowait()  # drop oldest
        except asyncio.QueueEmpty:
            pass
        try:
            q.put_nowait(tick)
        except asyncio.QueueFull:
            pass  # drop newest as a last resort
        # No per-drop log/event here — aggregated below to avoid the
        # ~50/s flood that bloated algo.events.
        self._record_backpressure_event(strategy_id=sid, token=tok)

    def _record_backpressure_event(
        self, strategy_id: UUID, token: int,
    ) -> None:
        """Count a backpressure drop; emit a summary event at most
        once per ``_BP_AGG_WINDOW_S`` per strategy. The first drop in a
        window emits immediately (so onset is visible); subsequent
        drops are counted and surface on the next window / on close.
        ``token`` is accepted for call-site compatibility but no longer
        carried per-drop (aggregation is per strategy)."""
        now_ns = int(time.time() * 1_000_000_000)
        self._bp_drops[strategy_id] = (
            self._bp_drops.get(strategy_id, 0) + 1
        )
        last = self._bp_last_emit_ns.get(strategy_id, 0)
        if now_ns - last >= _BP_AGG_WINDOW_NS:
            self._emit_backpressure_summary(strategy_id, now_ns)

    def _emit_backpressure_summary(
        self, strategy_id: UUID, now_ns: int,
    ) -> None:
        """Flush the accumulated drop count for ``strategy_id`` as one
        ``ws_backpressure_drop`` summary event + a single WARNING."""
        count = self._bp_drops.pop(strategy_id, 0)
        if count <= 0:
            return
        self._bp_last_emit_ns[strategy_id] = now_ns
        self._emit_ws_event(
            "ws_backpressure_drop",
            {
                "strategy_id": str(strategy_id),
                "dropped": count,
                "window_s": _BP_AGG_WINDOW_S,
            },
        )
        _logger.warning(
            "ws_backpressure_drop user=%s strategy=%s dropped=%d "
            "(aggregated over ~%ds)",
            self._user_id, strategy_id, count, _BP_AGG_WINDOW_S,
        )

    def _flush_backpressure_residual(self) -> None:
        """Emit summaries for any un-flushed drop counts (called on
        close so the final partial window is not lost)."""
        now_ns = int(time.time() * 1_000_000_000)
        for sid in list(self._bp_drops.keys()):
            self._emit_backpressure_summary(sid, now_ns)

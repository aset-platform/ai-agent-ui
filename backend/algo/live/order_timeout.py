"""Background asyncio watcher that auto-cancels stale LIMITs.

Part of PR #3 of the Order Safety Hardening epic (spec §3.3).

Lifecycle
---------
One ``_OrderTimeoutWatcher`` instance is created per ``LiveRuntime``
session and started as a background task at the top of ``run()``.
Every ``ALGO_ORDER_TIMEOUT_POLL_S`` seconds the watcher fetches
``kite.orders()`` and, for any order whose ``tag`` starts with
``algo-{strategy_id[:8]}`` (i.e. emitted by this session) AND whose
``status`` is still ``OPEN`` / ``TRIGGER PENDING`` AND whose age
exceeds ``ALGO_ORDER_TTL_S``, calls ``kite.cancel_order(...)``.

On cancel success an ``order_cancelled_timeout`` event is appended to
the live ``self._events`` buffer; on cancel failure the watcher emits
``order_cancel_failed`` and continues — never raises out of the loop.

Partial fills handled naturally: Kite's ``cancel_order`` only kills
the unfilled remainder, and the filled portion is already reflected
in our position state via the normal fill path. The watcher does
not try to reconcile or unwind fills.

Configuration
-------------
``ALGO_ORDER_TTL_S``           default 90 — cancel after N seconds
``ALGO_ORDER_TIMEOUT_POLL_S``  default 15 — poll cadence

Both env vars are read lazily (per call to ``_read_*_s``) so tests
can monkeypatch + ops can tune without a restart. Matches the
``_read_max_ltp_age_s`` pattern in ``kite_client.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from backend.algo.backtest.event_writer import event_row

_logger = logging.getLogger(__name__)

UTC = timezone.utc
# Container runs TZ=Asia/Kolkata (CLAUDE.md §4.5 #32); Kite returns
# ``order_timestamp`` as a naive IST local timestamp like
# ``"2026-05-12 09:18:42"``. We parse using this offset explicitly so
# the watcher behaves the same on any host TZ.
_IST = timezone(timedelta(hours=5, minutes=30))

_DEFAULT_TTL_S = 90
_DEFAULT_POLL_S = 15

_OPEN_STATES: frozenset[str] = frozenset({"OPEN", "TRIGGER PENDING"})


def _read_int_env(var: str, default: int) -> int:
    raw = os.environ.get(var, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not an int — using default %d",
            var, raw, default,
        )
        return default


def _read_ttl_s() -> int:
    """Resolve ``ALGO_ORDER_TTL_S`` (default 90)."""
    return _read_int_env("ALGO_ORDER_TTL_S", _DEFAULT_TTL_S)


def _read_poll_s() -> int:
    """Resolve ``ALGO_ORDER_TIMEOUT_POLL_S`` (default 15)."""
    return _read_int_env(
        "ALGO_ORDER_TIMEOUT_POLL_S", _DEFAULT_POLL_S,
    )


def _parse_order_timestamp(raw: Any) -> datetime | None:
    """Parse a Kite ``order_timestamp`` field robustly.

    Kite returns either:
      * a naive ``datetime`` (when going through pykiteconnect's
        ``orders()`` parser, which datetime-coerces select fields), OR
      * a string ``"2026-05-12 09:18:42"`` (space-separated, no tz).
    Both are IST-local. ISO-8601 with explicit offset is also
    accepted defensively for tests / fixture diversity. Returns a
    tz-aware UTC datetime, or ``None`` if unparseable.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        # Treat naive as IST-local (Kite's actual behaviour).
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=_IST)
        return raw.astimezone(UTC)
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    # Try Kite's space-separated naive format first.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(s, fmt)
            return naive.replace(tzinfo=_IST).astimezone(UTC)
        except ValueError:
            pass
    # Fall back to ISO-8601 with offset.
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_IST)
        return dt.astimezone(UTC)
    except ValueError:
        _logger.warning(
            "order_timeout: cannot parse order_timestamp=%r — "
            "skipping order", s,
        )
        return None


class _OrderTimeoutWatcher:
    """Polls ``kite.orders()`` and cancels session-tagged stale LIMITs.

    Args:
        kite_client: ``KiteClient`` instance shared with the runtime.
        session_id: Live session UUID — stamped onto emitted events.
        strategy_id: Strategy UUID — used both for the tag prefix
            filter (``algo-{first-8-chars}``) AND for the
            ``strategy_id`` column on emitted events.
        user_id: User UUID — stamped onto emitted events.
        events_sink: Callable that accepts a single event row dict
            (typically ``self._events.append`` on the runtime).
        ttl_seconds: Cancel any open order older than this. Defaults
            to ``ALGO_ORDER_TTL_S`` (env, fallback 90).
        poll_seconds: Sleep interval between polls. Defaults to
            ``ALGO_ORDER_TIMEOUT_POLL_S`` (env, fallback 15).
    """

    def __init__(
        self,
        *,
        kite_client: Any,
        session_id: UUID,
        strategy_id: UUID,
        user_id: UUID,
        events_sink: Callable[[dict], None],
        ttl_seconds: int | None = None,
        poll_seconds: int | None = None,
    ) -> None:
        self._kite = kite_client
        self._session_id = session_id
        self._strategy_id = strategy_id
        self._user_id = user_id
        self._sink = events_sink
        self._ttl_seconds = (
            ttl_seconds if ttl_seconds is not None
            else _read_ttl_s()
        )
        self._poll_seconds = (
            poll_seconds if poll_seconds is not None
            else _read_poll_s()
        )
        self._stopping: bool = False
        self._tag_prefix = f"algo-{str(strategy_id)[:8]}"

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------

    def request_stop(self) -> None:
        """Signal the run loop to exit on its next iteration."""
        self._stopping = True

    async def run(self) -> None:
        """Main loop. Returns when ``request_stop()`` is called.

        Failures inside ``_tick_once`` are caught + logged so the
        watcher never dies silently — the runtime can keep running
        even if Kite ``orders()`` is briefly unhappy.
        """
        _logger.info(
            "order_timeout watcher started: strat=%s ttl=%ds "
            "poll=%ds tag_prefix=%s",
            self._strategy_id, self._ttl_seconds,
            self._poll_seconds, self._tag_prefix,
        )
        try:
            while not self._stopping:
                try:
                    await self._tick_once()
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "order_timeout: tick failed: %s — "
                        "continuing", exc, exc_info=True,
                    )
                if self._stopping:
                    break
                # Use asyncio.sleep so request_stop + cancel can
                # interrupt the wait cleanly.
                await asyncio.sleep(self._poll_seconds)
        finally:
            _logger.info(
                "order_timeout watcher stopped: strat=%s",
                self._strategy_id,
            )

    # ----------------------------------------------------------------
    # Polling
    # ----------------------------------------------------------------

    async def _tick_once(self) -> None:
        """Single poll → cancel any matching aged orders."""
        orders = await asyncio.to_thread(self._fetch_orders)
        if not orders:
            return
        now = datetime.now(UTC)
        for order in orders:
            try:
                self._maybe_cancel(order, now)
            except Exception as exc:  # noqa: BLE001
                # Defensive: a single malformed row must not poison
                # the whole tick.
                _logger.warning(
                    "order_timeout: skip malformed order=%r "
                    "exc=%s", order, exc,
                )

    def _fetch_orders(self) -> list[dict]:
        """Synchronous SDK call; runs on the to_thread worker."""
        try:
            kc = getattr(self._kite, "_kc", None)
            if kc is None:
                # Test fixtures may patch ``orders`` directly on the
                # client; fall through to that surface.
                fn = getattr(self._kite, "orders", None)
                if callable(fn):
                    return list(fn() or [])
                return []
            return list(kc.orders() or [])
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "order_timeout: kite.orders() failed: %s", exc,
            )
            return []

    def _maybe_cancel(
        self, order: dict, now: datetime,
    ) -> None:
        tag = order.get("tag") or ""
        if not tag.startswith(self._tag_prefix):
            return
        status = order.get("status") or ""
        if status not in _OPEN_STATES:
            return
        ts = _parse_order_timestamp(order.get("order_timestamp"))
        if ts is None:
            return
        age = (now - ts).total_seconds()
        if age <= self._ttl_seconds:
            return
        order_id = order.get("order_id")
        if not order_id:
            return
        # Inline the cancel — _tick_once is itself awaited from
        # the loop, so blocking the loop on a synchronous SDK call
        # would stall the watcher. Wrap in to_thread.
        self._do_cancel_sync(order, age, status)

    def _do_cancel_sync(
        self, order: dict, age: float, status: str,
    ) -> None:
        """Issue the cancel and emit the corresponding event."""
        order_id = order["order_id"]
        try:
            # KiteClient.cancel_order signature is
            # ``cancel_order(order_id, variety="regular")``.
            self._kite.cancel_order(
                order_id=order_id, variety="regular",
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "order_timeout: cancel failed kite_order_id=%s "
                "status=%s age=%.1fs exc=%s",
                order_id, status, age, exc,
            )
            self._emit("order_cancel_failed", {
                "kite_order_id": order_id,
                "tag": order.get("tag"),
                "status_at_cancel_attempt": status,
                "age_seconds": age,
                "ttl_seconds": self._ttl_seconds,
                "symbol": order.get("tradingsymbol"),
                "exc_str": str(exc),
            })
            return
        _logger.info(
            "order_timeout: cancelled stale order "
            "kite_order_id=%s status=%s age=%.1fs ttl=%ds tag=%s",
            order_id, status, age, self._ttl_seconds,
            order.get("tag"),
        )
        self._emit("order_cancelled_timeout", {
            "kite_order_id": order_id,
            "tag": order.get("tag"),
            "status_at_cancel": status,
            "age_seconds": age,
            "ttl_seconds": self._ttl_seconds,
            "symbol": order.get("tradingsymbol"),
            "side": order.get("transaction_type"),
            "qty": order.get("quantity"),
            "filled_qty": order.get("filled_quantity"),
            "reason": "ttl_exceeded",
        })

    # ----------------------------------------------------------------
    # Event emission
    # ----------------------------------------------------------------

    def _emit(self, type_: str, payload: dict[str, Any]) -> None:
        # Ensure JSON-serialisable values (mirrors event_row's
        # default=str behaviour but caught early so a stray Decimal
        # doesn't blow up at flush time).
        try:
            json.dumps(payload, default=str)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "order_timeout: payload not JSON-serialisable "
                "type=%s payload=%r — coercing via str", type_,
                payload,
            )
            payload = {k: str(v) for k, v in payload.items()}
        try:
            self._sink(event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy_id,
                mode="live",
                type_=type_,
                payload=payload,
            ))
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "order_timeout: events_sink rejected event "
                "type=%s exc=%s", type_, exc,
            )

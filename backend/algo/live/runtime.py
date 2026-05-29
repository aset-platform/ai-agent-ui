"""LiveRuntime — real-money order-placement engine (V2-5).

Architecture mirrors PaperRuntime but replaces PaperBroker with
KiteAdapter calls and gates every signal through the full 9-cap
``pre_trade_check`` (binding = True).

DEFAULT-OFF contract
--------------------
This class MUST NOT be instantiatable without a valid ``caps`` dict
that has ``live_orders_enabled=True``.  The constructor raises
``LiveNotEnabledError`` if the guard fails.  That check is the last
line of defence before real money changes hands.

In-flight tracking
------------------
Submitted-but-not-yet-filled orders are persisted to
``algo.runs.live_orders_in_flight`` so the kill-switch handler can
cancel them.  Each entry is::

    {
        "kite_order_id": str,
        "internal_order_id": str,
        "symbol": str,
        "side": str,
        "qty": int,
        "submitted_at": ISO-8601 str,
        "status": "submitted" | "cancelled" | "filled"
    }
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from backend.algo.attribution.payload import (
    attribution_payload_extension as _attribution_payload_extension,
)
from backend.algo.backtest.cooldown_hydration import (
    _HydratedClose,
    load_recent_failed_exits,
)
from backend.algo.backtest.cooldown_monitor import in_cooldown
from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.stop_loss_monitor import (
    check_stop_loss_triggers,
)
from backend.algo.backtest.time_stop_monitor import (
    check_time_stop_triggers,
)
from backend.algo.broker.kite_client import KiteClient

# REGIME-2a — pre-computed nightly factor library overlay.
from backend.algo.factors.repo import get_factors_window

# FE-15b — shared per-bar feature assembly (consistent across
# backtest/paper/live/dry-run runtimes).
from backend.algo.features.per_bar import (
    assemble_per_bar_features,
    lookup_daily_overlay,
)
from backend.algo.live import slippage as _slippage
from backend.algo.live.order_timeout import _OrderTimeoutWatcher
from backend.algo.live.budget import (
    reserve as budget_reserve,
)
from backend.algo.live.budget import (
    transition as budget_transition,
)
from backend.algo.live.budget_types import ReservationState
from backend.algo.live.safety import (
    LiveRejectReason,
    pre_trade_check,
)
from backend.algo.paper.types import AccountState, Signal

# REGIME-4 — vol-target / Kelly sizer (legacy modes bypass).
from backend.algo.sizing.composer import SizingContext, compose_qty
from backend.algo.strategy.ast import Strategy
from backend.algo.stream.resampler import Resampler
from backend.algo.stream.sources import TickSource

_logger = logging.getLogger(__name__)

UTC = timezone.utc
# ISO strings emitted into algo.events payloads are user-facing
# (Submissions panel raw-JSON viewer). Stamp with +05:30 per
# feedback_ist_dates_user_facing — internal datetimes still UTC.
IST = timezone(timedelta(hours=5, minutes=30))


def _parse_ist_time(s: str) -> time:
    """Parse ``HH:MM`` 24-hour string → ``time`` object. Tolerant
    of leading/trailing whitespace; falls back to ``14:30`` on any
    parse failure with a warning so a malformed env var doesn't
    crash the runtime constructor."""
    try:
        hh, mm = s.strip().split(":")
        return time(int(hh), int(mm))
    except Exception:  # noqa: BLE001
        _logger.warning(
            "Invalid ALGO_DAILY_MIN_EVAL_TIME_IST=%r — falling "
            "back to 14:30",
            s,
        )
        return time(14, 30)


# ASETPLTFRM-383 — IST cutoff before which per-minute bar closes
# update today's running daily bar but do NOT fire strategy eval.
# Suppresses noisy fires while today's "close" is still volatile.
# Set via env; lower (e.g. ``09:30``) during smoke testing to see
# evals fire from market open.
_MIN_EVAL_TIME_IST = _parse_ist_time(
    os.environ.get("ALGO_DAILY_MIN_EVAL_TIME_IST", "14:30"),
)


def _select_last_price_ts_ns(tick: Any) -> int:
    """Return the best available ns-since-epoch stamp for ``tick``.

    ASETPLTFRM-372 — prefer the exchange-emission timestamp when
    Kite supplied it (full/quote-mode packets); fall back to the
    local arrival stamp otherwise. The exchange stamp catches
    "exchange feed froze but our WS is healthy" (Yahoo ^BSESN-
    style mid-session freeze) which a local-arrival stamp cannot
    detect.
    """
    return tick.exchange_ts_ns or tick.ts_ns


class LiveNotEnabledError(RuntimeError):
    """Raised when live trading is not enabled for (user, strategy)."""


class LiveRuntime:
    """Tick-driven live strategy executor.

    Parameters
    ----------
    strategy, user_id, initial_capital_inr, fee_as_of:
        Same semantics as PaperRuntime.
    kite:
        Authenticated KiteClient with a valid access_token.
    caps:
        Row from ``algo.live_caps``.  MUST have
        ``live_orders_enabled=True`` or ``LiveNotEnabledError``
        is raised.
    run_id:
        UUID of the ``algo.runs`` row (needed for in-flight tracking).
    caps_repo:
        ``CapsRepo`` instance for in-flight updates.
    kill_switch_repo:
        ``KillSwitchRepo`` instance for kill-check reads.
    redis_client:
        Optional async Redis — used by KillSwitchRepo for sub-ms
        kill checks.
    """

    def __init__(
        self,
        *,
        strategy: Strategy,
        user_id: UUID,
        initial_capital_inr: Decimal,
        fee_as_of: Any,
        kite: KiteClient,
        caps: dict[str, Any] | None,
        run_id: UUID,
        caps_repo: Any,
        kill_switch_repo: Any,
        ticker_to_token: dict[str, int] | None = None,
    ) -> None:
        dry_run = bool(getattr(kite, "dry_run", False))
        caps = caps or {}
        # Dry-run is the rehearsal step that runs BEFORE live-mode
        # caps are enabled, so the live-enabled gate only applies to
        # real-money (non-dry-run) runtimes. A missing live_caps row
        # (caps=None) is normal for a strategy never promoted to live.
        if not dry_run and not caps.get("live_orders_enabled"):
            raise LiveNotEnabledError(
                f"Live trading is disabled for "
                f"user={user_id} strategy={strategy.id}. "
                f"Enable via the frontend live-mode toggle.",
            )
        self._strategy = strategy
        self._user_id = user_id
        self._initial = initial_capital_inr
        self._kite = kite
        # Stamped on EVERY event payload below so the frontend can
        # filter the events timeline into paper / dry-run / live
        # segments without joining back to algo.runs.
        self._dry_run: bool = dry_run
        # Set True in run() when the tick source is a replay fixture.
        # Gates the daily eval-time cutoff (wall-clock is meaningless
        # for replayed historical bars).
        self._is_replay: bool = False
        self._caps = caps
        self._run_id = run_id
        self._caps_repo = caps_repo
        self._kill_switch_repo = kill_switch_repo
        self._ticker_to_token = ticker_to_token or {}
        self._evaluator = Evaluator()
        self._resampler = Resampler(intervals=(60,))
        self._positions = PositionTracker()
        self._session_id = uuid4()
        self._events: list[dict[str, Any]] = []
        self._in_flight: list[dict[str, Any]] = []
        self._bars_by_ticker: dict[str, list] = {}

        # ASETPLTFRM-376 — hydrate PositionTracker from any pre-
        # existing Kite positions/holdings so EXIT logic can see
        # yesterday's overnight CNC + today's already-open MIS
        # legs. Wrapped: a Kite hiccup must NOT fail runtime
        # construction; we degrade to the empty tracker and log.
        try:
            from backend.algo.live.position_hydration import (
                apply_hydrated_positions,
                hydrate,
                hydration_events,
            )

            allowed = caps.get("allowed_tickers") or None
            hydrated = hydrate(
                kite=kite,
                strategy=strategy,
                user_id=user_id,
                allowed_tickers=allowed,
            )
            if hydrated:
                apply_hydrated_positions(self._positions, hydrated)
                self._events.extend(
                    hydration_events(
                        session_id=self._session_id,
                        user_id=user_id,
                        strategy_id=strategy.id,
                        hydrated=hydrated,
                        dry_run=self._dry_run,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "LiveRuntime: position hydration failed: %s — "
                "PositionTracker starts empty (EXIT signals on "
                "pre-existing positions will no-op)",
                exc,
            )

        # Pre-load NIFTY regime + trend features so strategies
        # gated on ``nifty_above_sma200`` / ``nifty_30d_return_pct``
        # don't silent-fail every bar with a KeyError. Same
        # pattern as PaperRuntime + the backtest runner.
        self._market_regime: dict[Any, Decimal] = {}
        self._market_trend: dict[Any, Decimal] = {}
        try:
            from datetime import date as _date
            from datetime import timedelta

            from backend.algo.backtest.indicators import (
                compute_market_regime as _cmr,
            )
            from backend.algo.backtest.indicators import (
                compute_market_trend_strength as _cmts,
            )

            # Match ``load_ohlcv_window``'s UTC clock — local IST
            # racing past midnight UTC would trip
            # BackedFutureBarError.
            today = datetime.now(timezone.utc).date()
            window_start = today - timedelta(days=365 * 3)
            self._market_regime = _cmr(window_start, today)
            self._market_trend = _cmts(window_start, today)
            _logger.info(
                "LiveRuntime: regime cache loaded — %d regime "
                "days, %d trend days",
                len(self._market_regime),
                len(self._market_trend),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "LiveRuntime: regime cache load failed: %s",
                exc,
            )

        # ASETPLTFRM-436 — hydrate the cooldown gate from
        # algo.events at session start. Live runtime restarts wipe
        # in-memory state; the durable source for "ticker T had a
        # failed exit at date D" is the order_filled_live event
        # payload reason field. Same pure in_cooldown function as
        # backtest, different data origin.
        self._cooldown_history: list = []
        cd_days = getattr(
            getattr(strategy.risk, "per_trade", None),
            "cooldown_after_failed_exit_days",
            None,
        )
        if cd_days:
            try:
                self._cooldown_history = load_recent_failed_exits(
                    user_id=user_id,
                    strategy_id=strategy.id,
                    cooldown_days=cd_days,
                    as_of=datetime.now(timezone.utc).date(),
                    runtime_mode="live",
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "LiveRuntime: cooldown hydration failed "
                    "(%s) — gate starts empty",
                    exc,
                )

        # REGIME-2a — per-ticker cached factor row, lazy-loaded
        # on first sight of a ticker (same rationale as
        # PaperRuntime: ``strategy.universe`` is a scope spec,
        # not a ticker list).
        self._factor_cache: dict[tuple[str, date], dict[str, Decimal]] = {}
        self._factor_loaded_for_ticker: set[str] = set()
        # REGIME-1 — regime_label + stress_prob lookup, loaded
        # lazily on first bar so live sessions resolve regime
        # features identically to backtest + paper.
        self._regime_by_date: dict[date, dict[str, Any]] = {}
        self._regime_loaded: bool = False
        # FE-15b — daily-overlay panel (interval_sec=86400) for
        # cross-cadence AST references in intraday strategies.
        # Loaded lazily per ticker on first sight, same pattern
        # as the factor cache. Skipped entirely for daily
        # strategies (primary cadence is already 86400).
        self._daily_overlay_cache: dict[
            tuple[str, date], dict[str, Decimal | str]
        ] = {}
        self._daily_overlay_loaded_for_ticker: set[str] = set()

        # PR #2 (order-safety) — per-ticker liquidity bucket loaded
        # once at session start from the latest universe_snapshot
        # rebalance. Tickers absent from the snapshot fall through
        # to ``None`` → ``slippage.bps_for(None)`` returns 30 bps,
        # preserving today's behaviour. Missing snapshot column or
        # query failure is non-fatal — every ticker just defaults.
        self._bucket_by_ticker: dict[str, str] = self._load_bucket_by_ticker()

        # ASETPLTFRM-383 — preload 250 closed daily bars per allowed
        # ticker from stocks.ohlcv (Iceberg) so the very first
        # per-minute eval sees the same indicator landscape as the
        # backtest. Today's running bar is appended lazily on the
        # first ``_on_bar_close`` for each ticker via
        # ``initial_running_bar``. Fail-soft: any error degrades to
        # the pre-383 empty-history behaviour (strategy silent-skips
        # until indicators settle).
        #
        # ASETPLTFRM-393 — for intraday cadences (15m / 5m / 1m) we
        # route through ``preload_intraday_bars`` instead, reading
        # from ``algo.intraday_bars`` and falling back to Kite. The
        # daily path is preserved bit-for-bit for ``interval="1d"``.
        allowed_for_preload = caps.get("allowed_tickers") or []
        interval = strategy.schedule.interval
        if allowed_for_preload and interval == "1d":
            try:
                from backend.algo.live.daily_bar_warmup import (
                    preload_daily_bars,
                )

                preloaded = preload_daily_bars(
                    list(allowed_for_preload),
                    kite_client=kite,
                    ticker_to_token=self._ticker_to_token or None,
                )
                self._bars_by_ticker.update(preloaded)
                _logger.info(
                    "LiveRuntime: daily-bar warmup loaded — "
                    "%d ticker(s), eval_gate=%s IST",
                    len(preloaded),
                    _MIN_EVAL_TIME_IST.strftime("%H:%M"),
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "LiveRuntime: daily-bar warmup failed: %s — "
                    "strategies will silent-skip until indicators "
                    "settle on session-local minute history",
                    exc,
                )
        elif allowed_for_preload and interval != "1d":
            try:
                from backend.algo.live.intraday_bar_warmup import (
                    INTERVAL_SEC_BY_LABEL,
                    preload_intraday_bars,
                )

                interval_sec = INTERVAL_SEC_BY_LABEL[interval]
                preloaded = preload_intraday_bars(
                    list(allowed_for_preload),
                    interval_sec=interval_sec,
                    kite_client=kite,
                    ticker_to_token=self._ticker_to_token or None,
                )
                self._bars_by_ticker.update(preloaded)
                _logger.info(
                    "LiveRuntime: intraday-bar warmup loaded — "
                    "%d ticker(s), interval=%s",
                    len(preloaded),
                    interval,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "LiveRuntime: intraday-bar warmup failed: %s "
                    "— strategies will silent-skip until indicators"
                    " settle on session-local bars",
                    exc,
                    exc_info=True,
                )

        # PR #3 (order-safety) — background asyncio watcher that
        # cancels any session-tagged LIMIT older than
        # ALGO_ORDER_TTL_S (default 90s) still in OPEN /
        # TRIGGER PENDING. Started inside ``run()`` (we need a
        # running event loop) and stopped in the same method's
        # ``finally:`` block.
        self._timeout_watcher: _OrderTimeoutWatcher | None = None
        self._timeout_watcher_task: asyncio.Task | None = None

        # ASETPLTFRM-394 — MIS auto-square-off background task.
        # Scheduled inside ``run()`` (needs a running loop) for any
        # strategy where product == "MIS". Sleeps until
        # ``square_off_time`` (default "15:14 IST") IST today, then
        # emits a synthetic SELL signal per open position through
        # the normal ``_submit_order`` path. Cancelled in the
        # ``finally:`` block when the runtime stops.
        # Daily / CNC strategies leave this as None.
        self._square_off_task: asyncio.Task | None = None

    def _load_bucket_by_ticker(self) -> dict[str, str]:
        """Read latest ``stocks.universe_snapshot`` and build a
        ticker → liquidity_bucket dict for this session.

        Best-effort: any failure (missing column, empty table,
        DuckDB hiccup) logs a warning and returns an empty dict.
        The runtime then falls back to the unknown bucket for
        every ticker, matching pre-PR #2 behaviour.
        """
        try:
            from backend.db.duckdb_engine import query_iceberg_table

            rows = query_iceberg_table(
                "stocks.universe_snapshot",
                "SELECT ticker, liquidity_bucket, "
                "       MAX(rebalance_date) AS rd "
                "FROM universe_snapshot "
                "WHERE liquidity_bucket IS NOT NULL "
                "GROUP BY ticker, liquidity_bucket",
                [],
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "LiveRuntime: bucket cache load failed: %s — "
                "every ticker falls back to unknown (30 bps)",
                exc,
            )
            return {}
        # Pick the most-recent rebalance per ticker. The GROUP BY
        # above lets a ticker appear in multiple rebalances; we
        # keep the latest.
        latest: dict[str, tuple[Any, str]] = {}
        for r in rows:
            t = r.get("ticker")
            b = r.get("liquidity_bucket")
            rd = r.get("rd")
            if not t or not b:
                continue
            prev = latest.get(t)
            if prev is None or rd > prev[0]:
                latest[t] = (rd, b)
        out = {t: b for t, (_rd, b) in latest.items()}
        _logger.info(
            "LiveRuntime: bucket cache loaded — %d tickers",
            len(out),
        )
        return out

    def _flush_events_now(self) -> None:
        """Flush buffered events to algo.events immediately so
        the events panel sees signals + orders in real time.
        Without this the buffer only flushes at session end and
        the user-facing panel looks frozen during long live-ws
        sessions. Cheap (single Iceberg commit per call); the
        runtime emits ~1-10 events/minute typically."""
        if not self._events:
            return
        try:
            flush_events(self._events)
            self._events = []
        except Exception:  # noqa: BLE001
            _logger.warning(
                "in-session flush failed — events will land at " "session end",
                exc_info=True,
            )

    def _ensure_regime_cache(self, bar_date_obj: date) -> None:
        if self._regime_loaded:
            return
        self._regime_loaded = True
        try:
            from datetime import timedelta as _td

            from backend.algo.regime.repo import get_regime_history

            rh_rows = get_regime_history(
                bar_date_obj - _td(days=365),
                bar_date_obj + _td(days=1),
            )
            for rh in rh_rows:
                entry: dict[str, Any] = {
                    "regime_label": rh.regime_label,
                }
                if rh.stress_prob is not None:
                    entry["stress_prob"] = Decimal(
                        str(rh.stress_prob),
                    )
                self._regime_by_date[rh.bar_date] = entry
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "LiveRuntime: regime_history load failed: %s — "
                "regime-aware templates will silent-skip",
                exc,
            )

    def _ensure_factor_cache(
        self,
        ticker: str,
        bar_date_obj: date,
    ) -> None:
        """Lazy load factor rows for ``ticker``. Called once per
        ticker."""
        if ticker in self._factor_loaded_for_ticker:
            return
        self._factor_loaded_for_ticker.add(ticker)
        try:
            from datetime import timedelta as _td

            rows = get_factors_window(
                [ticker],
                bar_date_obj - _td(days=365),
                bar_date_obj + _td(days=1),
            )
            for r in rows:
                self._factor_cache[(r.ticker, r.bar_date)] = {
                    k: Decimal(str(v))
                    for k, v in r.values.items()
                    if v is not None
                }
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "LiveRuntime: factor cache load for %s failed: "
                "%s — strategies referencing factor.* keys will "
                "silent-skip until backfill catches up",
                ticker,
                exc,
            )

    def _ensure_daily_overlay_cache(
        self,
        ticker: str,
        bar_date_obj: date,
    ) -> None:
        """FE-15b — lazy load daily-cadence features
        (``interval_sec=86400``) for ``ticker`` so the AST can
        reference ``{name}_1d`` keys in intraday strategies.

        Skipped entirely for daily strategies (primary cadence
        is already 86400 — no overlay needed). Same code path
        runs for live AND dry-run (kite.dry_run=True) — signal
        generation is identical on both surfaces.

        Failures are logged + swallowed; strategies that don't
        reference ``_1d`` keys are unaffected.
        """
        if self._strategy.schedule.interval == "1d":
            return
        if ticker in self._daily_overlay_loaded_for_ticker:
            return
        self._daily_overlay_loaded_for_ticker.add(ticker)
        try:
            from datetime import datetime as _dt
            from datetime import timedelta as _td
            from datetime import timezone as _tz

            from backend.algo.features import (
                load_intraday_features_window,
            )

            panel = load_intraday_features_window(
                tickers=[ticker],
                interval_sec=86400,
                period_start=bar_date_obj - _td(days=30),
                period_end=bar_date_obj + _td(days=1),
                enable_on_demand_backfill=False,
            )
            for tk, by_ts in panel.items():
                for ts_ns, feats in by_ts.items():
                    bd = _dt.fromtimestamp(
                        ts_ns / 1_000_000_000, tz=_tz.utc
                    ).date()
                    self._daily_overlay_cache[(tk, bd)] = feats
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "LiveRuntime: daily overlay load for %s failed: "
                "%s — strategies referencing _1d keys will "
                "silent-skip until backfill catches up",
                ticker,
                exc,
            )

    # ----------------------------------------------------------
    # Main run loop
    # ----------------------------------------------------------

    # ----------------------------------------------------------
    # ASETPLTFRM-394 — MIS auto-square-off
    # ----------------------------------------------------------

    @staticmethod
    def _parse_square_off_ist(s: str | None) -> time:
        """Parse a "HH:MM IST" (or bare "HH:MM") string into a
        ``datetime.time``. Falls back to 15:14 IST on any parse
        failure — matches the AST default and is one minute before
        Zerodha's broker-side 15:15 auto-square so our fill lands
        in the ledger first.
        """
        raw = (s or "").strip() or "15:14 IST"
        cleaned = raw.replace("IST", "").strip()
        try:
            hh, mm = cleaned.split(":")
            return time(int(hh), int(mm))
        except Exception:  # noqa: BLE001
            _logger.warning(
                "LiveRuntime: invalid square_off_time=%r — "
                "falling back to 15:14 IST",
                s,
            )
            return time(15, 14)

    async def _schedule_mis_square_off(self) -> None:
        """Sleep until ``square_off_time`` IST today, then emit a
        synthetic SELL signal for every open position via the
        normal ``_submit_order`` path. Caps + slippage + audit all
        apply normally.

        Cancelled by ``run()``'s ``finally:`` block on session stop.
        If the target time is already in the past at scheduling
        (e.g. operator started the runtime at 15:30 IST), the task
        no-ops immediately.

        Daily / CNC strategies must never reach this method —
        ``run()`` only schedules it when ``strategy.product == "MIS"``.
        """
        from backend.algo.paper.types import Signal

        target_t = self._parse_square_off_ist(
            self._strategy.square_off_time,
        )
        now_ist = datetime.now(IST)
        target_ist = now_ist.replace(
            hour=target_t.hour,
            minute=target_t.minute,
            second=0,
            microsecond=0,
        )
        delay_s = (target_ist - now_ist).total_seconds()
        if delay_s <= 0:
            _logger.info(
                "LiveRuntime: square_off_time=%s already past at "
                "runtime start (now_ist=%s) — auto-square no-op",
                target_t.strftime("%H:%M"),
                now_ist.strftime("%H:%M:%S"),
            )
            return

        _logger.info(
            "LiveRuntime: MIS auto-square scheduled in %.1fs "
            "(target=%s IST, strategy=%s)",
            delay_s,
            target_t.strftime("%H:%M"),
            self._strategy.id,
        )
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            _logger.info(
                "LiveRuntime: MIS auto-square task cancelled "
                "before firing (session stopped early)",
            )
            raise

        open_positions = self._positions.open_positions()
        if not open_positions:
            _logger.info(
                "LiveRuntime: MIS auto-square fired but no open "
                "positions to close — no-op",
            )
            return

        _logger.warning(
            "LiveRuntime: MIS auto-square firing for %d open "
            "position(s) at %s IST",
            len(open_positions),
            datetime.now(IST).strftime("%H:%M:%S"),
        )
        for ticker, pos in list(open_positions.items()):
            if pos.qty <= 0:
                continue
            signal = Signal(
                strategy_id=self._strategy.id,
                user_id=self._user_id,
                ticker=ticker,
                side="SELL",
                qty=int(pos.qty),
                emitted_at_ns=int(
                    datetime.now(UTC).timestamp() * 1_000_000_000,
                ),
                reason="mis_auto_square_off",
            )
            # Use the position's avg price as a reference for the
            # marketable-LIMIT calc inside _submit_order. Real-time
            # LTP would be better, but this method runs from a
            # standalone task and doesn't have the per-tick last
            # price map handy. Avg-price is a conservative anchor.
            try:
                await self._submit_order(
                    signal=signal,
                    last_price=Decimal(str(pos.avg_price)),
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "LiveRuntime: MIS auto-square SELL failed for "
                    "%s: %s — Kite's broker-side 15:15 auto-square"
                    " will still close the position",
                    ticker,
                    exc,
                    exc_info=True,
                )

    async def run(self, source: TickSource) -> int:
        """Drain the tick source. Returns fill count."""
        from backend.algo.stream.sources import ReplayTickSource

        self._is_replay = isinstance(source, ReplayTickSource)
        fills = 0
        last_price_per_ticker: dict[str, Decimal] = {}
        # PR #1 (order-safety) — track per-ticker last-tick arrival
        # so place_order can enforce ALGO_MAX_LTP_AGE_S. Sourced
        # from Tick.ts_ns (multiplexer stamps now_ns; see open
        # question #1 in spec §7 — exchange_timestamp upgrade is
        # tracked separately, local arrival is adequate for the
        # WS-freeze detection the gate is designed to catch).
        last_price_ts_per_ticker: dict[str, datetime] = {}
        tick_count = 0
        bar_count = 0
        signal_count = 0
        _logger.info(
            "LiveRuntime: starting drain user=%s strat=%s " "run_id=%s",
            self._user_id,
            self._strategy.id,
            self._run_id,
        )
        # PR #3 (order-safety) — start the order TTL watcher as a
        # background task BEFORE the drain loop. Dry-run sessions
        # still start the watcher (it's a no-op against a Kite client
        # whose cancel_order is itself a no-op in dry-run, and the
        # observability events still flow). Disabled when
        # ALGO_ORDER_TTL_S=0 — preserves the rollout backout knob.
        if not self._timeout_watcher:
            from backend.algo.live.order_timeout import _read_ttl_s

            ttl = _read_ttl_s()
            if ttl > 0:
                self._timeout_watcher = _OrderTimeoutWatcher(
                    kite_client=self._kite,
                    session_id=self._session_id,
                    strategy_id=self._strategy.id,
                    user_id=self._user_id,
                    events_sink=self._events.append,
                )
                self._timeout_watcher_task = asyncio.create_task(
                    self._timeout_watcher.run(),
                )
            else:
                _logger.info(
                    "LiveRuntime: order timeout watcher disabled "
                    "(ALGO_ORDER_TTL_S=0)",
                )

        # ASETPLTFRM-394 — schedule MIS auto-square-off task for any
        # MIS strategy. CNC strategies skip this entirely; the
        # ``finally:`` block tolerates ``_square_off_task`` being
        # None so the daily-strategy lifecycle is unchanged.
        if (
            self._square_off_task is None
            and getattr(self._strategy, "product", "CNC") == "MIS"
        ):
            self._square_off_task = asyncio.create_task(
                self._schedule_mis_square_off(),
            )
        try:
            async for tick in source:
                tick_count += 1
                if tick_count == 1 or tick_count % 500 == 0:
                    _logger.info(
                        "LiveRuntime: tick #%d ticker=%s",
                        tick_count,
                        tick.ticker,
                    )
                last_price_per_ticker[tick.ticker] = Decimal(str(tick.ltp))
                # PR #1 — stamp arrival time for staleness gate.
                # ASETPLTFRM-372 — prefer exchange-emission ts
                # when Kite supplied it (full/quote-mode packets);
                # fall back to local arrival. Catches "exchange
                # froze but WS connection is healthy" failures.
                ts_ns = _select_last_price_ts_ns(tick)
                last_price_ts_per_ticker[tick.ticker] = datetime.fromtimestamp(
                    ts_ns / 1_000_000_000,
                    tz=UTC,
                )
                self._resampler.feed(tick)
                for bar in self._resampler.pop_completed():
                    bar_count += 1
                    lp = last_price_per_ticker.get(
                        bar.ticker,
                        Decimal(str(bar.close)),
                    )
                    lp_ts = last_price_ts_per_ticker.get(bar.ticker)
                    n = await self._on_bar_close(
                        bar=bar,
                        last_price=lp,
                        last_price_ts=lp_ts,
                    )
                    fills += n
                    if n > 0:
                        signal_count += n
            _logger.info(
                "LiveRuntime: drain complete ticks=%d bars=%d "
                "fills=%d events_buffered=%d",
                tick_count,
                bar_count,
                fills,
                len(self._events),
            )
        finally:
            # ASETPLTFRM-394 — cancel the MIS auto-square task on
            # session stop. If we stopped BEFORE the scheduled
            # square-off time, the task is sleeping and gets
            # cancelled cleanly. If we stopped AFTER firing, the
            # task already completed and the cancel is a no-op.
            # CNC strategies leave _square_off_task as None and
            # skip this whole block.
            if self._square_off_task is not None:
                self._square_off_task.cancel()
                try:
                    await self._square_off_task
                except (asyncio.CancelledError, Exception):
                    pass

            # PR #3 (order-safety) — stop the timeout watcher first
            # so any in-flight cancellation event lands in
            # ``self._events`` before the terminal flush below.
            # Bounded wait so a hung Kite ``orders()`` cannot block
            # session teardown indefinitely (30s is well above the
            # default 15s poll cadence).
            if self._timeout_watcher is not None:
                self._timeout_watcher.request_stop()
            if self._timeout_watcher_task is not None:
                try:
                    await asyncio.wait_for(
                        self._timeout_watcher_task,
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    _logger.warning(
                        "LiveRuntime: order timeout watcher did "
                        "not stop within 30s — cancelling task",
                    )
                    self._timeout_watcher_task.cancel()
                    try:
                        await self._timeout_watcher_task
                    except (asyncio.CancelledError, Exception):
                        pass
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "LiveRuntime: order timeout watcher "
                        "raised during stop",
                        exc_info=True,
                    )
                self._timeout_watcher = None
                self._timeout_watcher_task = None
            for bar in self._resampler.close_partial_bars():
                lp = last_price_per_ticker.get(
                    bar.ticker,
                    Decimal(str(bar.close)),
                )
                lp_ts = last_price_ts_per_ticker.get(bar.ticker)
                fills += await self._on_bar_close(
                    bar=bar,
                    last_price=lp,
                    last_price_ts=lp_ts,
                )
            if self._events:
                _logger.info(
                    "LiveRuntime: flushing %d events to " "algo.events",
                    len(self._events),
                )
                flush_events(self._events)
                self._events = []
            if hasattr(source, "stop"):
                try:
                    await source.stop()
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "LiveWsTickSource.stop() raised",
                        exc_info=True,
                    )
        return fills

    # ----------------------------------------------------------
    # Per-bar logic
    # ----------------------------------------------------------

    async def _on_bar_close(
        self,
        *,
        bar: Any,
        last_price: Decimal,
        last_price_ts: datetime | None = None,
    ) -> int:
        """Evaluate → gate → submit to Kite. Returns 1 if filled."""
        # Best-effort: publish bar close as live LTP so the paper
        # P&L summary endpoint marks open positions to live ticks.
        # WS multiplexer also writes per-tick — bar-close is a
        # belt-and-braces fallback for tickers that go quiet.
        try:
            from backend.cache import get_cache

            get_cache().set(
                f"cache:ltp:{bar.ticker}",
                str(float(bar.close)),
                ttl=60,
            )
        except Exception:  # noqa: BLE001
            pass
        from datetime import datetime, timezone

        from backend.algo.backtest.indicators import compute_indicators
        from backend.algo.backtest.types import BarData as _BackBar
        from backend.algo.live.daily_bar_warmup import (
            preload_daily_bars,
        )

        bar_date_obj = datetime.fromtimestamp(
            bar.bar_open_ts_ns / 1_000_000_000,
            tz=timezone.utc,
        ).date()

        # ASETPLTFRM-393 — bucket-key resolution per cadence.
        # Daily (interval="1d") buckets by trading date; one running
        # bar per day. Intraday buckets by bar_open_ts_ns floored to
        # interval_sec; multiple bars share a date so the date alone
        # can't tell us when a new bar starts.
        strategy_interval = self._strategy.schedule.interval
        if strategy_interval == "1d":
            bucket_key: Any = bar_date_obj
            bucket_open_ns: int | None = None
        else:
            from backend.algo.live.intraday_bar_warmup import (
                INTERVAL_SEC_BY_LABEL,
            )

            interval_sec = INTERVAL_SEC_BY_LABEL[strategy_interval]
            interval_ns = interval_sec * 1_000_000_000
            bucket_open_ns = (bar.bar_open_ts_ns // interval_ns) * interval_ns
            bucket_key = bucket_open_ns

        # ASETPLTFRM-383 / 393 — preloaded closed bars + a running
        # bar that the per-minute callback updates in place.
        # ``_bars_by_ticker`` is keyed by ticker — each LiveRuntime
        # carries exactly one cadence, so no (ticker, interval) key
        # needed at the dict level. Lazy-preload routes through the
        # right warmup module based on strategy cadence.
        history = self._bars_by_ticker.get(bar.ticker)
        if history is None:
            try:
                if strategy_interval == "1d":
                    lazy = await asyncio.to_thread(
                        preload_daily_bars,
                        [bar.ticker],
                        kite_client=self._kite,
                        ticker_to_token=(self._ticker_to_token or None),
                    )
                else:
                    from backend.algo.live.intraday_bar_warmup import (
                        preload_intraday_bars,
                    )

                    lazy = await asyncio.to_thread(
                        preload_intraday_bars,
                        [bar.ticker],
                        interval_sec=interval_sec,
                        kite_client=self._kite,
                        ticker_to_token=(self._ticker_to_token or None),
                    )
                history = lazy.get(bar.ticker, [])
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "LiveRuntime: lazy %s-bar preload for %s failed:"
                    " %s — strategy silent-skips on this ticker "
                    "until indicators settle",
                    strategy_interval,
                    bar.ticker,
                    exc,
                    exc_info=True,
                )
                history = []
            self._bars_by_ticker[bar.ticker] = history

        # Append (new bucket, or first bar for this ticker) or
        # update (existing running bar) the OHLCV candle. We refresh
        # close every minute so the indicator series reflects the
        # current LTP within the still-building bucket; high/low
        # broaden monotonically; volume accumulates.
        cur_open = Decimal(str(bar.open))
        cur_high = Decimal(str(bar.high))
        cur_low = Decimal(str(bar.low))
        cur_close = Decimal(str(bar.close))
        cur_vol = max(int(bar.volume), 0)

        def _is_new_bucket(h: list[Any]) -> bool:
            if not h:
                return True
            last = h[-1]
            if strategy_interval == "1d":
                return last.date != bar_date_obj
            return last.bar_open_ts_ns != bucket_open_ns

        if _is_new_bucket(history):
            history.append(
                _BackBar(
                    ticker=bar.ticker,
                    date=bar_date_obj,
                    open=cur_open,
                    high=cur_high,
                    low=cur_low,
                    close=cur_close,
                    volume=cur_vol,
                    bar_open_ts_ns=bucket_open_ns,
                )
            )
        else:
            today_bar = history[-1]
            history[-1] = today_bar.model_copy(
                update={
                    "high": max(today_bar.high, cur_high),
                    "low": min(today_bar.low, cur_low),
                    "close": cur_close,
                    "volume": today_bar.volume + cur_vol,
                }
            )

        # ASETPLTFRM-383 — eval-time gate. Before MIN_EVAL_TIME_IST,
        # ticks update today's bar but no strategy eval fires. Lets
        # the daily candle stabilise before we act on it. Override
        # ``ALGO_DAILY_MIN_EVAL_TIME_IST`` for smoke testing.
        #
        # ASETPLTFRM-390 — gate is daily-only. Intraday cadences
        # (5m / 1m) want to fire on every closed bar from market
        # open at 09:15 IST; gating them on the daily-candle-stabilise
        # cutoff would silence the entire morning session and defeat
        # the purpose of running an intraday strategy. The env var
        # name is ALGO_DAILY_MIN_EVAL_TIME_IST precisely because the
        # constraint is daily-specific.
        # Replay drains historical ticks, so wall-clock has no relation
        # to the replayed bars — the daily-candle-stabilise cutoff would
        # wrongly silence the whole replay before 14:30 IST. Enforce the
        # gate only for real-time sources (live-WS, incl. live-WS
        # dry-run, where the candle really is still forming).
        if (
            self._strategy.schedule.interval == "1d"
            and not self._is_replay
        ):
            now_ist_t = datetime.now(IST).time()
            if now_ist_t < _MIN_EVAL_TIME_IST:
                return 0

        ind_map = compute_indicators(history)
        # FE-10 — emit per-ticker intraday features to
        # ``stocks.intraday_features`` for the bar that just
        # closed. Side-effect only; failure is non-fatal. Daily
        # cadence is a no-op inside the emitter (FE-3 owns the
        # daily writes). Cohort features (FE-8 / FE-9) are NOT
        # emitted here — daily-batch compute is canonical.
        try:
            from backend.algo.features.live_emitter import (
                _INTERVAL_SEC_BY_LABEL,
                emit_features_for_bar,
            )

            _cadence = self._strategy.schedule.interval
            if _cadence in _INTERVAL_SEC_BY_LABEL:
                emit_features_for_bar(
                    ticker=bar.ticker,
                    interval_sec=_INTERVAL_SEC_BY_LABEL[_cadence],
                    history=history,
                    cadence_interval=_cadence,
                    mode="live",
                )
        except Exception:
            _logger.exception(
                "[live] FE-10 feature emission hook failed "
                "(non-fatal): ticker=%s",
                bar.ticker,
            )
        # REGIME-2a — lazy-load cached factor rows for this
        # ticker on first sight; subsequent bars are O(1).
        self._ensure_factor_cache(bar.ticker, bar_date_obj)
        self._ensure_regime_cache(bar_date_obj)
        self._ensure_daily_overlay_cache(bar.ticker, bar_date_obj)
        # FE-15b — shared per-bar feature assembly (single
        # source of truth across backtest/paper/live/dry-run).
        features = assemble_per_bar_features(
            bar_feats=ind_map.get(
                bar_date_obj,
                {
                    "today_ltp": bar.close,
                    "today_vol": Decimal(bar.volume),
                },
            ),
            market_regime=self._market_regime.get(bar_date_obj),
            market_trend=self._market_trend.get(bar_date_obj),
            factor_row=self._factor_cache.get(
                (bar.ticker, bar_date_obj),
            ),
            regime_row=self._regime_by_date.get(bar_date_obj),
            daily_overlay=self._daily_overlay_cache.get(
                (bar.ticker, bar_date_obj),
            ),
        )

        existing_pos = self._positions.open_positions().get(bar.ticker)

        # Stop-loss enforcement (universal v1, long-only). Run BEFORE
        # per-ticker AST eval so an in-flight stop blocks any
        # conflicting AST action on the same bar. Live differs from
        # backtest/paper: the SELL is submitted IMMEDIATELY through
        # KiteClient.place_order via _submit_order — no next-bar-open
        # delay. ``signal.reason="stop_loss"`` flows through
        # _submit_order → in_flight_entry["reason"] → Kite postback
        # → order_filled_live event payload (spec §4.5). Kite v2
        # rejects naked MARKET / bracket orders so _submit_order
        # uses an aggressive LIMIT priced at ``last_price`` with a
        # liquidity-bucket-driven slippage buffer — same path the
        # AST-driven SELL takes.
        #
        # IMPORTANT: SL submissions bypass _pre_trade_check
        # (kill_switch, max_inr, max_orders, allowed_tickers)
        # intentionally — stops must fire to bleed risk even when
        # the strategy is otherwise gated. Position-tracker realism
        # + LTP-staleness guard inside _submit_order remain in force.
        if existing_pos is not None and existing_pos.qty > 0:
            sl_triggers = check_stop_loss_triggers(
                open_positions={
                    bar.ticker: {
                        "qty": existing_pos.qty,
                        "avg_price": existing_pos.avg_price,
                    },
                },
                current_closes={
                    bar.ticker: Decimal(str(bar.close)),
                },
                stop_loss_pct=float(
                    self._strategy.risk.per_trade.stop_loss_pct
                ),
            )
            for trig in sl_triggers:
                sl_signal = Signal(
                    strategy_id=self._strategy.id,
                    user_id=self._user_id,
                    ticker=trig.ticker,
                    side="SELL",
                    qty=existing_pos.qty,
                    emitted_at_ns=bar.bar_open_ts_ns,
                    reason="stop_loss",
                )
                # INFO (not DEBUG) — live is real money; operators
                # need stops visible without flipping the log level.
                _logger.info(
                    "live stop_loss SELL %s qty=%d avg=%.4f "
                    "close=%.4f loss=%.2f%% threshold=%.2f%%",
                    trig.ticker,
                    existing_pos.qty,
                    float(trig.avg_price),
                    float(trig.current_close),
                    float(trig.loss_pct),
                    float(trig.stop_loss_pct),
                )
                fill_count = await self._submit_order(
                    signal=sl_signal,
                    last_price=last_price,
                    last_price_ts=last_price_ts,
                )
                # Same-bar skip: AST eval MUST NOT run for a ticker
                # that just stopped out — mirrors backtest / paper.
                # Propagate _submit_order's actual return value so the
                # per-bar fill counter is correctly attributed on
                # submission failure (e.g. LTP staleness, Kite error).
                # Keep in-process cooldown history in sync so the
                # gate fires on next-day re-entry attempts without
                # waiting for the next algo.events flush.
                self._cooldown_history.append(
                    _HydratedClose(
                        ticker=trig.ticker,
                        exit_reason="stop_loss",
                        closed_at=bar_date_obj,
                    )
                )
                return fill_count

        # ASETPLTFRM-436 — time-stop monitor (sibling to the
        # stop-loss block above). Same shape, different trigger
        # (holding_days >= max_holding_days). Submits IMMEDIATE
        # LIMIT SELL via _submit_order on the standard rails;
        # in_flight_entry["reason"]="time_stop" flows to the
        # order_filled_live event payload.
        if existing_pos is not None and existing_pos.qty > 0:
            ts_triggers = check_time_stop_triggers(
                open_positions={
                    bar.ticker: {
                        "qty": existing_pos.qty,
                        "opened_at": existing_pos.opened_at,
                    },
                },
                current_date=bar_date_obj,
                max_holding_days=(
                    self._strategy.risk.per_trade.max_holding_days
                ),
            )
            for trig in ts_triggers:
                ts_signal = Signal(
                    strategy_id=self._strategy.id,
                    user_id=self._user_id,
                    ticker=trig.ticker,
                    side="SELL",
                    qty=existing_pos.qty,
                    emitted_at_ns=bar.bar_open_ts_ns,
                    reason="time_stop",
                )
                _logger.info(
                    "live time_stop SELL %s qty=%d held=%d "
                    "days (threshold=%d)",
                    trig.ticker,
                    existing_pos.qty,
                    trig.holding_days,
                    trig.max_holding_days,
                )
                fill_count = await self._submit_order(
                    signal=ts_signal,
                    last_price=last_price,
                    last_price_ts=last_price_ts,
                )
                self._cooldown_history.append(
                    _HydratedClose(
                        ticker=trig.ticker,
                        exit_reason="time_stop",
                        closed_at=bar_date_obj,
                    )
                )
                return fill_count

        ctx = EvalContext(
            ticker=bar.ticker,
            bar_date=bar_date_obj,
            features=features,
            open_qty=existing_pos.qty if existing_pos else 0,
        )
        try:
            action = self._evaluator.eval_node(
                self._strategy.root.model_dump(by_alias=True),
                ctx,
            )
        except KeyError:
            return 0

        signal = self._action_to_signal(
            action,
            ticker=bar.ticker,
            bar_date_ns=bar.bar_open_ts_ns,
            last_price=last_price,
        )
        if signal is None:
            return 0

        # ASETPLTFRM-436 — repeat-offender cooldown gate. Blocks
        # NEW entries on a ticker with a recent failed exit
        # (time_stop / stop_loss). Hydrated from algo.events at
        # session start; kept in sync in-process as new failed
        # exits land above.
        cd_days = self._strategy.risk.per_trade.cooldown_after_failed_exit_days
        if (
            signal.side == "BUY"
            and cd_days
            and in_cooldown(
                ticker=bar.ticker,
                bar_date=bar_date_obj,
                closed_positions=self._cooldown_history,
                cooldown_days=cd_days,
            )
        ):
            _logger.info(
                "live cooldown SKIP %s — recent failed exit " "within %d days",
                bar.ticker,
                cd_days,
            )
            return 0

        # MIS "no new entries after T-1h" gate — shared helper so
        # backtest / paper / dry-run / live all enforce the same
        # rule. SELL / exit signals are unaffected.
        if signal.side == "BUY":
            from backend.algo.runtime.intraday_window import (
                is_entry_allowed,
                ist_time_from_ns,
            )

            bar_ist_time = ist_time_from_ns(bar.bar_open_ts_ns)
            if bar_ist_time is not None and not is_entry_allowed(
                product=self._strategy.product,
                entry_cutoff_raw=self._strategy.entry_cutoff_time,
                bar_time_ist=bar_ist_time,
            ):
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="live",
                        type_="signal_rejected",
                        payload={
                            **({"dry_run": True} if self._dry_run else {}),
                            "reason": "mis_entry_cutoff",
                            "ticker": signal.ticker,
                            "side": signal.side,
                            "qty": signal.qty,
                            "bar_ist_time": bar_ist_time.isoformat(),
                            "entry_cutoff": (self._strategy.entry_cutoff_time),
                        },
                    )
                )
                return 0

        # ASETPLTFRM-381 — also emit ``symbol`` (canonical, no .NS)
        # alongside ``ticker`` so attribution.trades can pair
        # signals with fills (fills carry payload.symbol only). The
        # ``.NS`` suffix denotes the NSE market in our internal
        # ticker scheme; Kite's tradingsymbol drops it.
        _canonical_symbol = str(signal.ticker).upper().removesuffix(".NS")
        self._events.append(
            event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="live",
                type_="signal_generated",
                payload={
                    **({"dry_run": True} if self._dry_run else {}),
                    "ticker": signal.ticker,
                    "symbol": _canonical_symbol,
                    "side": signal.side,
                    "qty": signal.qty,
                    # REGIME-6 — attribution context (additive).
                    **_attribution_payload_extension(features),
                },
            )
        )
        self._flush_events_now()

        # Fresh caps read — used for max_inr / max_orders_per_day
        # and the allow-list; the daily-counter columns on the row
        # are no longer authoritative (see below).
        current_caps = (
            await self._caps_repo.get(
                self._user_id,
                self._strategy.id,
            )
            or self._caps
        )

        account = self._account_snapshot(
            kill_switch_active=await self._kill_switch_repo.is_active(
                self._user_id
            ),
        )
        # Exposure-based day_state: "consumption" is the capital
        # currently tied up in this strategy's open positions, not
        # turnover-since-09:00. PositionTracker is hydrated from
        # Kite at runtime spawn (position_hydration.hydrate) so a
        # restart preserves yesterday's overnight legs. Square-offs
        # naturally bring this back to 0, no daily reset job needed.
        positions_open = self._positions.open_positions()
        committed_inr_now = sum(
            (Decimal(p.qty) * p.avg_price for p in positions_open.values()),
            start=Decimal("0"),
        )
        day_state = {
            "cumulative_inr_today": committed_inr_now,
            "orders_count_today": len(positions_open),
        }

        decision = await pre_trade_check(
            signal=signal,
            caps=current_caps,
            day_state=day_state,
            account=account,
            strategy_risk=self._strategy.risk.model_dump(),
            last_price=last_price,
            user_id=self._user_id,
            dry_run=self._dry_run,
        )

        if decision.outcome == "reject":
            reason_str = (
                decision.reason.value
                if hasattr(decision.reason, "value")
                else str(decision.reason) if decision.reason else "unknown"
            )
            self._events.append(
                event_row(
                    session_id=self._session_id,
                    user_id=self._user_id,
                    strategy_id=self._strategy.id,
                    mode="live",
                    type_="signal_rejected",
                    payload={
                        **({"dry_run": True} if self._dry_run else {}),
                        "reason": reason_str,
                        "ticker": signal.ticker,
                        "side": signal.side,
                        "qty": signal.qty,
                        "threshold": (
                            str(decision.threshold)
                            if decision.threshold is not None
                            else None
                        ),
                        "observed_value": (
                            str(decision.observed_value)
                            if decision.observed_value is not None
                            else None
                        ),
                    },
                )
            )
            self._flush_events_now()
            return 0

        effective_qty = (
            decision.adjusted_qty
            if decision.outcome == "scale" and decision.adjusted_qty
            else signal.qty
        )
        signal = signal.model_copy(update={"qty": effective_qty})

        # PR #4 — propagate remaining daily-cap budget so the
        # broker layer can short-circuit freeze-chunked orders
        # that would breach max_orders_per_day. Zero/negative
        # means "no cap configured" (KiteClient ignores it).
        max_orders = int(current_caps.get("max_orders_per_day", 0))
        orders_today = int(day_state.get("orders_count_today", 0))
        daily_cap_remaining = (
            max(0, max_orders - orders_today) if max_orders > 0 else None
        )

        return await self._submit_order(
            signal=signal,
            last_price=last_price,
            last_price_ts=last_price_ts,
            daily_cap_remaining=daily_cap_remaining,
        )

    # ----------------------------------------------------------
    # Kite order submission
    # ----------------------------------------------------------

    async def _submit_order(
        self,
        *,
        signal: Signal,
        last_price: Decimal,
        last_price_ts: datetime | None = None,
        daily_cap_remaining: int | None = None,
    ) -> int:
        """Submit one order to Kite. Returns 1 on success, 0 on error."""
        internal_order_id = str(uuid4())
        now_iso = datetime.now(IST).isoformat()

        # Determine exchange — Indian .NS → NSE
        exchange = "NSE"
        symbol = signal.ticker.replace(".NS", "")
        side = "BUY" if signal.side == "BUY" else "SELL"

        # Use LIMIT orders priced at the bar-close LTP plus a
        # small marketable buffer so the order is aggressive
        # enough to fill on the opposite side of the spread but
        # capped against runaway slippage. Switching from MARKET
        # solves three problems:
        #   1. Kite Connect refuses naked MARKET orders without
        #      market_protection — see commit 13001fb.
        #   2. The strategy was evaluated at `last_price` so we
        #      have a known reference; sending MARKET makes the
        #      fill price drift unpredictably and breaks the
        #      P&L summary's last_fill mark logic.
        #   3. Aggressive LIMIT mirrors how a manual day trader
        #      places intraday entries on big-cap NSE stocks.
        # PR #2 (order-safety) — slippage bps now ticker-aware via
        # the liquidity-bucket lookup loaded at session start.
        # Bucket = composite of mcap + 20d ADTV, conservative wins.
        # Defaults: largecap 20 / midcap 50 / smallcap 100 /
        # unknown 30 bps. Env-overrideable (spec §4).
        bucket = self._bucket_by_ticker.get(signal.ticker)
        slippage_bps = _slippage.bps_for(bucket)
        spread_bps = Decimal(slippage_bps)
        BPS_DENOM = Decimal("10000")
        limit_price: Decimal | None = None
        if last_price and last_price > 0:
            buffer = last_price * spread_bps / BPS_DENOM
            limit_price = (
                last_price + buffer if side == "BUY" else last_price - buffer
            )
            # NSE tick size — round to 0.05 to satisfy Kite's
            # tick rule, which rejects price not divisible by tick.
            tick = Decimal("0.05")
            limit_price = (limit_price / tick).quantize(Decimal("1")) * tick
            order_kwargs = {
                "order_type": "LIMIT",
                "price": float(limit_price),
            }
        else:
            order_kwargs = {"order_type": "MARKET"}

        # PR #1 — convert Decimal LTP to float for the staleness
        # gate + audit payload (Iceberg JSON serialises Decimal
        # to string; keeping it as float in the payload makes the
        # frontend renderer simpler).
        lp_float: float | None = (
            float(last_price) if last_price and last_price > 0 else None
        )
        # ASETPLTFRM-389 — product is now strategy-driven instead of
        # hard-coded CNC. Existing daily strategies parse with
        # product="CNC" (the AST default), so this read returns "CNC"
        # for every strategy that existed before ASETPLTFRM-387.
        # Intraday MIS strategies route here with product="MIS".
        product_code = self._strategy.product

        # Budget reservation — append-only audit lifecycle.
        # Reserves on BUY + SELL so the ledger captures full
        # context (Cap 0 gating runs separately in safety.py;
        # this is the audit trail).
        order_cost = (
            Decimal(signal.qty) * last_price
            if last_price and last_price > 0
            else Decimal("0")
        )
        reservation_id = await budget_reserve(
            user_id=self._user_id,
            strategy_id=self._strategy.id,
            ticker=signal.ticker,
            side=signal.side,
            qty=signal.qty,
            reserved_inr=order_cost,
            metadata={
                "internal_order_id": internal_order_id,
                "limit_price": (
                    str(limit_price) if limit_price is not None else None
                ),
            },
        )
        try:
            kite_order_id = await asyncio.to_thread(
                self._kite.place_order,
                tradingsymbol=symbol,
                exchange=exchange,
                transaction_type=side,
                quantity=signal.qty,
                **order_kwargs,
                product=product_code,
                variety="regular",
                tag=f"algo-{str(self._strategy.id)[:8]}",
                # PR #1 — order-safety hardening + full-payload
                # audit. last_price_ts feeds the staleness gate.
                # PR #2 — populate bucket + applied bps so the
                # order_submitted_live audit row carries the
                # full pre-trade decision trace (spec §3.6).
                last_price=lp_float,
                last_price_ts=last_price_ts,
                liquidity_bucket=bucket,
                slippage_bps_applied=slippage_bps,
                chunk_index=None,
                chunk_total=None,
                events_sink=self._events.append,
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                internal_order_id=internal_order_id,
                # PR #4 — daily-cap budget for freeze-chunk pre-check
                daily_cap_remaining=daily_cap_remaining,
            )
        except Exception as exc:
            rejection_reason = str(exc)
            self._events.append(
                event_row(
                    session_id=self._session_id,
                    user_id=self._user_id,
                    strategy_id=self._strategy.id,
                    mode="live",
                    type_="order_rejected_live",
                    payload={
                        **({"dry_run": True} if self._dry_run else {}),
                        "internal_order_id": internal_order_id,
                        "symbol": symbol,
                        "side": side,
                        "qty": signal.qty,
                        "rejection_reason": rejection_reason,
                        "kite_order_id": None,
                    },
                )
            )
            _logger.error(
                "live order rejected: symbol=%s side=%s qty=%d " "reason=%s",
                symbol,
                side,
                signal.qty,
                rejection_reason,
            )
            # Mark the reservation REJECTED — wrapped so a
            # budget-audit failure doesn't shadow the original
            # Kite SDK error path.
            try:
                await budget_transition(
                    reservation_id=reservation_id,
                    new_state=ReservationState.REJECTED,
                    error_text=str(exc)[:500],
                )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "budget transition on Kite error failed",
                )
            return 0

        # Reservation now carries the broker-assigned id.
        # Wrapped so a DB blip after a successful Kite order
        # doesn't bubble up and leave the order un-recorded
        # in in_flight. Degraded state (ledger PENDING with
        # kite_order_id=NULL) heals via the T+120s PENDING
        # timeout reconciliation path — log loudly.
        try:
            await budget_transition(
                reservation_id=reservation_id,
                new_state=ReservationState.SUBMITTED,
                kite_order_id=kite_order_id,
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "budget transition on Kite success failed — "
                "reservation %s, kite order %s — "
                "reconciliation loop will heal",
                reservation_id,
                kite_order_id,
            )

        is_dry = kite_order_id.startswith("DRY_")

        # Record in-flight. ``reason`` + ``product`` carried here
        # so synthetic_fill (dry-run) and the Kite postback
        # reconciliation can both stamp the same context onto the
        # final order_filled_live event payload — that's what the
        # Positions tab Reason column and the (symbol, product)
        # attribution join read.
        in_flight_entry = {
            "kite_order_id": kite_order_id,
            "internal_order_id": internal_order_id,
            "symbol": symbol,
            "side": side,
            "qty": signal.qty,
            "submitted_at": now_iso,
            "status": "submitted",
            "reason": signal.reason,
            # ASETPLTFRM-389 — strategy-driven (was hard-coded "CNC").
            # The (symbol, product) attribution join in routes/live.py
            # reads this to pair postback fills with the originating
            # strategy; surfacing the actual broker product keeps that
            # join honest for MIS positions too.
            "product": product_code,
        }
        self._in_flight.append(in_flight_entry)
        await self._caps_repo.update_in_flight(
            self._user_id,
            self._run_id,
            self._in_flight,
        )

        # PR #1 (order-safety) — order_submitted_live is now
        # emitted from inside KiteClient.place_order with the full
        # request/context/response payload (spec §3.6). We carry
        # `reason` separately into in_flight_entry above so the
        # eventual order_filled_live event keeps the attribution
        # link; the kite_client payload preserves all top-level
        # keys (kite_order_id / dry_run / side / qty / symbol)
        # that PaperEventsTimeline reads.
        self._flush_events_now()

        # Dry-run: spawn synthetic fill after short delay
        if is_dry:
            asyncio.create_task(
                self._synthetic_fill(
                    kite_order_id=kite_order_id,
                    internal_order_id=internal_order_id,
                    symbol=symbol,
                    side=side,
                    qty=signal.qty,
                    fill_price=last_price,
                    in_flight_entry=in_flight_entry,
                    reservation_id=reservation_id,
                ),
                name=f"dry_fill_{kite_order_id}",
            )

        # Bump daily counters
        order_notional = Decimal(signal.qty) * last_price
        await self._caps_repo.increment_daily_counters(
            self._user_id,
            self._strategy.id,
            inr_amount=order_notional,
        )

        _logger.info(
            "live order submitted: symbol=%s side=%s qty=%d "
            "kite_order_id=%s internal=%s",
            symbol,
            side,
            signal.qty,
            kite_order_id,
            internal_order_id,
        )
        return 1

    # ----------------------------------------------------------
    # Dry-run synthetic fill
    # ----------------------------------------------------------

    _DRY_FILL_DELAY_S: float = 0.1  # 100 ms — configurable in tests

    async def _synthetic_fill(
        self,
        *,
        kite_order_id: str,
        internal_order_id: str,
        symbol: str,
        side: str,
        qty: int,
        fill_price: Decimal,
        in_flight_entry: dict,
        reservation_id: UUID | None = None,
    ) -> None:
        """Simulate a Kite fill for dry-run mode.

        Sleeps ``_DRY_FILL_DELAY_S`` then:
        1. Computes fees via IndianFeeModel.
        2. Applies the fill to PositionTracker.
        3. Emits an ``order_filled_live`` event with
           ``dry_run: true`` in the payload.
        4. Marks the in-flight entry as filled.
        5. Transitions the budget reservation to FILLED — Kite has
           no record of a synthetic ``DRY_`` order, so the
           reconciliation loop can never advance it; without this
           the reservation stays SUBMITTED and its capital is never
           released back to the user-pool headroom.
        """
        await asyncio.sleep(self._DRY_FILL_DELAY_S)

        from backend.algo.backtest.types import Fill
        from backend.algo.fees import IndianFeeModel, Trade

        today = datetime.now(UTC).date()
        fee_model = IndianFeeModel(as_of=today)
        # ASETPLTFRM-389 — fee model uses DELIVERY / INTRADAY (not
        # CNC / MIS). Map from strategy.product, defaulting to
        # DELIVERY so existing CNC strategies keep the same fee
        # tier they had before this change.
        product = "INTRADAY" if self._strategy.product == "MIS" else "DELIVERY"
        trade = Trade(
            symbol=symbol,
            exchange="NSE",
            side=side,  # type: ignore[arg-type]
            product=product,  # type: ignore[arg-type]
            qty=qty,
            price=fill_price,
        )
        fees = fee_model.compute(trade)

        # Update position tracker using the proper Fill model
        fill = Fill(
            intent_id=uuid4(),
            ticker=f"{symbol}.NS",
            side=side,  # type: ignore[arg-type]
            qty=qty,
            fill_price=fill_price,
            fill_date=today,
            fees_inr=fees.total_inr,
            fee_rates_version=fees.rates_version,
        )
        self._positions.apply_fill(fill)

        # Mark in-flight entry filled — flip in memory AND
        # persist so the in-flight orders panel (which polls
        # algo.runs.live_orders_in_flight) sees the transition.
        # Without the persist call the panel kept showing every
        # synthetic fill stuck at status='submitted' forever.
        in_flight_entry["status"] = "filled"
        in_flight_entry["fill_price"] = str(fill_price)
        in_flight_entry["fees_inr"] = str(fees.total_inr)
        try:
            await self._caps_repo.update_in_flight(
                self._user_id,
                self._run_id,
                self._in_flight,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "synthetic_fill: in-flight persist failed for "
                "kite_order_id=%s — panel will lag until next "
                "successful update",
                kite_order_id,
                exc_info=True,
            )

        # Emit fill event + flush immediately. Default behaviour
        # batches events to the end-of-drain flush; for in-session
        # fills we want them visible in the events panel within
        # the next SWR poll cycle (~5s) so users can verify their
        # dry-run trades end-to-end without stopping the session.
        self._events.append(
            event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="live",
                type_="order_filled_live",
                payload={
                    **({"dry_run": True} if self._dry_run else {}),
                    "internal_order_id": internal_order_id,
                    "kite_order_id": kite_order_id,
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "price": str(fill_price),
                    "fees_inr": str(fees.total_inr),
                    # Carry forward from in_flight_entry so the
                    # Positions tab Reason column has the action
                    # type and the attribution join can key on
                    # product. Both nullable for legacy entries
                    # written before this change.
                    "reason": in_flight_entry.get("reason"),
                    "product": in_flight_entry.get("product"),
                },
            )
        )
        self._flush_events_now()

        # Release the budget reservation: a synthetic DRY_ order has no
        # Kite counterpart, so reconciliation cannot advance it. Mark it
        # FILLED here so its reserved capital leaves active_reserved and
        # (for BUYs) is picked up by open_pos_cost. Best-effort: a budget
        # ledger blip must not shadow the fill itself.
        if reservation_id is not None:
            try:
                await budget_transition(
                    reservation_id=reservation_id,
                    new_state=ReservationState.FILLED,
                    filled_qty=qty,
                    filled_inr=Decimal(qty) * fill_price,
                )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "synthetic_fill: budget FILLED transition failed "
                    "for reservation=%s kite_order_id=%s",
                    reservation_id,
                    kite_order_id,
                )

        # ASETPLTFRM-402 / FE-5 — per-fill feature snapshot.
        # Live synthetic fills (and real Kite postback fills
        # which also flow through this method) have no
        # in-scope feature dict — the decision-time features
        # were emitted on the prior signal_generated event;
        # we write an empty features map here for complete
        # coverage of the fill ledger. Realised-pnl /
        # outcome_label backfilled by Phase-3 jobs.
        # Snapshot failure never blocks the fill / event.
        try:
            from backend.algo.features.snapshots import (
                write_trade_feature_snapshot,
            )

            # FE-5.1 — pass ``user_id`` so the dispatcher routes
            # this row to the Redis LIST keyed on the user (the
            # 15:30 IST EOD flush job drains one Iceberg commit
            # per ``(user_id, trading_date_ist)``).
            write_trade_feature_snapshot(
                fill_id=str(kite_order_id),
                run_id=str(self._run_id),
                strategy_id=str(self._strategy.id),
                ticker=f"{symbol}.NS",
                side=side,
                qty=qty,
                fill_price=fill_price,
                fill_ts_ns=None,
                bar_date=today.isoformat(),
                mode="live",
                features=None,
                user_id=str(self._user_id),
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "trade_feature_snapshot hook failed "
                "(non-fatal): symbol=%s mode=live "
                "kite_order_id=%s",
                symbol,
                kite_order_id,
            )

        _logger.info(
            "[DRY_RUN] synthetic fill: symbol=%s side=%s qty=%d "
            "price=%s fees=%s kite_order_id=%s",
            symbol,
            side,
            qty,
            fill_price,
            fees.total_inr,
            kite_order_id,
        )

    # ----------------------------------------------------------
    # Kill-switch in-flight cancellation
    # ----------------------------------------------------------

    async def cancel_in_flight_orders(self) -> dict[str, Any]:
        """Cancel all submitted-but-not-filled orders.

        Called when the kill switch is armed while this runtime
        is active.  Best-effort: failures are logged as
        ``order_cancel_failed`` events but do NOT raise.

        Returns a summary dict with ``cancelled`` and ``failed``
        counts.
        """
        in_flight = [
            e for e in self._in_flight if e.get("status") == "submitted"
        ]
        cancelled = 0
        failed = 0
        for entry in in_flight:
            kite_id = entry.get("kite_order_id")
            if not kite_id:
                continue
            try:
                await asyncio.to_thread(
                    self._kite.cancel_order,
                    kite_id,
                )
                entry["status"] = "cancelled"
                cancelled += 1
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="live",
                        type_="order_cancelled_live",
                        payload={
                            **({"dry_run": True} if self._dry_run else {}),
                            "kite_order_id": kite_id,
                            "reason": "kill_switch_armed",
                        },
                    )
                )
            except Exception as exc:
                failed += 1
                _logger.error(
                    "cancel_in_flight FAILED: kite_order_id=%s " "error=%s",
                    kite_id,
                    exc,
                )
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="live",
                        type_="order_cancel_failed",
                        payload={
                            **({"dry_run": True} if self._dry_run else {}),
                            "kite_order_id": kite_id,
                            "error": str(exc),
                        },
                    )
                )

        # Persist updated in-flight list
        await self._caps_repo.update_in_flight(
            self._user_id,
            self._run_id,
            self._in_flight,
        )

        # Flush all accumulated events
        if self._events:
            flush_events(self._events)
            self._events = []

        return {"cancelled": cancelled, "failed": failed}

    # ----------------------------------------------------------
    # Account snapshot + signal helpers (mirrors PaperRuntime)
    # ----------------------------------------------------------

    def _account_snapshot(
        self,
        *,
        kill_switch_active: bool = False,
    ) -> AccountState:
        open_qty = {
            t: p.qty for t, p in self._positions.open_positions().items()
        }
        return AccountState(
            user_id=self._user_id,
            day_date=datetime.now(UTC).date(),
            initial_capital_inr=self._initial,
            current_equity_inr=(
                self._initial + self._positions.total_realised_pnl_inr()
            ),
            daily_realised_pnl_inr=(self._positions.total_realised_pnl_inr()),
            daily_unrealised_pnl_inr=Decimal("0"),
            open_positions=open_qty,
            open_position_count=len(open_qty),
            kill_switch_active=kill_switch_active,
        )

    def _size_via_composer(
        self,
        *,
        qty_spec: dict,
        ticker: str,
        bar_date_ns: int,
        last_price: Decimal | None,
    ) -> int:
        """REGIME-4 — mirror of PaperRuntime._size_via_composer."""
        if last_price is None or last_price <= 0:
            return 0
        bar_date_obj = datetime.fromtimestamp(
            bar_date_ns / 1_000_000_000,
            tz=timezone.utc,
        ).date()
        nav = self._initial + self._positions.total_realised_pnl_inr()
        factor_row = self._factor_cache.get(
            (ticker, bar_date_obj),
            {},
        )
        realized_vol = factor_row.get(
            "realized_vol_60d",
            Decimal("NaN"),
        )
        ctx = SizingContext(
            ticker=ticker,
            bar_date=bar_date_obj,
            nav=nav,
            cash=nav,
            stock_price=last_price,
            realized_vol_annual=realized_vol,
            sector=None,
            sector_exposure=Decimal("0"),
            equity_curve=[],
        )
        return compose_qty(qty_spec, ctx)

    def _action_to_signal(
        self,
        action: dict,
        *,
        ticker: str,
        bar_date_ns: int,
        last_price: Decimal | None = None,
    ) -> Signal | None:
        """Identical to PaperRuntime._action_to_signal."""
        t = action.get("type")
        if t == "buy":
            qty_spec = action["qty"]
            # REGIME-4 — vol-target / Kelly route through composer.
            if "vol_target_pct" in qty_spec or "kelly_fraction" in qty_spec:
                qty = self._size_via_composer(
                    qty_spec=qty_spec,
                    ticker=ticker,
                    bar_date_ns=bar_date_ns,
                    last_price=last_price,
                )
            else:
                qty = int(qty_spec.get("shares") or 0)
            if qty <= 0:
                return None
            return Signal(
                strategy_id=self._strategy.id,
                user_id=self._user_id,
                ticker=ticker,
                side="BUY",
                qty=qty,
                emitted_at_ns=bar_date_ns,
                reason=t,
            )
        if t == "sell":
            qty_spec = action["qty"]
            if qty_spec.get("all"):
                existing = self._positions.open_positions().get(ticker)
                if not existing:
                    return None
                qty = existing.qty
            else:
                qty = int(qty_spec.get("shares") or 0)
            if qty <= 0:
                return None
            return Signal(
                strategy_id=self._strategy.id,
                user_id=self._user_id,
                ticker=ticker,
                side="SELL",
                qty=qty,
                emitted_at_ns=bar_date_ns,
                reason=t,
            )
        if t == "exit":
            existing = self._positions.open_positions().get(ticker)
            if not existing:
                return None
            return Signal(
                strategy_id=self._strategy.id,
                user_id=self._user_id,
                ticker=ticker,
                side="SELL",
                qty=existing.qty,
                emitted_at_ns=bar_date_ns,
                reason=t,
            )
        if t == "set_target_weight":
            if last_price is None or last_price <= 0:
                return None
            current_equity = (
                self._initial + self._positions.total_realised_pnl_inr()
            )
            if current_equity <= 0:
                return None
            try:
                weight = Decimal(str(action.get("weight", 0)))
            except Exception:  # noqa: BLE001
                return None
            if weight <= 0:
                return None
            target_qty = int(
                (current_equity * weight) // last_price,
            )
            existing = self._positions.open_positions().get(ticker)
            current_qty = existing.qty if existing else 0
            diff = target_qty - current_qty
            if diff > 0:
                return Signal(
                    strategy_id=self._strategy.id,
                    user_id=self._user_id,
                    ticker=ticker,
                    side="BUY",
                    qty=int(diff),
                    emitted_at_ns=bar_date_ns,
                    reason=t,
                )
            if diff < 0:
                return Signal(
                    strategy_id=self._strategy.id,
                    user_id=self._user_id,
                    ticker=ticker,
                    side="SELL",
                    qty=int(-diff),
                    emitted_at_ns=bar_date_ns,
                    reason=t,
                )
            return None
        return None

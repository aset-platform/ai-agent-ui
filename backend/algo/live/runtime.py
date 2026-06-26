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
import time as _time
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
from backend.algo.backtest.trailing_stop_manager import (
    TrailingStopManager,
)
from backend.algo.features.primitives import wilder_atr as _wilder_atr
from backend.algo.broker.exceptions import (
    PartialChunkPlacementError,
)
from backend.algo.broker.freeze_cache import get_tick_size
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
    fetch_kite_available_cash,
    reserve as budget_reserve,
)
from backend.algo.live.budget import (
    reserve_if_headroom as budget_reserve_if_headroom,
)
from backend.algo.live.budget import (
    sum_active_reservations_for_strategy as budget_active_for_strategy,
)
from backend.algo.live.budget import (
    transition as budget_transition,
)
from backend.algo.live.budget import (
    load_user_budget as budget_load_user,
)
from backend.algo.live.budget_types import ReservationState
from backend.db.engine import disposable_pg_session
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
    of leading/trailing whitespace; falls back to ``09:30`` on any
    parse failure with a warning so a malformed env var doesn't
    crash the runtime constructor."""
    try:
        hh, mm = s.strip().split(":")
        return time(int(hh), int(mm))
    except Exception:  # noqa: BLE001
        _logger.warning(
            "Invalid ALGO_DAILY_MIN_EVAL_TIME_IST=%r — falling "
            "back to 09:30",
            s,
        )
        return time(9, 30)


# ASETPLTFRM-383 — IST cutoff: before this time BUY decisions use only
# history[:-1] (yesterday's closed bar). At or after this time the
# today's still-forming running bar is also eligible for BUY entry.
# Exits (stop-loss, time-stop, discretionary SELL) are never gated —
# they always fire on full history regardless of wall-clock.
# Default 14:20 — 10 min before NSE close; lets the day's trend settle.
_MIN_EVAL_TIME_IST = _parse_ist_time(
    os.environ.get("ALGO_DAILY_MIN_EVAL_TIME_IST", "14:20"),
)

# Earliest wall-clock at which a BUY order may be placed.
# The NSE opening auction runs 09:07–09:15; the first 15 min of the
# regular session (09:15–09:30) is typically high-volatility price
# discovery. No BUY is placed before this time regardless of which
# eval path fired. SELLs and GTT exits are never gated here.
# Override: ALGO_MIN_BUY_TIME_IST (HH:MM IST).
_MIN_BUY_TIME_IST = _parse_ist_time(
    os.environ.get("ALGO_MIN_BUY_TIME_IST", "09:30"),
)

# PR3 — live-mode events are buffered and flushed on this cadence
# instead of one Iceberg commit per signal. The terminal flush on
# session stop drains whatever remains. Env-overridable for tuning.
_EVENT_FLUSH_INTERVAL_S = float(
    os.environ.get("ALGO_EVENT_FLUSH_INTERVAL_S", "5")
)

# High #15 — cap per-ticker bar history so a long live session cannot
# grow _bars_by_ticker without bound. 300 > SMA-200 (largest indicator
# lookback) with headroom; override via ALGO_MAX_BAR_HISTORY env var.
# Falls back to 300 on any parse error so a bad env value doesn't crash
# the runtime at startup.
try:
    _MAX_BAR_HISTORY = int(os.getenv("ALGO_MAX_BAR_HISTORY", "300"))
    if _MAX_BAR_HISTORY < 1:
        raise ValueError("must be positive")
except Exception:  # noqa: BLE001
    _MAX_BAR_HISTORY = 300

# _closed_entry_cache keys are (ticker, closed_date). Entries whose
# date is older than this many calendar days are evicted at bar-close.
# 4 calendar days covers 2 trading days including a weekend.
_CLOSED_ENTRY_CACHE_MAX_AGE_DAYS = 4


def _env_truthy(name: str) -> bool:
    """True when env var ``name`` is set to a truthy value.

    Read live (not at import) so tests can toggle it via monkeypatch
    and operators can flip it without a restart. Truthy ⇔ one of
    ``1/true/yes/on`` (case-insensitive); anything else is False.
    """
    val = os.environ.get(name)
    if val is None:
        return False
    return val.strip().lower() in {"1", "true", "yes", "on"}


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


def _parse_fill_date(*candidates: Any) -> date | None:
    """Return the ``date`` parsed from the first ISO-8601 candidate.

    Used to preserve a position's REAL open date across a restart /
    fill-sync. Each candidate is an ISO-8601 timestamp string (e.g.
    the in-flight entry's ``filled_at`` or ``submitted_at``). The
    first parseable one wins; returns ``None`` when none parse so the
    caller can fall back to ``date.today()`` rather than crash.

    A naive timestamp (no tz) is treated as UTC. We take the UTC
    ``.date()`` — fill dates are coarse-grained for the calendar-day
    ``max_holding_days`` arithmetic, so tz drift of a few hours never
    flips a holding-day count near the boundary in a way that under-
    counts (UTC is at or behind IST, so it never reports the position
    as YOUNGER than it really is).
    """
    for cand in candidates:
        if not cand:
            continue
        if isinstance(cand, date) and not isinstance(cand, datetime):
            return cand
        if isinstance(cand, datetime):
            return cand.date()
        if not isinstance(cand, str):
            continue
        raw = cand.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            _logger.warning(
                "fill-date: cannot parse ISO timestamp %r", cand,
            )
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.date()
    return None


def _to_int(v: Any) -> int:
    """Coerce a broker numeric field to int; 0 on garbage/None."""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _as_ns(tradingsymbol: str) -> str:
    """Kite bare tradingsymbol -> internal ``.NS`` ticker form.

    Leaves a symbol that already carries an exchange suffix
    (``"RELIANCE.NS"``) untouched; appends ``.NS`` otherwise.
    """
    return tradingsymbol if "." in tradingsymbol else f"{tradingsymbol}.NS"


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
        self._gtt_limit_headroom_pct: float = float(
            caps.get("gtt_limit_headroom_pct", 0.01)
        )
        self._kill_switch_repo = kill_switch_repo
        self._ticker_to_token = ticker_to_token or {}
        self._evaluator = Evaluator()
        self._resampler = Resampler(intervals=(60,))
        self._positions = PositionTracker()
        # Daily eval-gate: cache the entry decision computed on the last
        # CLOSED bar, keyed by (ticker, closed_date). The closed-bar
        # signal is fixed for the day, so this avoids re-running
        # compute_indicators on every intraday tick-bar while flat.
        self._closed_entry_cache: dict[tuple[str, date], dict | None] = {}
        self._session_id = uuid4()
        self._events: list[dict[str, Any]] = []
        self._in_flight: list[dict[str, Any]] = []
        # Per-ticker cap: set of internal tickers (e.g. "INFY.NS") that
        # have an active BUY order (in-flight or open position). Prevents
        # the weight mechanism from placing duplicate orders on the same
        # ticker within a session. Populated at startup from hydrated
        # positions + Redis; flushed to algo.runs every 30s.
        self._ticker_locked: set[str] = set()
        self._bars_by_ticker: dict[str, list] = {}
        # Task 4.0a — anti-churn guard. Keyed on (ticker, side) →
        # epoch seconds of the last ACTUAL placement. A non-protective
        # (rebalance/entry) order for the same key inside
        # ALGO_ORDER_COOLDOWN_S is suppressed; protective exits are
        # exempt. See _submit_order's churn guard.
        self._last_submit_ts: dict[tuple[str, str], float] = {}

        # Task 4.0b — capital-shrink guardrail. Set True in run() when
        # the configured start capital is below the cost-basis of
        # already-deployed positions (e.g. runtime restarted with ₹20k
        # while real positions were built under ₹100k). While set, the
        # ``set_target_weight`` branch SUPPRESSES rebalance-DOWN trims
        # (which would liquidate real shares) — BUYs and protective
        # exits are unaffected. See _detect_capital_below_deployed.
        self._capital_below_deployed: bool = False

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
        # v5 three-phase trailing stop (all None = disabled; v3 unchanged).
        self._trailing_enabled = (
            strategy.risk.per_trade.trailing_trigger_pct is not None
            and strategy.risk.per_trade.trailing_atr_multiplier is not None
        )
        self._trailing_managers: dict[str, TrailingStopManager] = {}
        self._gtt_ids: dict[str, int] = {}
        self._ws_hwm: dict[str, float] = {}
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

        # ASETPLTFRM-383 — preload 250 closed daily bars per ticker
        # from stocks.ohlcv (Iceberg) so the very first per-minute
        # eval sees the same indicator landscape as the backtest.
        # Today's running bar is appended lazily on the first
        # ``_on_bar_close`` for each ticker via
        # ``initial_running_bar``. Fail-soft: any error degrades to
        # the pre-383 empty-history behaviour (strategy silent-skips
        # until indicators settle).
        #
        # Scope: the full strategy evaluation universe
        # (``_bucket_by_ticker``, 712 tickers from universe_snapshot)
        # NOT just ``allowed_tickers`` (portfolio/watchlist, ~8).
        # Reason: ``preload_daily_bars`` issues ONE bulk DuckDB query
        # for all tickers — 8 vs 712 is the same round-trip — and
        # preloading the full universe here eliminates 700+ sequential
        # Kite API calls that would otherwise block the drain loop
        # in ``_on_bar_close`` at runtime, most critically at the
        # 15:25 IST daily bar-close when unpreloaded tickers all fire
        # simultaneously.
        #
        # ASETPLTFRM-393 — for intraday cadences (15m / 5m / 1m) we
        # route through ``preload_intraday_bars`` instead, reading
        # from ``algo.intraday_bars`` and falling back to Kite. The
        # daily path is preserved bit-for-bit for ``interval="1d"``.
        allowed_for_preload = caps.get("allowed_tickers") or []
        # Full evaluation universe: bucket cache (712 strategy-
        # eligible tickers) plus any allowed_tickers not already
        # covered. Falls back to allowed_tickers-only if the bucket
        # cache is empty (fresh install / missing universe_snapshot).
        universe_for_preload: list[str] = list(
            set(self._bucket_by_ticker.keys()) | set(allowed_for_preload)
        ) or list(allowed_for_preload)
        interval = strategy.schedule.interval
        if universe_for_preload and interval == "1d":
            try:
                from backend.algo.live.daily_bar_warmup import (
                    preload_daily_bars,
                )

                preloaded = preload_daily_bars(
                    universe_for_preload,
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
            # Bulk factor cache — replace 712 per-ticker lazy DuckDB
            # reads (each blocking the async event loop) with one
            # single batch query at startup.
            try:
                from datetime import date as _date_t
                from datetime import timedelta as _tdelta

                from backend.algo.factors.repo import get_factors_window

                _today = _date_t.today()
                _factor_rows = get_factors_window(
                    list(universe_for_preload),
                    _today - _tdelta(days=400),
                    _today + _tdelta(days=1),
                )
                for _fr in _factor_rows:
                    self._factor_cache[(_fr.ticker, _fr.bar_date)] = {
                        k: Decimal(str(v))
                        for k, v in _fr.values.items()
                        if v is not None
                    }
                self._factor_loaded_for_ticker.update(universe_for_preload)
                _logger.info(
                    "LiveRuntime: factor cache bulk-loaded — %d rows"
                    " for %d tickers",
                    len(_factor_rows),
                    len(universe_for_preload),
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "LiveRuntime: factor cache bulk-load failed: %s"
                    " — falling back to per-ticker lazy load",
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

        # PR3 — periodic algo.events flush task. Started in run(),
        # cancelled in its finally: before the terminal flush.
        self._event_flush_task: asyncio.Task | None = None
        # v5 trailing stop — 15-min GTT ratchet task (trailing-
        # enabled strategies only). Started in run(), cancelled in
        # finally: before terminal flush.
        self._trailing_ratchet_task: asyncio.Task | None = None

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

    async def _flush_events_now(self) -> None:
        """Flush buffered events to algo.events immediately so
        the events panel sees signals + orders in real time.
        Without this the buffer only flushes at session end and
        the user-facing panel looks frozen during long live-ws
        sessions.

        Runs the Iceberg write in a thread so the asyncio event
        loop is not blocked — each commit takes ~1-2 s of I/O
        which would otherwise starve FastAPI health probes when
        712 tickers fire simultaneously at bar close.
        """
        if not self._events:
            return
        rows = self._events[:]
        self._events = []
        try:
            await asyncio.to_thread(flush_events, rows)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "in-session flush failed — events will land at "
                "session end",
                exc_info=True,
            )
            # Re-buffer so events aren't lost on transient failure.
            self._events = rows + self._events

    async def _periodic_event_flush(self) -> None:
        """Flush buffered ``algo.events`` rows on a fixed cadence so
        live-mode events reach the panel within a few seconds without a
        commit per signal (PR3). Runs until cancelled at teardown."""
        try:
            while True:
                await asyncio.sleep(_EVENT_FLUSH_INTERVAL_S)
                try:
                    await self._flush_events_now()
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "periodic event flush failed", exc_info=True
                    )
        except asyncio.CancelledError:
            raise

    # ----------------------------------------------------------
    # Per-ticker cap helpers
    # ----------------------------------------------------------

    def _sync_ticker_lock_to_redis(self) -> None:
        """Mirror ``_ticker_locked`` to Redis so the lock survives a
        backend restart within the same trading session (TTL = 24h)."""
        try:
            from backend.cache import get_cache

            c = get_cache()
            key = (
                f"cache:algo:live:locked:"
                f"{self._user_id}:{self._strategy.id}"
            )
            if self._ticker_locked:
                c.set(
                    key,
                    ",".join(sorted(self._ticker_locked)),
                    ttl=86400,
                )
            else:
                c.delete(key)
        except Exception:  # noqa: BLE001
            _logger.warning("ticker lock Redis sync failed", exc_info=True)

    def _restore_ticker_locks_from_redis(self) -> set[str]:
        """Load any tickers locked in a prior session from Redis."""
        try:
            from backend.cache import get_cache

            c = get_cache()
            key = (
                f"cache:algo:live:locked:"
                f"{self._user_id}:{self._strategy.id}"
            )
            val = c.get(key)
            if val and isinstance(val, str):
                return {t.strip() for t in val.split(",") if t.strip()}
        except Exception:  # noqa: BLE001
            _logger.warning(
                "ticker lock Redis restore failed", exc_info=True
            )
        return set()

    async def _restore_ticker_locks_from_pg(self) -> set[str]:
        """Durable PG fallback: reads ``locked_tickers`` + unfinished
        BUY in-flight entries from the previous run for this strategy.
        Consulted when Redis is empty (e.g. after FLUSHALL or Redis
        restart), ensuring the per-ticker cap survives a full cold boot."""
        try:
            return await self._caps_repo.get_locked_tickers_from_previous_run(
                self._user_id,
                self._strategy.id,
                self._run_id,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "ticker lock PG restore failed", exc_info=True
            )
            return set()

    async def _periodic_ticker_lock_flush(self) -> None:
        """Persist ``_ticker_locked`` to ``algo.runs.locked_tickers``
        every 30 seconds for durability and SQL observability.
        Cancelled at session teardown; final flush done inline."""
        try:
            while True:
                await asyncio.sleep(30)
                try:
                    await self._caps_repo.update_locked_tickers(
                        self._user_id,
                        self._run_id,
                        self._ticker_locked,
                    )
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "ticker lock PG flush failed", exc_info=True
                    )
        except asyncio.CancelledError:
            raise

    async def _sync_fills_from_pg(self) -> None:
        """Apply fills confirmed by the Kite postback webhook to the
        in-memory position tracker.

        The postback route updates ``algo.runs.live_orders_in_flight``
        in PG (status → 'filled') and emits ``order_filled_live`` to
        Iceberg, but it has no reference to this LiveRuntime instance.
        Without this sync the in-memory ``_positions`` never sees the
        close, so the signal engine regenerates a SELL on every
        subsequent bar for an already-gone holding.

        Runs every 30 s inside ``_periodic_budget_reconcile``.
        """
        try:
            pg_entries = await self._caps_repo.get_in_flight(
                self._user_id, self._run_id,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "fill-sync: get_in_flight failed", exc_info=True,
            )
            return

        from backend.algo.backtest.types import Fill
        from datetime import datetime as _dt, timezone as _tz

        in_flight_index = {
            e["kite_order_id"]: e
            for e in self._in_flight
            if e.get("kite_order_id")
        }

        for pg_entry in pg_entries:
            if pg_entry.get("status") != "filled":
                continue
            kid = pg_entry.get("kite_order_id")
            if not kid:
                continue
            mem_entry = in_flight_index.get(kid)
            if mem_entry and mem_entry.get("status") == "filled":
                continue  # already applied in this session
            side = pg_entry.get("side", "")
            sym = pg_entry.get("symbol", "")
            ticker = f"{sym}.NS" if not sym.endswith(".NS") else sym
            qty = int(pg_entry.get("qty") or pg_entry.get("fill_qty") or 0)
            fill_price_raw = pg_entry.get("fill_price") or 0
            fill_price = (
                Decimal(str(fill_price_raw))
                if fill_price_raw
                else Decimal("0")
            )
            if qty <= 0:
                continue
            if side not in ("BUY", "SELL"):
                continue
            # Preserve the REAL fill date so opened_at (→ time-stop
            # holding-days) survives a restart. The webhook stamps
            # ``filled_at`` on the in-flight entry at fill time;
            # ``submitted_at`` is the fallback. Only default to today
            # when neither is present/parseable.
            orig_date = _parse_fill_date(
                pg_entry.get("filled_at"),
                pg_entry.get("submitted_at"),
            )
            if orig_date is None:
                orig_date = _dt.now(_tz.utc).date()
            fill = Fill(
                intent_id=uuid4(),
                ticker=ticker,
                side=side,  # type: ignore[arg-type]
                qty=qty,
                fill_price=fill_price,
                fill_date=orig_date,
                fees_inr=Decimal("0"),
                fee_rates_version="postback_sync",
            )
            self._positions.apply_fill(fill)
            # Unlock the ticker on SELL so re-entry is possible
            if side == "SELL":
                self._ticker_locked.discard(ticker)
                self._ticker_locked.discard(sym)
            else:
                self._ticker_locked.add(ticker)
            # Mirror status into in-memory _in_flight so next sync skips it
            if mem_entry is not None:
                mem_entry["status"] = "filled"

            # Place the protective GTT / trailing manager exactly as the
            # webhook postback does. Without this a BUY synced here (postback
            # miss / mid-session restart) is left NAKED — no stop — for the
            # rest of the session. on_buy_fill_trailing is sync, self-gates on
            # _trailing_enabled, and is idempotent on _trailing_managers; it
            # does blocking Kite I/O so run it off the event loop.
            if (
                side == "BUY"
                and self._trailing_enabled
                and ticker not in self._trailing_managers
            ):
                try:
                    await asyncio.to_thread(
                        self.on_buy_fill_trailing,
                        ticker=ticker,
                        fill_price=float(fill_price),
                        qty=qty,
                    )
                except Exception:  # noqa: BLE001
                    _logger.error(
                        "fill-sync: protective GTT init failed for %s "
                        "— position may be unprotected until ratchet",
                        ticker, exc_info=True,
                    )

            # Transition the budget reservation to FILLED immediately.
            # reservation_id was stored in the in-flight entry at submit
            # time so we don't need a separate DB lookup. Without this,
            # the only FILLED transition was reconcile_one() polling Kite
            # API — which drops history after 1 trading day, causing the
            # reservation to be TIMEOUT'd if the runtime restarted before
            # the 60s poll could fire.
            res_id_raw = pg_entry.get("reservation_id")
            if res_id_raw:
                try:
                    from uuid import UUID as _UUID
                    from backend.algo.live.budget_types import (
                        ReservationState as _RS,
                    )
                    filled_inr = fill_price * Decimal(str(qty))
                    await budget_transition(
                        reservation_id=_UUID(res_id_raw),
                        new_state=_RS.FILLED,
                        filled_qty=qty,
                        filled_inr=filled_inr,
                    )
                    _logger.info(
                        "fill-sync: budget FILLED res=%s sym=%s "
                        "qty=%d filled_inr=%.2f",
                        res_id_raw, sym, qty, filled_inr,
                    )
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "fill-sync: budget FILLED transition failed "
                        "res=%s sym=%s — reconciler will catch it",
                        res_id_raw, sym, exc_info=True,
                    )

            _logger.info(
                "fill-sync applied postback fill: sym=%s side=%s "
                "qty=%d @₹%s kite_order_id=%s",
                sym, side, qty, fill_price, kid,
            )
        self._sync_ticker_lock_to_redis()

    async def _periodic_budget_reconcile(self) -> None:
        """Reconcile SUBMITTED/PENDING budget reservations every 60 s,
        and sync postback fills to the in-memory position tracker every
        30 s (half-interval) to prevent duplicate signals on filled legs.
        """
        _INTERVAL_S = 60
        _tick = 0
        try:
            while True:
                await asyncio.sleep(_INTERVAL_S // 2)
                _tick += 1
                # Sync fills on every half-tick (every 30 s).
                try:
                    await self._sync_fills_from_pg()
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "periodic fill-sync failed", exc_info=True
                    )
                # Full budget reconcile on every full tick (every 60 s).
                if _tick % 2 == 0:
                    try:
                        from backend.algo.live.budget_reconciliation import (
                            reconcile as _budget_reconcile,
                        )

                        await _budget_reconcile()
                    except Exception:  # noqa: BLE001
                        _logger.warning(
                            "periodic budget reconcile failed", exc_info=True
                        )
        except asyncio.CancelledError:
            raise

    def _per_bar_sync_reads(
        self,
        *,
        ticker: str,
        bar_date_obj: date,
        history: list[Any],
        cadence: str,
    ) -> None:
        """Sync Iceberg reads + feature emit for one bar.

        Called via ``asyncio.to_thread`` from ``_on_bar_close`` so
        the event-loop tick drain is not blocked. Must complete before
        ``assemble_per_bar_features`` (sequential ``await``).

        FE-10: feature emission failure is non-fatal (try/except kept
        as today — ticker logged, bar continues).
        REGIME-2a: factor / regime / overlay caches are idempotent +
        O(1) after first load so repeated calls are cheap.
        """
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

            if cadence in _INTERVAL_SEC_BY_LABEL:
                emit_features_for_bar(
                    ticker=ticker,
                    interval_sec=_INTERVAL_SEC_BY_LABEL[cadence],
                    history=history,
                    cadence_interval=cadence,
                    mode="live",
                )
        except Exception:
            _logger.exception(
                "[live] FE-10 feature emission hook failed "
                "(non-fatal): ticker=%s",
                ticker,
            )
        # REGIME-2a — lazy-load cached factor rows for this
        # ticker on first sight; subsequent bars are O(1).
        self._ensure_factor_cache(ticker, bar_date_obj)
        self._ensure_regime_cache(bar_date_obj)
        self._ensure_daily_overlay_cache(ticker, bar_date_obj)

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

    # ── v5 GTT trailing stop — 15-min ratchet ────────────────────

    async def _trailing_ratchet_loop(self) -> None:
        """Every 15 min during market hours: ratchet GTTs.

        Aligned to 15m bar boundaries starting 09:15 IST.
        Stops evaluating at 15:25 IST (strategy time-stop fires
        before then to close all positions anyway).
        """
        _MARKET_OPEN = (9, 15)
        _MARKET_CLOSE = (15, 25)
        _INTERVAL_MIN = 15

        while True:
            try:
                now_ist = datetime.now(IST)
                h, m = now_ist.hour, now_ist.minute

                after_open = (h, m) >= _MARKET_OPEN
                before_close = (h, m) < _MARKET_CLOSE
                if not (after_open and before_close):
                    await asyncio.sleep(60)
                    continue

                minutes_past = (
                    (m - _MARKET_OPEN[1]) % _INTERVAL_MIN
                )
                wait_s = (
                    (_INTERVAL_MIN - minutes_past) * 60
                    - now_ist.second
                )
                if wait_s > 0:
                    await asyncio.sleep(wait_s)

                if not self._trailing_enabled:
                    continue

                await asyncio.to_thread(self._ratchet_all_gtts)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                _logger.error(
                    "trailing ratchet loop error: %s",
                    exc, exc_info=True,
                )
                await asyncio.sleep(30)

    def _ratchet_all_gtts(self) -> None:
        """Sync: evaluate all trailing managers against WS HWM.

        Called from ``_trailing_ratchet_loop`` via
        ``asyncio.to_thread``. Updates GTTs when stop ratchets up.
        Places an emergency limit sell if STOP_HIT is detected via
        the WS HWM (i.e. the GTT fired but the postback hasn't
        arrived yet, or the GTT missed).
        """
        for ticker, mgr in list(self._trailing_managers.items()):
            pos = self._positions.open_positions().get(ticker)
            if pos is None or pos.qty <= 0:
                self._trailing_managers.pop(ticker, None)
                continue

            hwm_price = self._ws_hwm.get(ticker, 0.0)
            if hwm_price <= 0:
                continue

            old_stop = mgr.current_stop
            event = mgr.on_price_update(hwm_price)

            if event is None:
                continue

            if event.event_type == "STOP_UPDATED":
                old_gtt_id = self._gtt_ids.get(ticker)
                stop = mgr.current_stop
                limit = stop * (
                    1.0 - self._gtt_limit_headroom_pct
                )
                try:
                    if old_gtt_id:
                        self._kite.delete_gtt(old_gtt_id)
                    new_id = self._kite.place_gtt(
                        ticker=ticker,
                        trigger_price=stop,
                        limit_price=limit,
                        qty=pos.qty,
                        last_price=hwm_price,
                    )
                    self._gtt_ids[ticker] = new_id
                    self._save_trailing_state(ticker, mgr, new_id)
                    self._events.append(
                        event_row(
                            session_id=self._session_id,
                            user_id=self._user_id,
                            strategy_id=self._strategy.id,
                            mode="live",
                            type_="gtt_ratcheted",
                            payload={
                                "ticker": ticker,
                                "phase": event.phase.value,
                                "old_stop": old_stop,
                                "new_stop": stop,
                                "hwm": event.hwm,
                                "gtt_id_old": old_gtt_id,
                                "gtt_id_new": new_id,
                                "dry_run": self._dry_run,
                            },
                        )
                    )
                    _logger.info(
                        "trailing: ratcheted GTT %s "
                        "%.4f → %.4f (phase %d) dry=%s",
                        ticker, old_stop, stop,
                        event.phase.value, self._dry_run,
                    )
                except Exception as exc:
                    _logger.error(
                        "trailing: ratchet GTT failed for %s: %s",
                        ticker, exc, exc_info=True,
                    )

            elif event.event_type == "STOP_HIT":
                _logger.warning(
                    "trailing: STOP_HIT via WS HWM for %s "
                    "— GTT may not have fired; emergency SELL",
                    ticker,
                )
                old_gtt_id = self._gtt_ids.pop(ticker, None)
                if old_gtt_id:
                    self._kite.delete_gtt(old_gtt_id)
                raw = ticker.removesuffix(
                    ".NS"
                ).removesuffix(".BO")
                try:
                    self._kite.place_order(
                        tradingsymbol=raw,
                        exchange="NSE",
                        transaction_type="SELL",
                        quantity=pos.qty,
                        order_type="LIMIT",
                        price=mgr.current_stop,
                        product=self._strategy.product or "CNC",
                    )
                except Exception as exc:
                    _logger.error(
                        "trailing: emergency SELL failed for "
                        "%s: %s",
                        ticker, exc, exc_info=True,
                    )
                self._trailing_managers.pop(ticker, None)

    # ── v5 GTT trailing stop ─────────────────────────────────────

    def _on_sell_fill_trailing(
        self,
        ticker: str,
        *,
        reason: str | None = None,
    ) -> None:
        """Clear trailing state when a position-closing SELL fills.

        Called from the postback handler on COMPLETE SELL.

        ``reason`` is the signal reason stored in the in-flight entry.
        A ``set_target_weight`` SELL is a rebalancing *trim* (partial
        reduce to fit the 20% weight), NOT a full position close.
        For trims the GTT and trailing state must be preserved so the
        remaining shares stay protected. Only exit/stop_loss/None (GTT
        trigger postback has no in-flight entry) → full close cleanup.

        Safe to call even when no trailing state exists for the ticker.
        A GTT-cancel failure must NOT abort the rest of the cleanup
        (best-effort, logged with ``exc_info``).
        """
        if not self._trailing_enabled:
            return
        if reason == "set_target_weight":
            # Rebalancing trim: position is NOT fully closed.
            # Keep GTT + trailing manager active to protect what remains.
            _logger.info(
                "trailing: set_target_weight trim for %s — "
                "preserving GTT/trailing state (not a full close); "
                "gtt_id=%s",
                ticker, self._gtt_ids.get(ticker),
            )
            return
        # Full close (exit, stop_loss, GTT fire, panic-close, etc.)
        # Capture the gtt_id BEFORE popping so we can cancel it.
        gtt_id = self._gtt_ids.get(ticker)
        self._trailing_managers.pop(ticker, None)
        self._gtt_ids.pop(ticker, None)
        self._ws_hwm.pop(ticker, None)
        try:
            from backend.cache import get_cache
            get_cache().invalidate_exact(
                f"trailing:{self._user_id}:"
                f"{self._strategy.id}:{ticker}"
            )
        except Exception:  # noqa: BLE001
            pass
        if gtt_id:
            try:
                self._kite.delete_gtt(gtt_id)
            except Exception as exc:
                _logger.warning(
                    "trailing: delete_gtt %s failed on SELL close "
                    "for %s: %s",
                    gtt_id, ticker, exc, exc_info=True,
                )
        self._ticker_locked.discard(ticker)
        self._sync_ticker_lock_to_redis()
        _logger.info(
            "trailing: state cleared for %s after SELL fill "
            "(gtt_id=%s, lock released)", ticker, gtt_id,
        )

    def on_buy_fill_trailing(
        self,
        *,
        ticker: str,
        fill_price: float,
        qty: int,
    ) -> None:
        """Initialise TrailingStopManager + place GTT after BUY fill.

        Called by the postback route when a COMPLETE BUY arrives for
        a trailing-enabled strategy. Safe to call from any thread —
        the only shared state is the in-memory dicts (no await).
        """
        if not self._trailing_enabled:
            return
        today = datetime.now(timezone.utc).date()
        _atr_raw = next(
            (
                self._factor_cache.get((ticker, today - timedelta(days=n)))
                for n in range(8)
                if (today - timedelta(days=n)).weekday() < 5
                and self._factor_cache.get((ticker, today - timedelta(days=n)))
            ),
            {},
        )
        atr = float(_atr_raw.get("atr_14", 0.0))
        if atr <= 0:
            _bars = self._bars_by_ticker.get(ticker, [])
            if len(_bars) >= 2:
                _atr_series = _wilder_atr(_bars, 14)
                atr = float(
                    _atr_series[-1]
                    if _atr_series and _atr_series[-1] is not None
                    else 0.0
                )
        if atr <= 0:
            atr = fill_price * 0.02
            _logger.warning(
                "trailing: atr_14 missing for %s — "
                "using 2%% price proxy atr=%.4f; "
                "phase-3 trail may be imprecise",
                ticker, atr,
            )
        mgr = TrailingStopManager(
            self._strategy.risk.per_trade,
            entry_price=fill_price,
            atr=atr,
            ticker=ticker,
        )
        stop = mgr.current_stop
        limit = stop * (1.0 - self._gtt_limit_headroom_pct)
        try:
            gtt_id = self._kite.place_gtt(
                ticker=ticker,
                trigger_price=stop,
                limit_price=limit,
                qty=qty,
                last_price=fill_price,
            )
        except Exception as exc:
            _logger.error(
                "trailing: place_gtt failed for %s: %s",
                ticker, exc, exc_info=True,
            )
            return
        self._trailing_managers[ticker] = mgr
        self._gtt_ids[ticker] = gtt_id
        self._ws_hwm[ticker] = fill_price
        self._save_trailing_state(ticker, mgr, gtt_id)
        self._events.append(
            event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="live",
                type_="gtt_placed",
                payload={
                    "ticker": ticker,
                    "phase": 1,
                    "entry_price": fill_price,
                    "stop_price": stop,
                    "limit_price": limit,
                    "gtt_id": gtt_id,
                    "atr": atr,
                    "dry_run": self._dry_run,
                },
            )
        )
        _logger.info(
            "trailing: placed GTT %d for %s stop=%.4f "
            "(dry_run=%s)",
            gtt_id, ticker, stop, self._dry_run,
        )

    def _save_trailing_state(
        self,
        ticker: str,
        mgr: TrailingStopManager,
        gtt_id: int,
    ) -> None:
        """Persist trailing manager state to Redis (TTL = 48 h)."""
        try:
            import json as _json
            from backend.cache import get_cache
            key = (
                f"trailing:{self._user_id}:"
                f"{self._strategy.id}:{ticker}"
            )
            data = mgr.to_dict()
            data["gtt_id"] = gtt_id
            get_cache().set(key, _json.dumps(data), ttl=172800)
        except Exception as exc:
            _logger.warning(
                "trailing: Redis save failed for %s: %s",
                ticker, exc, exc_info=True,
            )

    def _load_trailing_state_from_redis(self) -> None:
        """On restart: reload all trailing managers from Redis.

        Called once at the top of ``run()`` after ticker-lock restore.
        Silently skips tickers with no Redis entry or with corrupt data.
        """
        if not self._trailing_enabled:
            return
        try:
            import json as _json
            from backend.cache import get_cache
            cache = get_cache()
            prefix = (
                f"trailing:{self._user_id}:{self._strategy.id}:"
            )
            # Scan both hydrated positions AND locked tickers so a restart
            # that missed the Kite positions() API still recovers trailing
            # managers for all previously-filled positions.
            _restore_set = (
                set(self._positions.open_positions().keys())
                | self._ticker_locked
            )
            for ticker in list(_restore_set):
                raw = cache.get(f"{prefix}{ticker}")
                if raw is None:
                    continue
                data = _json.loads(raw)
                gtt_id = int(data.pop("gtt_id", 0))
                if not gtt_id:
                    continue
                mgr = TrailingStopManager.from_dict(
                    data, self._strategy.risk.per_trade,
                )
                self._trailing_managers[ticker] = mgr
                self._gtt_ids[ticker] = gtt_id
                self._ws_hwm[ticker] = mgr.state.hwm
                _logger.info(
                    "trailing: restored %s from Redis "
                    "phase=%d stop=%.4f gtt_id=%d",
                    ticker, mgr.state.phase.value,
                    mgr.current_stop, gtt_id,
                )
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="live",
                        type_="trailing_stop_recovered",
                        payload={
                            "ticker": ticker,
                            "phase": mgr.state.phase.value,
                            "hwm": mgr.state.hwm,
                            "current_stop": mgr.current_stop,
                            "gtt_id": gtt_id,
                        },
                    )
                )
        except Exception as exc:
            _logger.warning(
                "trailing: Redis restore failed: %s",
                exc, exc_info=True,
            )

    async def _recover_unhydrated_positions(self) -> None:
        """Re-inject positions that are ticker-locked but missed by hydration.

        Scenario: a BUY filled while the prior runtime was stopping — the
        Kite postback arrived after ``get_live_runtime`` returned None, so
        ``on_buy_fill_trailing`` was never called.  On the next restart
        ``positions()['net']`` can miss the intraday CNC fill (timing race),
        so the position never enters the tracker and ``ensure_gtts`` can't
        place its GTT.

        Recovery: for every ticker in ``_ticker_locked`` that is NOT in
        ``open_positions()``, look up the fill data from the previous run's
        ``live_orders_in_flight`` and inject a synthetic BUY fill.  This
        makes the position visible to ``_ensure_gtts_for_hydrated_positions``,
        which runs immediately after and places the missing GTT.
        """
        open_pos = self._positions.open_positions()
        locked_unhydrated = {
            t for t in self._ticker_locked
            if t not in open_pos
        }
        if not locked_unhydrated:
            return

        try:
            filled = await self._caps_repo.get_filled_buys_from_previous_runs(
                self._user_id, self._strategy.id, self._run_id
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "recover_positions: previous run query failed: %s",
                exc,
                exc_info=True,
            )
            return

        from backend.algo.backtest.types import Fill

        recovered = 0
        for ticker in locked_unhydrated:
            fd = filled.get(ticker)
            if not fd or fd["fill_price"] <= 0 or fd["qty"] <= 0:
                continue
            # Preserve the REAL open date so the time-stop holding-day
            # count survives the restart. Fall back to today only when
            # the previous run's in-flight entry carried no parseable
            # fill/submit timestamp.
            open_date = fd.get("fill_date") or _parse_fill_date(
                fd.get("filled_at"), fd.get("submitted_at"),
            )
            if open_date is None:
                open_date = datetime.now(timezone.utc).date()
            self._positions.apply_fill(
                Fill(
                    intent_id=uuid4(),
                    ticker=ticker,
                    side="BUY",
                    qty=fd["qty"],
                    fill_price=Decimal(str(fd["fill_price"])),
                    fill_date=open_date,
                    fees_inr=Decimal("0"),
                    fee_rates_version="recovered",
                )
            )
            _logger.info(
                "recover_positions: re-injected %s qty=%d avg=%.4f "
                "opened_at=%s (locked-but-not-hydrated; prev run "
                "in_flight)",
                ticker,
                fd["qty"],
                float(fd["fill_price"]),
                open_date.isoformat(),
            )
            recovered += 1

        if recovered:
            _logger.info(
                "recover_positions: %d position(s) re-injected",
                recovered,
            )

    async def _ensure_gtts_for_hydrated_positions(self) -> None:
        """Verify every HELD position's GTT against the LIVE Kite book.

        Called once in ``run()`` after ``_load_trailing_state_from_redis()``.
        Kite's active GTT book is the SOURCE OF TRUTH — a Redis-restored
        ``gtt_id`` is NOT proof a GTT exists. This is a real-money safety
        check: a held position whose GTT is dead on Kite would otherwise be
        silently believed-protected.

        For every held position (``open_positions()`` with ``qty > 0``),
        regardless of whether the ticker is already in
        ``_trailing_managers``:
          - If Kite has an active GTT for the bare symbol → the position IS
            protected. Register/correct the manager to the REAL Kite
            ``gtt_id`` (trust Kite over Redis); never place a duplicate.
          - If Kite has NO active GTT → the position is UNPROTECTED (even if
            Redis had a manager/id). Place a fresh GTT (entry=avg_price, ATR
            from factor cache / Wilder fallback / 2% proxy) and register it.
            A zero/negative ``avg_price`` is refused (Task 3.2 preserved).

        Kite READ FAILURE is NOT "no GTTs": if ``get_gtts`` raises we log a
        loud WARNING, emit ``gtt_verification_failed``, and return early
        leaving existing managers intact — placing on an unreadable book
        would create duplicate GTTs.

        Source tag ``hydrated_algo`` / ``hydrated_manual`` is logged and
        emitted in the ``gtt_placed`` event.
        """
        if not self._trailing_enabled:
            return

        open_pos = self._positions.open_positions()
        held = {
            t: p
            for t, p in open_pos.items()
            if p.qty > 0
        }
        if not held:
            return

        _logger.info(
            "ensure_gtts: verifying %d held position(s) vs Kite: %s",
            len(held), list(held),
        )

        # Batch-fetch active GTTs from Kite once. A READ FAILURE must NOT
        # masquerade as "no GTTs" (that would place duplicates). Fail
        # VISIBLE: warn loudly, emit an event, and bail with managers
        # intact.
        try:
            all_gtts: list[dict] = await asyncio.to_thread(
                self._kite.get_gtts
            )
        except Exception as exc:
            _logger.warning(
                "ensure_gtts: get_gtts FAILED — cannot verify held "
                "positions against Kite; leaving %d manager(s) intact, "
                "placing nothing (avoids duplicate GTTs): %s",
                len(self._trailing_managers), exc, exc_info=True,
            )
            self._events.append(
                event_row(
                    session_id=self._session_id,
                    user_id=self._user_id,
                    strategy_id=self._strategy.id,
                    mode="live",
                    type_="gtt_verification_failed",
                    payload={
                        "error": str(exc),
                        "held_tickers": list(held),
                        "managers_preserved": list(
                            self._trailing_managers
                        ),
                    },
                )
            )
            return

        # bare_symbol (no suffix) → (gtt_id, trigger_price)
        kite_gtt_map: dict[str, tuple[int, float]] = {}
        for _g in all_gtts:
            if _g.get("status") != "active":
                continue
            _cond = _g.get("condition") or {}
            _sym = (_cond.get("tradingsymbol") or "").upper()
            _gid = _g.get("id") or 0
            _triggers = _cond.get("trigger_values") or []
            if _sym and _gid and _triggers:
                kite_gtt_map[_sym] = (int(_gid), float(_triggers[0]))

        # Batch-query algo.events to identify algo-placed positions.
        _algo_syms: set[str] = set()
        try:
            from backend.db.duckdb_engine import query_iceberg_table
            import json as _json
            _evt_rows = query_iceberg_table(
                "algo.events",
                "SELECT payload_json FROM events "
                "WHERE user_id = ? AND mode = 'live' "
                "  AND type = 'order_filled_live'",
                [str(self._user_id)],
            )
            for _r in _evt_rows:
                try:
                    _p = _json.loads(_r.get("payload_json") or "{}")
                    _s = (_p.get("symbol") or "").upper()
                    if _s:
                        _algo_syms.add(_s)
                except Exception:
                    pass
        except Exception as exc:
            _logger.debug(
                "ensure_gtts: events query failed: %s", exc
            )

        _today = datetime.now(timezone.utc).date()

        for ticker, pos in held.items():
            bare = (
                ticker.removesuffix(".NS").removesuffix(".BO").upper()
            )
            avg_price = float(pos.avg_price)
            qty = pos.qty
            source = (
                "hydrated_algo"
                if bare in _algo_syms
                else "hydrated_manual"
            )

            # ── Kite is SOURCE OF TRUTH ──────────────────────────────
            # If an active GTT exists on Kite for this held position it
            # IS protected. Register/correct the manager to the REAL
            # Kite gtt_id (trust Kite over a possibly-stale Redis id)
            # and place NOTHING — no duplicate GTTs. Reuse the
            # Redis-restored manager (phase/hwm) when present; else
            # build one at entry so the ratchet loop can manage it.
            existing = kite_gtt_map.get(bare)
            if existing:
                _existing_gtt_id, _existing_stop = existing
                mgr = self._trailing_managers.get(ticker)
                if mgr is None:
                    if avg_price <= 0:
                        # Can't build a manager off a ₹0 entry, but the
                        # position IS protected on Kite — record the id
                        # so the ratchet loop sees it; skip manager.
                        self._gtt_ids[ticker] = _existing_gtt_id
                        _logger.warning(
                            "ensure_gtts: %s protected by Kite GTT %d "
                            "but avg_price=%.4f — registered gtt_id "
                            "without a manager (manual review).",
                            ticker, _existing_gtt_id, avg_price,
                        )
                        continue
                    mgr = TrailingStopManager(
                        self._strategy.risk.per_trade,
                        entry_price=avg_price,
                        atr=avg_price * 0.02,
                        ticker=ticker,
                    )
                    _ltp = self._ws_hwm.get(ticker, avg_price)
                    if _ltp > avg_price:
                        mgr.on_price_update(_ltp)
                _prev_id = self._gtt_ids.get(ticker)
                self._trailing_managers[ticker] = mgr
                self._gtt_ids[ticker] = _existing_gtt_id
                self._ws_hwm.setdefault(ticker, avg_price)
                self._save_trailing_state(
                    ticker, mgr, _existing_gtt_id,
                )
                if _prev_id is not None and _prev_id != _existing_gtt_id:
                    _logger.warning(
                        "ensure_gtts: %s gtt_id corrected %d -> %d "
                        "(Redis was stale; Kite is truth)",
                        ticker, _prev_id, _existing_gtt_id,
                    )
                _logger.info(
                    "ensure_gtts: %s protected by live Kite GTT %d "
                    "kite_stop=%.4f phase=%d source=%s — no duplicate",
                    ticker, _existing_gtt_id, _existing_stop,
                    mgr.state.phase.value, source,
                )
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="live",
                        type_="trailing_gtt_verified",
                        payload={
                            "ticker": ticker,
                            "phase": mgr.state.phase.value,
                            "hwm": mgr.state.hwm,
                            "current_stop": mgr.current_stop,
                            "gtt_id": _existing_gtt_id,
                            "source": source,
                        },
                    )
                )
                continue

            # ── No active Kite GTT — position is UNPROTECTED ─────────
            # Place a fresh protective GTT (even if Redis had a manager
            # whose gtt_id is now dead on Kite).
            # REFUSE to place a GTT off a missing/zero entry price.
            # A ₹0 avg_price yields a ₹0 trigger/limit — a garbage
            # protective stop that would either never fire or fire
            # instantly. Emit a loud event so the unprotected position
            # is visible on the live panel instead of silently naked.
            if avg_price <= 0:
                _logger.error(
                    "ensure_gtts: REFUSING GTT for %s — entry price "
                    "is %.4f (missing/zero). Position left WITHOUT a "
                    "protective stop; manual review required.",
                    ticker, avg_price,
                )
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="live",
                        type_="gtt_skipped_no_entry_price",
                        payload={
                            "ticker": ticker,
                            "avg_price": avg_price,
                            "qty": qty,
                            "source": source,
                        },
                    )
                )
                continue

            # ATR — weekday-aware, same pattern as on_buy_fill_trailing.
            _atr_raw = next(
                (
                    self._factor_cache.get(
                        (ticker, _today - timedelta(days=_n))
                    )
                    for _n in range(8)
                    if (
                        _today - timedelta(days=_n)
                    ).weekday() < 5
                    and self._factor_cache.get(
                        (ticker, _today - timedelta(days=_n))
                    ) is not None
                ),
                {},
            )
            atr = float(_atr_raw.get("atr_14", 0.0))
            if atr <= 0:
                # Fallback: compute Wilder ATR(14) from preloaded daily bars.
                _bars = self._bars_by_ticker.get(ticker, [])
                if len(_bars) >= 2:
                    _atr_series = _wilder_atr(_bars, 14)
                    atr = float(
                        _atr_series[-1]
                        if _atr_series and _atr_series[-1] is not None
                        else 0.0
                    )
            if atr <= 0:
                # Last resort: 2% of entry price (phase-0 GTT still placed;
                # phase-3 ATR trail will be imprecise but better than no GTT).
                atr = avg_price * 0.02
                _logger.warning(
                    "ensure_gtts: atr_14 missing for %s — "
                    "using 2%% price proxy atr=%.4f; "
                    "phase-3 trail may be imprecise",
                    ticker, atr,
                )

            # Build manager at entry; advance phase via LTP if known.
            mgr = TrailingStopManager(
                self._strategy.risk.per_trade,
                entry_price=avg_price,
                atr=atr,
                ticker=ticker,
            )
            ltp = self._ws_hwm.get(ticker, avg_price)
            if ltp > avg_price:
                mgr.on_price_update(ltp)

            stop = mgr.current_stop
            limit = stop * (1.0 - self._gtt_limit_headroom_pct)

            # No GTT on Kite — place a fresh one.
            # ltp captured in closure so lambda binds the right value
            # per iteration (ticker-level variable, not loop-level).
            _ltp_snap = ltp
            try:
                gtt_id = await asyncio.to_thread(
                    lambda: self._kite.place_gtt(
                        ticker=ticker,
                        trigger_price=stop,
                        limit_price=limit,
                        qty=qty,
                        last_price=_ltp_snap,
                    )
                )
            except Exception as exc:
                _logger.error(
                    "ensure_gtts: place_gtt failed for %s: %s",
                    ticker, exc, exc_info=True,
                )
                continue

            self._trailing_managers[ticker] = mgr
            self._gtt_ids[ticker] = gtt_id
            self._ws_hwm.setdefault(ticker, avg_price)
            self._save_trailing_state(ticker, mgr, gtt_id)
            self._events.append(
                event_row(
                    session_id=self._session_id,
                    user_id=self._user_id,
                    strategy_id=self._strategy.id,
                    mode="live",
                    type_="gtt_placed",
                    payload={
                        "ticker": ticker,
                        "phase": mgr.state.phase.value,
                        "entry_price": avg_price,
                        "stop_price": stop,
                        "limit_price": limit,
                        "gtt_id": gtt_id,
                        "atr": atr,
                        "dry_run": self._dry_run,
                        "source": source,
                    },
                )
            )
            _logger.info(
                "ensure_gtts: placed GTT %d for %s stop=%.4f "
                "phase=%d source=%s (dry_run=%s)",
                gtt_id, ticker, stop,
                mgr.state.phase.value, source, self._dry_run,
            )

    def _broker_really_held(self) -> set[str] | None:
        """Tickers with ANY real exposure across ALL broker sources.

        The load-bearing safety set for close-time / hydration GTT
        cleanup. A ticker is HELD if it shows exposure in EITHER:
          * ``positions().net`` row with ``quantity != 0`` — this
            catches CNC buys made TODAY (they live in net, NOT in
            holdings until T+1), the exact bug a holdings()-only
            check caused earlier.
          * ``holdings`` row with ``quantity > 0 OR t1_quantity > 0``
            — settled or T+1-pending delivery equity.

        Returns the union as internal ``.NS`` tickers, or ``None`` if
        EITHER broker read raises / is unavailable. ``None`` means
        UNKNOWN -> the caller MUST clean nothing (fail safe). Reuses
        the ``kite._kc.positions()/holdings()`` access pattern from
        ``position_hydration.hydrate``.
        """
        kc = getattr(self._kite, "_kc", None)
        if kc is None:
            _logger.warning(
                "cleanup: kite._kc unavailable — held set UNKNOWN",
            )
            return None
        try:
            raw_pos = kc.positions()
            raw_hold = kc.holdings()
        except Exception as exc:
            _logger.warning(
                "cleanup: broker positions/holdings read failed — "
                "held set UNKNOWN (%s)", exc, exc_info=True,
            )
            return None

        held: set[str] = set()

        net = (
            raw_pos.get("net", [])
            if isinstance(raw_pos, dict) else []
        )
        for r in net:
            qty = _to_int(r.get("quantity"))
            if qty == 0:
                continue
            sym = (r.get("tradingsymbol") or "").strip()
            if sym:
                held.add(_as_ns(sym))

        rows = raw_hold if isinstance(raw_hold, list) else []
        for r in rows:
            settled = _to_int(r.get("quantity"))
            t1 = _to_int(r.get("t1_quantity"))
            if settled <= 0 and t1 <= 0:
                continue
            sym = (r.get("tradingsymbol") or "").strip()
            if sym:
                held.add(_as_ns(sym))

        return held

    async def _cleanup_stale_protection(self) -> None:
        """Cancel GTTs + clear lock/Redis for PROVABLY-GONE tickers.

        Called once in ``run()`` right AFTER
        ``_ensure_gtts_for_hydrated_positions``. Reconciles leftover
        protection (a lock / trailing manager / live Kite GTT) for
        tickers that NO LONGER have any real position — e.g. a SELL
        that filled while the prior runtime was down, so close-time
        cleanup never ran.

        GUARDRAIL (load-bearing, real-money): a ticker is cleaned ONLY
        if ``_broker_really_held`` proves it gone across ALL broker
        sources. If the broker read is UNKNOWN (``None``) we clean
        NOTHING and emit ``cleanup_skipped_broker_unreadable``.
        """
        if not self._trailing_enabled:
            return

        really_held = self._broker_really_held()
        if really_held is None:
            _logger.warning(
                "cleanup: broker unreadable — skipping stale "
                "protection cleanup (clean nothing, fail safe)",
            )
            self._events.append(
                event_row(
                    session_id=self._session_id,
                    user_id=self._user_id,
                    strategy_id=self._strategy.id,
                    mode="live",
                    type_="cleanup_skipped_broker_unreadable",
                    payload={
                        "candidates": sorted(
                            self._ticker_locked
                            | set(self._trailing_managers)
                        ),
                    },
                )
            )
            return

        # Active Kite GTT book: bare symbol -> gtt_id. Best-effort;
        # only used to find a GTT to cancel, NOT as a safety gate.
        gtt_by_bare: dict[str, int] = {}
        try:
            all_gtts = await asyncio.to_thread(self._kite.get_gtts)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "cleanup: get_gtts failed: %s", exc, exc_info=True,
            )
            all_gtts = []
        for _g in all_gtts or []:
            if _g.get("status") != "active":
                continue
            _cond = _g.get("condition") or {}
            _sym = (_cond.get("tradingsymbol") or "").upper()
            _gid = _g.get("id") or 0
            if _sym and _gid:
                gtt_by_bare[_sym] = int(_gid)

        candidates = (
            set(self._ticker_locked)
            | set(self._trailing_managers)
            | {_as_ns(s) for s in gtt_by_bare}
        )

        lock_changed = False
        for ticker in sorted(candidates):
            if ticker in really_held:
                continue
            bare = (
                ticker.removesuffix(".NS").removesuffix(".BO").upper()
            )
            gtt_id = self._gtt_ids.get(ticker) or gtt_by_bare.get(bare)
            if gtt_id:
                try:
                    self._kite.delete_gtt(gtt_id)
                except Exception as exc:
                    _logger.warning(
                        "cleanup: delete_gtt %s failed for %s: %s",
                        gtt_id, ticker, exc, exc_info=True,
                    )
            try:
                from backend.cache import get_cache
                get_cache().invalidate_exact(
                    f"trailing:{self._user_id}:"
                    f"{self._strategy.id}:{ticker}"
                )
            except Exception:  # noqa: BLE001
                pass
            self._trailing_managers.pop(ticker, None)
            self._gtt_ids.pop(ticker, None)
            self._ws_hwm.pop(ticker, None)
            if ticker in self._ticker_locked:
                self._ticker_locked.discard(ticker)
                lock_changed = True
            _logger.info(
                "cleanup: stale protection cleared for %s "
                "(provably gone; cancelled_gtt_id=%s)",
                ticker, gtt_id,
            )
            self._events.append(
                event_row(
                    session_id=self._session_id,
                    user_id=self._user_id,
                    strategy_id=self._strategy.id,
                    mode="live",
                    type_="stale_protection_cleaned",
                    payload={
                        "ticker": ticker,
                        "cancelled_gtt_id": gtt_id,
                        "reason": "provably_gone_no_broker_position",
                    },
                )
            )

        if lock_changed:
            self._sync_ticker_lock_to_redis()

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
        # PR3 — start the periodic algo.events flush (idempotent guard
        # so a re-entrant run() doesn't double-start). Cancelled in the
        # finally: block below before the terminal flush.
        if self._event_flush_task is None:
            self._event_flush_task = asyncio.create_task(
                self._periodic_event_flush(),
            )

        # Per-ticker cap: union of three sources at startup so the lock
        # set survives any restart scenario:
        #   1. Kite position hydration — catches overnight/filled positions.
        #   2. Redis — fast path for in-flight BUYs from the prior session.
        #   3. PG previous-run locked_tickers + unfinished in-flight JSONB
        #      — durable fallback when Redis is empty (FLUSHALL / cold boot).
        for ticker, pos in self._positions.open_positions().items():
            if pos.qty > 0:
                self._ticker_locked.add(ticker)
        self._ticker_locked.update(self._restore_ticker_locks_from_redis())
        self._ticker_locked.update(await self._restore_ticker_locks_from_pg())
        await self._recover_unhydrated_positions()
        self._load_trailing_state_from_redis()
        await self._ensure_gtts_for_hydrated_positions()
        await self._cleanup_stale_protection()
        # Task 4.0b — once positions are hydrated, detect a start where
        # the configured capital is below the already-deployed cost so
        # the set_target_weight branch can suppress liquidating trims.
        self._detect_capital_below_deployed()
        if self._ticker_locked:
            _logger.info(
                "LiveRuntime: ticker locks restored: %s",
                self._ticker_locked,
            )
        ticker_lock_flush_task = asyncio.create_task(
            self._periodic_ticker_lock_flush(),
            name=f"ticker_lock_flush_{self._run_id}",
        )
        budget_reconcile_task = asyncio.create_task(
            self._periodic_budget_reconcile(),
            name=f"budget_reconcile_{self._run_id}",
        )
        if self._trailing_enabled:
            self._trailing_ratchet_task = asyncio.create_task(
                self._trailing_ratchet_loop(),
                name=f"trailing_ratchet_{self._run_id}",
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
                # v5 trailing stop: lightweight HWM update per tick.
                if (
                    self._trailing_enabled
                    and tick.ticker in self._trailing_managers
                ):
                    _ltp = float(tick.ltp)
                    if _ltp > self._ws_hwm.get(tick.ticker, 0.0):
                        self._ws_hwm[tick.ticker] = _ltp
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
                        last_price_per_ticker=last_price_per_ticker,
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
                    last_price_per_ticker=last_price_per_ticker,
                )
            # Stop per-ticker lock flush before terminal drain.
            ticker_lock_flush_task.cancel()
            try:
                await ticker_lock_flush_task
            except (asyncio.CancelledError, Exception):
                pass
            # Final PG flush of locked tickers at session end.
            try:
                await self._caps_repo.update_locked_tickers(
                    self._user_id,
                    self._run_id,
                    self._ticker_locked,
                )
            except Exception:  # noqa: BLE001
                _logger.warning(
                    "final ticker lock PG flush failed", exc_info=True
                )
            # Stop and do a final budget reconcile sweep.
            budget_reconcile_task.cancel()
            try:
                await budget_reconcile_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                from backend.algo.live.budget_reconciliation import (
                    reconcile as _budget_reconcile,
                )

                await _budget_reconcile()
            except Exception:  # noqa: BLE001
                _logger.warning(
                    "final budget reconcile failed", exc_info=True
                )

            # v5 trailing stop: cancel the ratchet loop before
            # the event flush so any final ratchet events land
            # in self._events before terminal drain.
            if self._trailing_ratchet_task is not None:
                self._trailing_ratchet_task.cancel()
                try:
                    await self._trailing_ratchet_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._trailing_ratchet_task = None

            # PR3 — stop the periodic flush before the terminal drain
            # so they cannot race on self._events.
            if self._event_flush_task is not None:
                self._event_flush_task.cancel()
                try:
                    await self._event_flush_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._event_flush_task = None
            if self._events:
                _logger.info(
                    "LiveRuntime: flushing %d events to " "algo.events",
                    len(self._events),
                )
                await asyncio.to_thread(flush_events, self._events)
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
        last_price_per_ticker: dict[str, Decimal] | None = None,
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
            # Skip expensive Kite preload for tickers that are only
            # in the universe LTP subscription (indices, instruments
            # not in the strategy's evaluation universe). After a
            # full startup warmup, _bars_by_ticker already covers all
            # bucket_by_ticker entries; an absent entry here means a
            # pure LTP-cache token (e.g. an index) that cannot be
            # traded. Mark it seen-and-skip so this path is O(1) on
            # every subsequent bar for that token.
            if (
                strategy_interval == "1d"
                and bar.ticker not in self._bucket_by_ticker
                and bar.ticker
                not in self._positions.open_positions()
            ):
                self._bars_by_ticker[bar.ticker] = []
                return 0
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
            # High #15 — trim in place so the same list object remains
            # bound to _bars_by_ticker[ticker]; keeps the most-recent N
            # bars. Only on new-bucket append (NOT the in-place update
            # else branch below — that path never grows the list).
            if len(history) > _MAX_BAR_HISTORY:
                del history[: len(history) - _MAX_BAR_HISTORY]
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

        # ASETPLTFRM-383 (revised) — the daily eval-time gate is now
        # applied to the ENTRY DECISION only (see below, after the AST
        # eval), NOT as a blanket pre-eval skip. Indicators, features
        # and exits MUST run on every bar so stop-loss / time-stop fire
        # on time and a completed-bar entry can be acted on immediately.
        # The ALGO_DAILY_MIN_EVAL_TIME_IST gate only
        # defers a BUY that appears solely on today's still-forming
        # candle; replay is exempt (wall-clock is meaningless there).
        ind_map = compute_indicators(history)
        # FE-10 + REGIME-2a — offloaded to a worker thread so the
        # WS tick drain is not blocked by sync Iceberg reads.
        # Sequential await ensures caches are warm before the
        # assemble_per_bar_features call that follows.
        await asyncio.to_thread(
            self._per_bar_sync_reads,
            ticker=bar.ticker,
            bar_date_obj=bar_date_obj,
            history=history,
            cadence=self._strategy.schedule.interval,
        )
        # High #15 — evict stale _closed_entry_cache entries once per
        # bar-close. Cheap dict comprehension; guards unbounded growth.
        self._evict_stale_closed_entry_cache(as_of=bar_date_obj)
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
            market_regime=next(
                (
                    self._market_regime.get(bar_date_obj - timedelta(days=n))
                    for n in range(8)
                    if (bar_date_obj - timedelta(days=n)).weekday() < 5
                    and self._market_regime.get(bar_date_obj - timedelta(days=n))
                    is not None
                ),
                None,
            ),
            market_trend=next(
                (
                    self._market_trend.get(bar_date_obj - timedelta(days=n))
                    for n in range(8)
                    if (bar_date_obj - timedelta(days=n)).weekday() < 5
                    and self._market_trend.get(bar_date_obj - timedelta(days=n))
                    is not None
                ),
                None,
            ),
            factor_row=next(
                (
                    self._factor_cache.get(
                        (bar.ticker, bar_date_obj - timedelta(days=n))
                    )
                    for n in range(8)
                    if (bar_date_obj - timedelta(days=n)).weekday() < 5
                    and self._factor_cache.get(
                        (bar.ticker, bar_date_obj - timedelta(days=n))
                    )
                ),
                None,
            ),
            regime_row=next(
                (
                    self._regime_by_date.get(
                        bar_date_obj - timedelta(days=n)
                    )
                    for n in range(8)
                    if (bar_date_obj - timedelta(days=n)).weekday() < 5
                    and self._regime_by_date.get(
                        bar_date_obj - timedelta(days=n)
                    )
                ),
                None,
            ),
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
                # v5 trailing stop: cancel GTT before placing SELL
                # to prevent double exit (GTT fires + SELL fills).
                if self._trailing_enabled:
                    _gtt_id = self._gtt_ids.pop(trig.ticker, None)
                    if _gtt_id is not None:
                        try:
                            self._kite.delete_gtt(_gtt_id)
                        except Exception:  # noqa: BLE001
                            _logger.warning(
                                "time_stop: delete_gtt %d "
                                "failed for %s",
                                _gtt_id, trig.ticker,
                                exc_info=True,
                            )
                        self._events.append(
                            event_row(
                                session_id=self._session_id,
                                user_id=self._user_id,
                                strategy_id=self._strategy.id,
                                mode="live",
                                type_="gtt_cancelled_for_time_stop",
                                payload={
                                    "ticker": trig.ticker,
                                    "holding_days": trig.holding_days,
                                    "gtt_id": _gtt_id,
                                    "dry_run": self._dry_run,
                                },
                            )
                        )
                    self._trailing_managers.pop(trig.ticker, None)
                    self._ws_hwm.pop(trig.ticker, None)
                    try:
                        from backend.cache import get_cache
                        get_cache().invalidate_exact(
                            f"trailing:{self._user_id}:"
                            f"{self._strategy.id}:{trig.ticker}"
                        )
                    except Exception:  # noqa: BLE001
                        pass
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
        except KeyError as exc:
            _logger.warning(
                "eval_node KeyError ticker=%s date=%s missing_key=%s "
                "features=%s",
                bar.ticker, bar_date_obj, exc,
                {k: v for k, v in (features or {}).items()
                 if k in ("rsi_2", "distance_from_sma50", "distance_from_sma200",
                          "stress_prob", "nifty_above_sma200", "nifty_30d_return_pct")},
            )
            return 0

        _logger.info(
            "eval ticker=%s date=%s action=%s rsi2=%s sma50dist=%s sma200dist=%s "
            "stress_prob=%s nifty_sma200=%s nifty30d=%s",
            bar.ticker, bar_date_obj, action,
            (features or {}).get("rsi_2"),
            (features or {}).get("distance_from_sma50"),
            (features or {}).get("distance_from_sma200"),
            (features or {}).get("stress_prob"),
            (features or {}).get("nifty_above_sma200"),
            (features or {}).get("nifty_30d_return_pct"),
        )

        signal = self._action_to_signal(
            action,
            ticker=bar.ticker,
            bar_date_ns=bar.bar_open_ts_ns,
            last_price=last_price,
        )

        # ASETPLTFRM-383 (revised) — daily entry timing. The canonical
        # daily entry is the LAST CLOSED bar: if the strategy fires on
        # history[:-1] (excluding today's still-forming candle) we act
        # immediately, regardless of wall-clock — matches Paper /
        # backtest and covers a signal already valid 1-2 days back that
        # still holds. A BUY that appears ONLY on today's forming candle
        # is premature until _MIN_EVAL_TIME_IST. Exits are
        # never gated (stop-loss / time-stop handled above; a
        # discretionary SELL flows through unchanged below).
        is_flat = existing_pos is None or existing_pos.qty <= 0
        daily_realtime = (
            self._strategy.schedule.interval == "1d"
            and not self._is_replay
        )
        last_bar_is_today = (
            len(history) >= 2 and history[-1].date == bar_date_obj
        )
        if (
            daily_realtime
            and is_flat
            and last_bar_is_today
            and bar.ticker not in self._ticker_locked
        ):
            now_ist = datetime.now(IST).time()

            # Gate A: no BUY before _MIN_BUY_TIME_IST (default 09:30).
            # Pre-open auction prices are erratic; first 15 min of the
            # regular session is volatile price discovery.
            # SELL / GTT exits are never blocked here.
            if (
                signal is not None
                and signal.side == "BUY"
                and now_ist < _MIN_BUY_TIME_IST
            ):
                _logger.info(
                    "daily BUY deferred — before %s IST "
                    "(ticker=%s now=%s IST)",
                    _MIN_BUY_TIME_IST.strftime("%H:%M"),
                    bar.ticker,
                    now_ist.strftime("%H:%M:%S"),
                )
                return 0

            if now_ist < _MIN_EVAL_TIME_IST:
                # Before gate — only yesterday's closed bar may trigger
                # a BUY. Running-bar-only signals are deferred.
                closed_entry = self._eval_entry_on_closed_bar(
                    history, bar, last_price,
                )
                if closed_entry is not None and closed_entry.side == "BUY":
                    # Dual-bar confirmation: yesterday was oversold
                    # (closed_entry says BUY). Also require today's
                    # running bar to confirm (today's main-eval signal
                    # == BUY). If today's bar no longer says BUY the
                    # stock has already recovered intraday — suppress to
                    # avoid chasing a gap-up or upper-circuit opener.
                    if signal is not None and signal.side == "BUY":
                        _logger.info(
                            "daily entry on CLOSED bar (pre-gate) — "
                            "both bars confirm: ticker=%s",
                            bar.ticker,
                        )
                        signal = closed_entry
                    else:
                        _logger.info(
                            "daily closed-bar BUY suppressed — "
                            "today's running bar does not confirm "
                            "(stock recovered intraday, ticker=%s)",
                            bar.ticker,
                        )
                        self._events.append(
                            event_row(
                                session_id=self._session_id,
                                user_id=self._user_id,
                                strategy_id=self._strategy.id,
                                mode="live",
                                type_="signal_rejected",
                                payload={
                                    **(
                                        {"dry_run": True}
                                        if self._dry_run else {}
                                    ),
                                    "reason": "today_bar_not_confirmed",
                                    "ticker": bar.ticker,
                                    "side": "BUY",
                                },
                            )
                        )
                        return 0
                elif signal is not None and signal.side == "BUY":
                    _logger.info(
                        "daily entry premature (today-forming only) "
                        "— deferring %s until %s IST",
                        bar.ticker,
                        _MIN_EVAL_TIME_IST.strftime("%H:%M"),
                    )
                    return 0
            # After gate — signal from full history (running bar included)
            # flows through unchanged. No closed-bar override.

        if signal is None:
            _logger.info(
                "_action_to_signal returned None ticker=%s action=%s "
                "last_price=%s equity=%s",
                bar.ticker, action, last_price,
                self._initial + self._positions.total_realised_pnl_inr(),
            )
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

        # Per-ticker cap: block BUY when the ticker already has an active
        # order in-flight (_ticker_locked) or an open position from
        # startup hydration (existing_pos.qty > 0). This prevents the
        # weight-based order sizer from placing duplicate entries on the
        # same stock across successive bars, enforcing portfolio
        # diversification. Emit signal_rejected for observability.
        if signal.side == "BUY" and (
            signal.ticker in self._ticker_locked
            or (existing_pos is not None and existing_pos.qty > 0)
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
                        "reason": "ticker_already_in_portfolio",
                        "ticker": signal.ticker,
                        "side": signal.side,
                        "qty": signal.qty,
                        "in_flight": signal.ticker in self._ticker_locked,
                        "open_qty": (
                            existing_pos.qty if existing_pos else 0
                        ),
                    },
                )
            )
            _logger.debug(
                "live per-ticker cap: %s already in portfolio "
                "(in_flight=%s open_qty=%s)",
                signal.ticker,
                signal.ticker in self._ticker_locked,
                existing_pos.qty if existing_pos else 0,
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
                self._user_id,
                session_factory=disposable_pg_session,
            ),
        )
        # Exposure-based day_state: "consumption" is the capital
        # currently tied up in this strategy's open positions, not
        # turnover-since-09:00. PositionTracker is hydrated from
        # Kite at runtime spawn (position_hydration.hydrate) so a
        # restart preserves yesterday's overnight legs. Square-offs
        # naturally bring this back to 0, no daily reset job needed.
        positions_open = self._positions.open_positions()
        filled_committed = sum(
            (Decimal(p.qty) * p.avg_price for p in positions_open.values()),
            start=Decimal("0"),
        )
        # Critical C4 — deployed = filled positions + in-flight
        # BUY reservations. Without the in-flight term two BUYs in
        # one tick both size against the same remaining max_inr
        # (fills land asynchronously, so positions_open hasn't moved
        # yet). Dry-run reservations are tagged mode=dryrun and are
        # excluded by the query, so the rehearsal still sees full
        # headroom. Best-effort: a budget read failure must not
        # block trading, so fall back to filled-only.
        active_reserved = Decimal("0")
        try:
            active_reserved = await budget_active_for_strategy(
                self._user_id,
                self._strategy.id,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "in-flight reservation read failed for strategy=%s "
                "— deployed falls back to filled-only",
                self._strategy.id,
                exc_info=True,
            )
        committed_inr_now = filled_committed + active_reserved
        day_state = {
            "cumulative_inr_today": committed_inr_now,
            "orders_count_today": len(positions_open),
        }

        # Strategy budget cap — BUY only. Use the strategy's own
        # max_inr allocation minus what's already deployed to compute
        # how many shares we can actually afford. This is tighter than
        # Zerodha's live_balance (which includes the user's buffer
        # beyond the strategy allocation) and correctly reflects
        # remaining strategy headroom.
        # Only active when max_inr > 0 (0 means "no cap").
        if signal.side == "BUY":
            _max_inr = Decimal(str(current_caps.get("max_inr") or 0))
            if _max_inr > 0:
                _internal_remaining = _max_inr - committed_inr_now
                # Cap against Kite's actual available cash so the clamped
                # qty doesn't overshoot what the broker will accept.
                # pre_trade_check enforces this too, but aligning here
                # turns a full reject into a partial-fill instead.
                # Dry-run: no real broker cash; fail-open on error.
                _kite_cash = Decimal("Infinity")
                if not self._dry_run:
                    try:
                        _kite_cash = await fetch_kite_available_cash(
                            self._user_id
                        )
                    except Exception:  # noqa: BLE001
                        pass  # pre_trade_check is the authoritative gate
                _remaining = min(_internal_remaining, _kite_cash)
                _affordable = (
                    int(_remaining // last_price)
                    if last_price and last_price > 0 and _remaining > 0
                    else 0
                )
                if _affordable < 1:
                    self._events.append(
                        event_row(
                            session_id=self._session_id,
                            user_id=self._user_id,
                            strategy_id=self._strategy.id,
                            mode="live",
                            type_="signal_rejected",
                            payload={
                                **({"dry_run": True} if self._dry_run else {}),
                                "reason": "insufficient_balance",
                                "ticker": signal.ticker,
                                "side": signal.side,
                                "qty": signal.qty,
                                "max_inr": str(_max_inr),
                                "committed_inr": str(committed_inr_now),
                                "remaining_inr": str(max(_remaining, Decimal("0"))),
                                "last_price": str(last_price),
                            },
                        )
                    )
                    _logger.warning(
                        "live budget cap: %s rejected — remaining ₹%s "
                        "(max_inr ₹%s − deployed ₹%s) < price ₹%s",
                        signal.ticker,
                        _remaining,
                        _max_inr,
                        committed_inr_now,
                        last_price,
                    )
                    return 0
                elif _affordable < signal.qty:
                    _old_qty = signal.qty
                    signal = signal.model_copy(update={"qty": _affordable})
                    self._events.append(
                        event_row(
                            session_id=self._session_id,
                            user_id=self._user_id,
                            strategy_id=self._strategy.id,
                            mode="live",
                            type_="signal_adjusted",
                            payload={
                                **({"dry_run": True} if self._dry_run else {}),
                                "ticker": signal.ticker,
                                "side": signal.side,
                                "old_qty": _old_qty,
                                "new_qty": _affordable,
                                "max_inr": str(_max_inr),
                                "committed_inr": str(committed_inr_now),
                                "remaining_inr": str(_remaining),
                                "last_price": str(last_price),
                                "reason": "strategy_budget_cap",
                            },
                        )
                    )
                    _logger.info(
                        "live budget cap: %s qty %d → %d "
                        "(max_inr ₹%s − deployed ₹%s = ₹%s remaining, "
                        "price ₹%s)",
                        signal.ticker,
                        _old_qty,
                        _affordable,
                        _max_inr,
                        committed_inr_now,
                        _remaining,
                        last_price,
                    )

        decision = await pre_trade_check(
            signal=signal,
            caps=current_caps,
            day_state=day_state,
            account=account,
            strategy_risk=self._strategy.risk.model_dump(),
            last_price=last_price,
            user_id=self._user_id,
            dry_run=self._dry_run,
            last_price_per_ticker=last_price_per_ticker,
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

    # Default cooldown between same-(ticker, side) non-protective
    # placements. Read once per call so tests / ops can tune via env
    # without a restart. 300s = 5 min: long enough to outlast an
    # order-timeout cancel+re-eval cycle, short enough to not block a
    # legitimate same-direction rebalance later in the session.
    _COOLDOWN_DEFAULT_S = 300.0

    def _churn_suppress_kind(
        self, *, ticker: str, side: str
    ) -> str | None:
        """Return the churn-suppression kind for a non-protective
        (ticker, side) order, or None if it may proceed.

        ``"inflight"`` — a non-terminal in-flight entry exists for the
        same (ticker, side). ``"cooldown"`` — placed within
        ``ALGO_ORDER_COOLDOWN_S`` of the last actual placement. The
        caller MUST have already exempted protective exits.
        """
        symbol = ticker.replace(".NS", "")
        _TERMINAL = {"filled", "cancelled", "rejected", "complete"}
        for entry in self._in_flight:
            if entry.get("side") != side:
                continue
            entry_symbol = str(entry.get("symbol", "")).replace(
                ".NS", ""
            )
            if entry_symbol != symbol:
                continue
            if str(entry.get("status", "")).lower() not in _TERMINAL:
                return "inflight"

        try:
            cooldown_s = float(
                os.getenv(
                    "ALGO_ORDER_COOLDOWN_S",
                    str(self._COOLDOWN_DEFAULT_S),
                )
            )
        except (TypeError, ValueError):
            cooldown_s = self._COOLDOWN_DEFAULT_S
        last = self._last_submit_ts.get((ticker, side))
        if last is not None and (_time.time() - last) < cooldown_s:
            return "cooldown"
        return None

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

        # Task 4.0a — anti-churn guard. On 2026-06-25 the runtime
        # re-issued an identical KTKBANK set_target_weight SELL every
        # eval (~60s) while prior ones were still in-flight or had
        # just been cancelled by the order-timeout watcher → place→
        # cancel→re-place churn that became real fills. Suppress a
        # non-protective duplicate (ticker, side) when an order is
        # already in flight OR within the cooldown window.
        #
        # Load-bearing safety property: a PROTECTIVE exit MUST NEVER
        # be suppressed. stop_loss / time_stop / mis_auto_square_off
        # (and any reason containing "exit") bypass the guard
        # entirely — a protective SELL always reaches place_order.
        reason = (signal.reason or "").lower()
        _PROTECTIVE = {"stop_loss", "time_stop", "mis_auto_square_off"}
        is_protective = reason in _PROTECTIVE or "exit" in reason
        if not is_protective:
            suppress_kind = self._churn_suppress_kind(
                ticker=signal.ticker, side=side
            )
            if suppress_kind is not None:
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="dryrun" if self._dry_run else "live",
                        type_="order_suppressed_churn",
                        payload={
                            **(
                                {"dry_run": True}
                                if self._dry_run
                                else {}
                            ),
                            "ticker": signal.ticker,
                            "side": side,
                            "qty": signal.qty,
                            "signal_reason": signal.reason,
                            "suppress_kind": suppress_kind,
                        },
                    )
                )
                _logger.info(
                    "order SUPPRESSED (churn/%s): ticker=%s side=%s "
                    "qty=%d reason=%s — not placing",
                    suppress_kind,
                    signal.ticker,
                    side,
                    signal.qty,
                    signal.reason,
                )
                return 0

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
            # Look up the actual per-symbol tick size from the
            # instruments cache (same Redis hash populated by freeze_cache).
            # LAURUSLABS and other mid/smallcap scripts use 0.10, not 0.05.
            tick = await asyncio.to_thread(
                get_tick_size,
                kc=self._kite,
                redis_client=self._kite._get_redis(),
                symbol=symbol,
            )
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

        # Budget reservation.
        #
        # Critical C4 — for a LIVE BUY the reservation is the
        # AUTHORITATIVE atomic gate, not just an audit row. The
        # safety.py Cap-0 check (run earlier in pre_trade_check) is
        # a cached advisory pre-filter; it has a TOCTOU window where
        # two concurrent BUYs can both pass. reserve_if_headroom
        # locks the user (advisory xact lock + FOR UPDATE),
        # recomputes headroom UNCACHED in one txn, and only inserts
        # the PENDING row if it fits. On None we ABORT — no order is
        # placed.
        #
        # SELL frees capital (never gated) and dry-run is a
        # rehearsal that must NOT consume real budget — both keep
        # the plain append-only audit reservation. dry-run rows are
        # tagged mode=dryrun and are excluded from every headroom
        # query.
        order_cost = (
            Decimal(signal.qty) * last_price
            if last_price and last_price > 0
            else Decimal("0")
        )
        reservation_metadata = {
            "internal_order_id": internal_order_id,
            "limit_price": (
                str(limit_price) if limit_price is not None else None
            ),
            "mode": "dryrun" if self._dry_run else "live",
        }
        if signal.side == "BUY" and not self._dry_run:
            try:
                allocated_inr = (
                    await budget_load_user(self._user_id)
                ).allocated_inr
            except Exception:  # noqa: BLE001
                # Fail-closed: cannot determine the allocation →
                # do not place a live order against unknown budget.
                _logger.error(
                    "live order ABORT %s — budget load failed; "
                    "cannot gate on allocated_inr (fail-closed)",
                    signal.ticker,
                    exc_info=True,
                )
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="live",
                        type_="signal_rejected",
                        payload={
                            "reason": "insufficient_balance",
                            "ticker": signal.ticker,
                            "side": signal.side,
                            "qty": signal.qty,
                            "order_cost": str(order_cost),
                            "detail": "budget_load_failed",
                        },
                    )
                )
                return 0
            reservation_id = await budget_reserve_if_headroom(
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                ticker=signal.ticker,
                side=signal.side,
                qty=signal.qty,
                reserved_inr=order_cost,
                allocated_inr=allocated_inr,
                metadata=reservation_metadata,
            )
            if reservation_id is None:
                # Atomic gate rejected — over allocated headroom.
                # ABORT; do NOT place the order.
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="live",
                        type_="signal_rejected",
                        payload={
                            "reason": "insufficient_balance",
                            "ticker": signal.ticker,
                            "side": signal.side,
                            "qty": signal.qty,
                            "order_cost": str(order_cost),
                            "allocated_inr": str(allocated_inr),
                        },
                    )
                )
                _logger.warning(
                    "live order ABORT %s — atomic reserve "
                    "rejected: cost ₹%s over allocated ₹%s "
                    "headroom",
                    signal.ticker,
                    order_cost,
                    allocated_inr,
                )
                return 0
        else:
            reservation_id = await budget_reserve(
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                ticker=signal.ticker,
                side=signal.side,
                qty=signal.qty,
                reserved_inr=order_cost,
                metadata=reservation_metadata,
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
        except PartialChunkPlacementError as exc:
            # Critical C1 — a freeze-split order failed mid-loop
            # with chunks 0..N-1 ALREADY live on the exchange. We
            # must NOT re-submit (that would blind-retry the full
            # qty and double the live chunks). Instead record the
            # live order ids in _in_flight so the postback / order-
            # timeout reconciler tracks and settles them, and move
            # the reservation into PARTIAL (an ACTIVE, non-terminal
            # state) so its reserved capital stays held and the
            # reconciliation loop picks it up — explicitly NOT
            # FILLED/CANCELLED (terminal — would free/settle the
            # budget and lose the live exposure).
            placed_ids = exc.placed_order_ids
            _logger.error(
                "live order PARTIAL chunk failure: symbol=%s "
                "side=%s placed=%d failed_chunk=%d — recording "
                "live ids %s, NOT re-submitting",
                symbol,
                side,
                len(placed_ids),
                exc.failed_chunk,
                placed_ids,
                exc_info=True,
            )
            self._events.append(
                event_row(
                    session_id=self._session_id,
                    user_id=self._user_id,
                    strategy_id=self._strategy.id,
                    mode="live",
                    type_="order_partial_chunk_failure",
                    payload={
                        **({"dry_run": True} if self._dry_run else {}),
                        "internal_order_id": internal_order_id,
                        "symbol": symbol,
                        "side": side,
                        "qty": signal.qty,
                        "placed_order_ids": placed_ids,
                        "placed_count": len(placed_ids),
                        "failed_chunk": exc.failed_chunk,
                        "rejection_reason": str(exc.cause)[:500],
                    },
                )
            )
            # Record each live chunk so reconciliation / order-
            # timeout track them. All chunks share the originating
            # internal_order_id + reservation_id for attribution.
            for oid in placed_ids:
                self._in_flight.append(
                    {
                        "kite_order_id": oid,
                        "internal_order_id": internal_order_id,
                        "symbol": symbol,
                        "side": side,
                        "qty": signal.qty,
                        "submitted_at": now_iso,
                        "status": "submitted",
                        "reason": signal.reason,
                        "product": product_code,
                        "reservation_id": (
                            str(reservation_id)
                            if reservation_id
                            else None
                        ),
                    }
                )
            if placed_ids:
                await self._caps_repo.update_in_flight(
                    self._user_id,
                    self._run_id,
                    self._in_flight,
                )
            # Move reservation to PARTIAL (needs-reconcile) — keeps
            # the capital held; reconciler settles the live chunks.
            try:
                await budget_transition(
                    reservation_id=reservation_id,
                    new_state=ReservationState.PARTIAL,
                    kite_order_id=(
                        placed_ids[0] if placed_ids else None
                    ),
                    error_text=str(exc.cause)[:500],
                )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "budget transition to PARTIAL failed — "
                    "reservation %s, placed ids %s — "
                    "reconciliation loop will heal",
                    reservation_id,
                    placed_ids,
                )
            # Return the count of live chunks (>0 if any reached
            # the exchange). Crucially we DO NOT re-call
            # place_order — the live chunks settle via reconcile.
            return len(placed_ids)
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
            # Stored so _sync_fills_from_pg can transition the budget
            # reservation to FILLED immediately on postback — without
            # this the only path was reconcile_one() polling Kite API
            # which drops history after 1 trading day.
            "reservation_id": (
                str(reservation_id) if reservation_id else None
            ),
        }
        self._in_flight.append(in_flight_entry)
        # Task 4.0a — record the placement clock for the anti-churn
        # cooldown. Keyed on the internal ticker (.NS form) + side so
        # the guard's lookup matches. Updated ONLY on an actual
        # placement (incl. dry-run), never on a suppressed/aborted
        # order — so a suppressed dup does not extend the window.
        self._last_submit_ts[(signal.ticker, side)] = _time.time()
        await self._caps_repo.update_in_flight(
            self._user_id,
            self._run_id,
            self._in_flight,
        )

        # Per-ticker cap: lock on BUY submission, release on SELL
        # submission. Release on SELL submission (not fill) so the
        # ticker is available for re-entry as soon as the close order
        # is in motion; the BUY gate's existing_pos.qty check still
        # guards against premature re-entry before the SELL fills.
        if side == "BUY":
            self._ticker_locked.add(signal.ticker)
        else:
            self._ticker_locked.discard(signal.ticker)
        self._sync_ticker_lock_to_redis()

        # PR #1 (order-safety) — order_submitted_live is now
        # emitted from inside KiteClient.place_order with the full
        # request/context/response payload (spec §3.6). We carry
        # `reason` separately into in_flight_entry above so the
        # eventual order_filled_live event keeps the attribution
        # link; the kite_client payload preserves all top-level
        # keys (kite_order_id / dry_run / side / qty / symbol)
        # that PaperEventsTimeline reads.

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
            await asyncio.to_thread(flush_events, self._events)
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
        factor_row = next(
            (
                self._factor_cache.get(
                    (ticker, bar_date_obj - timedelta(days=n))
                )
                for n in range(8)
                if (bar_date_obj - timedelta(days=n)).weekday() < 5
                and self._factor_cache.get(
                    (ticker, bar_date_obj - timedelta(days=n))
                )
            ),
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

    def _eval_entry_on_closed_bar(
        self,
        history: list,
        bar: Any,
        last_price: Decimal,
    ) -> Signal | None:
        """Evaluate the strategy ENTRY on the last CLOSED daily bar
        (``history[:-1]`` — excludes today's still-forming candle).

        Returns the resulting Signal (typically BUY) or None. Used by
        the daily eval-time gate to tell a completed-bar entry (act
        now) from a today-forming-only entry (deferred until
        _MIN_EVAL_TIME_IST). Always evaluated flat (open_qty=0); callers only invoke
        it when there is no open position.
        """
        from backend.algo.backtest.indicators import compute_indicators

        closed = history[:-1]
        if not closed:
            return None
        closed_date = closed[-1].date
        cache_key = (bar.ticker, closed_date)
        newly_computed = False
        if cache_key in self._closed_entry_cache:
            action = self._closed_entry_cache[cache_key]
        else:
            newly_computed = True
            ind_map = compute_indicators(closed)
            # Idempotent lazy cache loads (already warmed for today,
            # which covers the prior day, but keep them explicit).
            self._ensure_factor_cache(bar.ticker, closed_date)
            self._ensure_regime_cache(closed_date)
            self._ensure_daily_overlay_cache(bar.ticker, closed_date)
            features = assemble_per_bar_features(
                bar_feats=ind_map.get(
                    closed_date,
                    {
                        "today_ltp": closed[-1].close,
                        "today_vol": Decimal(closed[-1].volume),
                    },
                ),
                market_regime=next(
                    (
                        self._market_regime.get(closed_date - timedelta(days=n))
                        for n in range(8)
                        if (closed_date - timedelta(days=n)).weekday() < 5
                        and self._market_regime.get(closed_date - timedelta(days=n))
                        is not None
                    ),
                    None,
                ),
                market_trend=next(
                    (
                        self._market_trend.get(closed_date - timedelta(days=n))
                        for n in range(8)
                        if (closed_date - timedelta(days=n)).weekday() < 5
                        and self._market_trend.get(closed_date - timedelta(days=n))
                        is not None
                    ),
                    None,
                ),
                factor_row=next(
                    (
                        self._factor_cache.get(
                            (bar.ticker, closed_date - timedelta(days=n))
                        )
                        for n in range(8)
                        if (closed_date - timedelta(days=n)).weekday() < 5
                        and self._factor_cache.get(
                            (bar.ticker, closed_date - timedelta(days=n))
                        )
                    ),
                    None,
                ),
                regime_row=next(
                    (
                        self._regime_by_date.get(closed_date - timedelta(days=n))
                        for n in range(8)
                        if (closed_date - timedelta(days=n)).weekday() < 5
                        and self._regime_by_date.get(
                            closed_date - timedelta(days=n)
                        )
                    ),
                    None,
                ),
                daily_overlay=self._daily_overlay_cache.get(
                    (bar.ticker, closed_date),
                ),
            )
            ctx = EvalContext(
                ticker=bar.ticker,
                bar_date=closed_date,
                features=features,
                open_qty=0,
            )
            try:
                action = self._evaluator.eval_node(
                    self._strategy.root.model_dump(by_alias=True),
                    ctx,
                )
            except KeyError:
                action = None
            self._closed_entry_cache[cache_key] = action
        if action is None:
            return None
        sig = self._action_to_signal(
            action,
            ticker=bar.ticker,
            bar_date_ns=bar.bar_open_ts_ns,
            last_price=last_price,
        )
        # Observability — a completed-bar entry whose ``set_target_weight``
        # BUY intent rounds to qty=0 (account can't afford one share) is
        # otherwise a SILENT no-op: no signal, no event, the events panel
        # looks frozen. Surface it as a ``signal_rejected`` so the user
        # sees WHY no entry fired. ``newly_computed`` (cache miss) bounds
        # this to at most one event per (ticker, closed bar) — no per-tick
        # spam. Only the flat case reaches here (open_qty=0 by contract).
        if sig is None and newly_computed:
            self._maybe_emit_qty_zero_rejection(
                action=action,
                ticker=bar.ticker,
                last_price=last_price,
                bar_date=closed_date,
            )
        return sig

    def _evict_stale_closed_entry_cache(self, *, as_of: date) -> None:
        """Drop _closed_entry_cache entries older than
        _CLOSED_ENTRY_CACHE_MAX_AGE_DAYS calendar days before ``as_of``.

        High #15 — prevents the cache from growing without bound across
        a long live session. Called once per bar-close; the dict is
        small (one entry per ticker per day) so the comprehension is
        cheap. Safe to call on an empty dict.
        """
        if not self._closed_entry_cache:
            return
        cutoff = as_of - timedelta(days=_CLOSED_ENTRY_CACHE_MAX_AGE_DAYS)
        self._closed_entry_cache = {
            k: v
            for k, v in self._closed_entry_cache.items()
            if k[1] >= cutoff
        }

    def _maybe_emit_qty_zero_rejection(
        self,
        *,
        action: dict,
        ticker: str,
        last_price: Decimal | None,
        bar_date: date,
    ) -> bool:
        """Emit a ``signal_rejected`` event when a ``set_target_weight``
        BUY intent sizes to qty=0 because available equity cannot afford
        a single share. Mirrors the sizing math in ``_action_to_signal``
        so the event fires for exactly the case that method drops to
        ``None``. Returns True iff an event was appended (eases testing).
        """
        if not isinstance(action, dict):
            return False
        if action.get("type") != "set_target_weight":
            return False
        if last_price is None or last_price <= 0:
            return False
        try:
            weight = Decimal(str(action.get("weight", 0)))
        except (TypeError, ValueError, ArithmeticError):
            return False
        if weight <= 0:
            return False
        current_equity = (
            self._initial + self._positions.total_realised_pnl_inr()
        )
        if current_equity <= 0:
            return False
        target_qty = int((current_equity * weight) // last_price)
        # Only the can't-afford-one-share case is the silent drop worth
        # surfacing; a positive target sizes normally through the signal.
        if target_qty > 0:
            return False
        self._events.append(
            event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="live",
                type_="signal_rejected",
                payload={
                    **({"dry_run": True} if self._dry_run else {}),
                    "reason": "insufficient_capital_qty_zero",
                    "ticker": ticker,
                    "symbol": str(ticker).upper().removesuffix(".NS"),
                    "side": "BUY",
                    "qty": 0,
                    "target_weight": float(weight),
                    "last_price": str(last_price),
                    "current_equity_inr": str(current_equity),
                    "bar_date": bar_date.isoformat(),
                },
            )
        )
        _logger.info(
            "live entry SKIPPED — insufficient capital (qty=0): "
            "ticker=%s target_weight=%s last_price=%s equity=%s "
            "(raise capital / weight or use a cheaper universe)",
            ticker,
            float(weight),
            last_price,
            current_equity,
        )
        return True

    def _detect_capital_below_deployed(self) -> None:
        """Flag a start where capital < already-deployed cost-basis.

        Task 4.0b real-money guardrail. ``set_target_weight`` sizes
        the target off ``self._initial``; if the runtime is (re)started
        with capital well below the cost of positions that were built
        under a larger account, every held position looks "overweight"
        and the strategy would issue SELLs that trim REAL shares — a
        surprise sell-off. Called once in ``run()`` after positions are
        hydrated; sets ``self._capital_below_deployed`` and emits a
        HIGH-severity ``capital_below_deployed`` event + WARNING when
        tripped. The suppression itself lives in the
        ``set_target_weight`` branch of ``_action_to_signal``.
        """
        deployed_cost = sum(
            pos.qty * float(pos.avg_price)
            for pos in self._positions.open_positions().values()
        )
        initial = float(self._initial)
        if deployed_cost <= 0 or initial >= deployed_cost:
            self._capital_below_deployed = False
            return
        self._capital_below_deployed = True
        ratio = initial / deployed_cost if deployed_cost else 0.0
        _logger.warning(
            "LiveRuntime: start capital ₹%.2f is BELOW already-"
            "deployed cost ₹%.2f (ratio=%.3f) — suppressing "
            "rebalance-DOWN trims to avoid a surprise sell-off; set "
            "ALGO_ALLOW_REBALANCE_DOWN_ON_SHRINK=1 to override",
            initial,
            deployed_cost,
            ratio,
        )
        self._events.append(
            event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="live",
                type_="capital_below_deployed",
                payload={
                    "severity": "high",
                    "initial": initial,
                    "deployed_cost": deployed_cost,
                    "ratio": ratio,
                },
            )
        )

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
            # v5 trailing stop: GTT is the primary exit — suppress
            # the AST's RSI-based exit while the GTT is live so the
            # position can ride further before the trail fires.
            if self._trailing_enabled and ticker in self._trailing_managers:
                _logger.info(
                    "trailing active — suppressing AST exit for %s "
                    "(gtt_id=%s handles close)",
                    ticker,
                    self._gtt_ids.get(ticker),
                )
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
                # Task 4.0b — capital-shrink guardrail. A trim-down
                # SELL while started below already-deployed cost would
                # liquidate REAL shares (the 2026-06-25 incident).
                # Suppress it and surface why, UNLESS the operator
                # opted in to genuinely reduce capital. Protective
                # exits never reach here (separate reasons).
                if (
                    self._capital_below_deployed
                    and not _env_truthy(
                        "ALGO_ALLOW_REBALANCE_DOWN_ON_SHRINK"
                    )
                ):
                    _logger.warning(
                        "set_target_weight trim SUPPRESSED for %s "
                        "(capital below deployed): target=%d "
                        "current=%d — would sell %d real shares",
                        ticker,
                        target_qty,
                        current_qty,
                        -diff,
                    )
                    self._events.append(
                        event_row(
                            session_id=self._session_id,
                            user_id=self._user_id,
                            strategy_id=self._strategy.id,
                            mode="live",
                            type_=(
                                "rebalance_down_suppressed_"
                                "capital_shrink"
                            ),
                            payload={
                                "severity": "high",
                                "ticker": ticker,
                                "target_qty": target_qty,
                                "current_qty": current_qty,
                            },
                        )
                    )
                    return None
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

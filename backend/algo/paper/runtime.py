"""PaperRuntime — tick-driven strategy executor.

Lifecycle:
  1. Caller starts ``run(source)``.
  2. For every tick in ``source``:
       - Update an internal Resampler (1m bars feed the AST
         evaluator's price features).
       - On every bar close, evaluate ``strategy.root`` against
         the bar context.
       - For each emitted action, build a Signal.
       - Gate via RiskEngine; reject → ``signal_rejected`` event.
       - Accept/scale → PaperBroker.execute → Fill.
       - Apply fill to PositionTracker; emit ``order_filled``.
  3. On shutdown, force-flush in-flight bars and single-commit
     events to algo.events.

This v1 runtime is one-strategy-per-instance. Multi-strategy
fan-out across one user's tick stream lives in Slice 8b's
service shell.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from backend.algo.attribution.payload import (
    attribution_payload_extension as _attribution_payload_extension,
)
from backend.algo.backtest.event_writer import (
    event_row,
    flush_events,
)
from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.cooldown_hydration import (
    load_recent_failed_exits,
)
from backend.algo.backtest.cooldown_monitor import in_cooldown
from backend.algo.backtest.stop_loss_monitor import (
    check_stop_loss_triggers,
)
from backend.algo.backtest.time_stop_monitor import (
    check_time_stop_triggers,
)

# REGIME-2a — pre-computed nightly factor library overlay.
from backend.algo.factors.repo import get_factors_window
# FE-15b — shared per-bar feature assembly.
from backend.algo.features.per_bar import (
    assemble_per_bar_features,
    lookup_daily_overlay,
)
from backend.algo.paper.broker import PaperBroker
from backend.algo.paper.risk_engine import RiskEngine
from backend.algo.paper.types import AccountState, Signal

# REGIME-4 — vol-target / Kelly sizer (legacy modes bypass).
from backend.algo.sizing.composer import SizingContext, compose_qty
from backend.algo.stream.resampler import Resampler
from backend.algo.stream.sources import TickSource
from backend.algo.strategy.ast import Strategy

_logger = logging.getLogger(__name__)


def _features_for_bar(bar) -> dict[str, Decimal]:  # noqa: ANN001
    """Minimal fallback feature map. The runtime augments this
    with rolling indicators (SMAs, golden_cross_days_ago) on
    every bar close — see _on_bar_close. This stub is used only
    when the indicator engine has zero history for a ticker."""
    return {
        "today_ltp": bar.close,
        "today_vol": Decimal(bar.volume),
    }


class PaperRuntime:
    def __init__(
        self,
        *,
        strategy: Strategy,
        user_id: UUID,
        initial_capital_inr: Decimal,
        fee_as_of: date,
        kill_switch_active: bool = False,
    ) -> None:
        self._strategy = strategy
        self._user_id = user_id
        self._initial = initial_capital_inr
        self._broker = PaperBroker(fee_as_of=fee_as_of)
        self._evaluator = Evaluator()
        self._risk = RiskEngine()
        self._resampler = Resampler(intervals=(60,))
        self._positions = PositionTracker()
        self._session_id = uuid4()
        self._events: list[dict[str, Any]] = []
        self._kill_switch_active = kill_switch_active
        # Per-ticker rolling bar history for indicator computation.
        # On every closed bar we re-run compute_indicators over the
        # ticker's full series. SMA + golden_cross are O(N); paper
        # sessions are bounded (~375 1m bars/day) so cost is fine.
        self._bars_by_ticker: dict[str, list] = {}

        # Top-level market regime features (NIFTY-derived) — same
        # ones the backtest runner injects. Strategies referencing
        # ``nifty_above_sma200`` or ``nifty_30d_return_pct`` would
        # otherwise raise KeyError on every bar (silently caught,
        # signal never fires). Pre-computed once at start over a
        # wide date window so per-bar lookups are O(1).
        self._market_regime: dict[Any, Decimal] = {}
        self._market_trend: dict[Any, Decimal] = {}
        try:
            from datetime import date as _date, timedelta
            from backend.algo.backtest.indicators import (
                compute_market_regime as _cmr,
                compute_market_trend_strength as _cmts,
            )

            # ``load_ohlcv_window`` validates against UTC today;
            # using local IST time here would race past the UTC
            # boundary and trip BackedFutureBarError. Match the
            # validator's clock.
            today = datetime.now(timezone.utc).date()
            window_start = today - timedelta(days=365 * 3)
            self._market_regime = _cmr(window_start, today)
            self._market_trend = _cmts(window_start, today)
            _logger.info(
                "PaperRuntime: regime cache loaded — %d regime "
                "days, %d trend days",
                len(self._market_regime),
                len(self._market_trend),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "PaperRuntime: regime cache load failed: %s "
                "(strategies referencing nifty_* features will "
                "silent-skip every bar)",
                exc,
            )

        # ASETPLTFRM-436 — hydrate the cooldown gate from
        # algo.events at session start. Paper sessions accumulate
        # failed-exit history over time; on restart we re-load
        # it here. ``in_cooldown`` reads this list — same pure
        # function as backtest, different data source.
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
                    runtime_mode="paper",
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "PaperRuntime: cooldown hydration failed "
                    "(%s) — gate starts empty",
                    exc,
                )

        # REGIME-2a — per-ticker cached factor row for today.
        # Lazily populated on first sight of a ticker in
        # ``_on_bar_close`` (paper sessions don't know the
        # universe upfront — the strategy's ``universe`` is a
        # scope spec, not a ticker list — so eager loading would
        # require resolving the scope here).
        self._factor_cache: dict[tuple[str, date], dict[str, Decimal]] = {}
        self._factor_loaded_for_ticker: set[str] = set()
        # REGIME-1 — regime_label + stress_prob lookup, loaded
        # lazily on first bar so paper sessions resolve regime
        # features identically to backtest.
        self._regime_by_date: dict[date, dict[str, Any]] = {}
        self._regime_loaded: bool = False
        # FE-15b — daily-overlay panel (interval_sec=86400) for
        # cross-cadence AST references. Loaded lazily per ticker
        # on first sight, same pattern as the factor cache.
        # Empty / unused when strategy.schedule.interval == "1d"
        # (daily strategies read daily features as primary).
        self._daily_overlay_cache: dict[
            tuple[str, date], dict[str, Decimal | str]
        ] = {}
        self._daily_overlay_loaded_for_ticker: set[str] = set()

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
                "PaperRuntime: regime_history load failed: %s — "
                "regime-aware templates will silent-skip",
                exc,
            )

    def _ensure_factor_cache(
        self,
        ticker: str,
        bar_date_obj: date,
    ) -> None:
        """Lazy load factor rows for ``ticker`` over a wide
        window so any historical bar in this paper session
        resolves from the cache. Called once per ticker."""
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
                "PaperRuntime: factor cache load for %s failed: "
                "%s — strategies referencing factor.* keys will "
                "silent-skip until backfill catches up",
                ticker,
                exc,
            )

    def _ensure_daily_overlay_cache(
        self, ticker: str, bar_date_obj: date,
    ) -> None:
        """FE-15b — lazy load daily-cadence features
        (``interval_sec=86400``) for ``ticker`` so the AST can
        reference ``{name}_1d`` keys in intraday strategies.

        Skipped entirely for daily strategies (primary cadence
        is already 86400 — no overlay needed).

        Failures are logged + swallowed; strategies that don't
        reference ``_1d`` keys are unaffected.
        """
        if self._strategy.schedule.interval == "1d":
            return
        if ticker in self._daily_overlay_loaded_for_ticker:
            return
        self._daily_overlay_loaded_for_ticker.add(ticker)
        try:
            from datetime import timedelta as _td

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
                from datetime import datetime as _dt
                from datetime import time as _time
                from datetime import timezone as _tz

                for ts_ns, feats in by_ts.items():
                    # Reverse-derive bar_date from ts_ns
                    # (UTC midnight ns). Daily rows are
                    # deterministic per
                    # daily_features_daily_compute._utc_midnight_ns.
                    bd = _dt.fromtimestamp(
                        ts_ns / 1_000_000_000, tz=_tz.utc
                    ).date()
                    self._daily_overlay_cache[(tk, bd)] = feats
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "PaperRuntime: daily overlay load for %s failed: "
                "%s — strategies referencing _1d keys will "
                "silent-skip until backfill catches up",
                ticker, exc,
            )

    async def run(self, source: TickSource) -> int:
        """Drain the source. Returns the count of fills emitted.

        When the source is a ``LiveWsTickSource``, its ``stop()``
        method is called in the finally block to unsubscribe from
        the multiplexer.
        """
        fills = 0
        last_price_per_ticker: dict[str, Decimal] = {}
        try:
            async for tick in source:
                last_price_per_ticker[tick.ticker] = Decimal(str(tick.ltp))
                self._resampler.feed(tick)
                for bar in self._resampler.pop_completed():
                    fills += self._on_bar_close(
                        bar=bar,
                        last_price=last_price_per_ticker.get(
                            bar.ticker,
                            Decimal(str(bar.close)),
                        ),
                    )
        finally:
            for bar in self._resampler.close_partial_bars():
                fills += self._on_bar_close(
                    bar=bar,
                    last_price=last_price_per_ticker.get(
                        bar.ticker,
                        Decimal(str(bar.close)),
                    ),
                )
            if self._events:
                flush_events(self._events)
                self._events = []
            # ASETPLTFRM-417 / FE-5.1 — drain the per-session
            # feature snapshot buffer in ONE Iceberg commit.
            # Non-fatal: failure logs + buffer is cleared so a
            # later session reuse can't pick up stale rows.
            try:
                from backend.algo.features.snapshots_buffer import (
                    get_buffer,
                )

                get_buffer().flush(
                    key=(
                        str(self._strategy.id),
                        str(self._session_id),
                    ),
                )
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "[fe5.1] snapshots buffer flush failed for "
                    "paper session_id=%s (non-fatal)",
                    self._session_id,
                )
            # For live-ws sources, unsubscribe from the multiplexer.
            if hasattr(source, "stop"):
                try:
                    await source.stop()
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "LiveWsTickSource.stop() raised",
                        exc_info=True,
                    )
        return fills

    def _on_bar_close(
        self,
        *,
        bar,  # noqa: ANN001 — Bar
        last_price: Decimal,
    ) -> int:
        """Evaluate the strategy on this bar; route accepted
        signals to the broker. Returns the count of fills."""
        # Best-effort: publish the bar close as the live LTP for
        # this ticker so the paper P&L summary endpoint can mark
        # open positions to the latest fixture/live tick price.
        # 60s TTL — replay sessions that have moved on past a
        # ticker leave stale marks for at most a minute, after
        # which the summary endpoint falls back to OHLCV close.
        try:
            from backend.cache import get_cache

            get_cache().set(
                f"cache:ltp:{bar.ticker}",
                str(float(bar.close)),
                ttl=60,
            )
        except Exception:  # noqa: BLE001
            pass
        existing_pos = self._positions.open_positions().get(bar.ticker)
        bar_date_obj = datetime.fromtimestamp(
            bar.bar_open_ts_ns / 1_000_000_000,
            tz=timezone.utc,
        ).date()

        # Append to per-ticker history + recompute indicators
        # over the full series. The strategy AST may reference
        # SMAs / golden_cross_days_ago that the minimal stub
        # _features_for_bar can't provide.
        # Stream.Bar uses float OHLCV; backtest indicators expect
        # Decimal + a `.date` attr — adapt via a tiny shim.
        from backend.algo.backtest.indicators import (
            compute_indicators,
        )
        from backend.algo.backtest.types import (
            BarData as _BackBar,
        )

        adapted = _BackBar(
            ticker=bar.ticker,
            date=bar_date_obj,
            open=Decimal(str(bar.open)),
            high=Decimal(str(bar.high)),
            low=Decimal(str(bar.low)),
            close=Decimal(str(bar.close)),
            volume=int(bar.volume),
        )
        history = self._bars_by_ticker.setdefault(bar.ticker, [])
        history.append(adapted)
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
                    mode="paper",
                )
        except Exception:
            _logger.exception(
                "[paper] FE-10 feature emission hook failed "
                "(non-fatal): ticker=%s",
                bar.ticker,
            )
        # REGIME-2a — lazy-load cached factor rows for this
        # ticker on first sight; subsequent bars are O(1).
        self._ensure_factor_cache(bar.ticker, bar_date_obj)
        self._ensure_regime_cache(bar_date_obj)
        self._ensure_daily_overlay_cache(bar.ticker, bar_date_obj)
        # FE-15b — shared per-bar feature assembly (single
        # source of truth across backtest/paper/live).
        features = assemble_per_bar_features(
            bar_feats=ind_map.get(
                bar_date_obj, _features_for_bar(bar),
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

        # Stop-loss enforcement (universal v1, long-only). Run
        # BEFORE per-ticker AST eval so an in-flight stop blocks
        # any conflicting AST action on the same bar. The monitor
        # is shared with the backtest runner; here we scope the
        # check to ``bar.ticker`` since paper closes one ticker's
        # bar at a time (other tickers' stops fire on their own
        # bar-close events).
        stop_loss_triggered = False
        if existing_pos is not None and existing_pos.qty > 0:
            sl_triggers = check_stop_loss_triggers(
                open_positions={
                    bar.ticker: {
                        "qty": existing_pos.qty,
                        "avg_price": existing_pos.avg_price,
                    },
                },
                current_closes={bar.ticker: Decimal(str(bar.close))},
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
                sl_fill = self._broker.execute(
                    signal=sl_signal,
                    last_price=last_price,
                    fill_date=bar_date_obj,
                )
                self._positions.apply_fill(sl_fill)
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="paper",
                        type_="order_filled",
                        payload={
                            "ticker": sl_fill.ticker,
                            "side": sl_fill.side,
                            "qty": sl_fill.qty,
                            "fill_price": str(sl_fill.fill_price),
                            "fill_date": (
                                sl_fill.fill_date.isoformat()
                            ),
                            "fees_inr": str(sl_fill.fees_inr),
                            "fee_rates_version": (
                                sl_fill.fee_rates_version
                            ),
                            "exit_reason": "stop_loss",
                        },
                    )
                )
                stop_loss_triggered = True
                # ASETPLTFRM-436 — keep cooldown gate in sync.
                from backend.algo.backtest.cooldown_hydration \
                    import _HydratedClose
                self._cooldown_history.append(_HydratedClose(
                    ticker=trig.ticker,
                    exit_reason="stop_loss",
                    closed_at=bar_date_obj,
                ))
                # INFO (not DEBUG) — paper is a real-time simulator;
                # operators need stops visible without flipping the
                # log level.
                _logger.info(
                    "paper stop_loss SELL %s qty=%d avg=%.4f "
                    "close=%.4f loss=%.2f%% threshold=%.2f%%",
                    trig.ticker,
                    existing_pos.qty,
                    float(trig.avg_price),
                    float(trig.current_close),
                    float(trig.loss_pct),
                    float(trig.stop_loss_pct),
                )
        if stop_loss_triggered:
            # Same-bar skip: AST eval must NOT run for a ticker
            # that just stopped out — mirrors backtest semantics.
            return 1

        # ASETPLTFRM-436 — time-stop monitor (sibling to the
        # stop-loss block above). Force-exits positions held
        # >= max_holding_days. Mirrors backtest runner pattern.
        time_stop_triggered = False
        if existing_pos and existing_pos.qty > 0:
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
                    signal_id=uuid4(),
                    strategy_id=self._strategy.id,
                    user_id=self._user_id,
                    ticker=trig.ticker,
                    side="SELL",
                    qty=existing_pos.qty,
                    emitted_at_ns=bar.bar_open_ts_ns,
                    reason="time_stop",
                )
                try:
                    ts_fill = self._broker.execute(
                        signal=ts_signal,
                        last_price=last_price,
                        fill_date=bar_date_obj,
                    )
                except Exception as exc:  # noqa: BLE001
                    _logger.error(
                        "paper time_stop fill failed for %s: %s",
                        trig.ticker, exc, exc_info=True,
                    )
                    continue
                self._positions.apply_fill(ts_fill)
                self._events.append(
                    event_row(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        strategy_id=self._strategy.id,
                        mode="paper",
                        type_="order_filled",
                        payload={
                            "ticker": ts_fill.ticker,
                            "side": ts_fill.side,
                            "qty": ts_fill.qty,
                            "fill_price": str(ts_fill.fill_price),
                            "fill_date": (
                                ts_fill.fill_date.isoformat()
                            ),
                            "fees_inr": str(ts_fill.fees_inr),
                            "fee_rates_version": (
                                ts_fill.fee_rates_version
                            ),
                            "exit_reason": "time_stop",
                        },
                    )
                )
                # Keep the in-process cooldown history in sync
                # so the gate fires on next-day re-entry attempts
                # without waiting for the next algo.events flush.
                from backend.algo.backtest.cooldown_hydration \
                    import _HydratedClose
                self._cooldown_history.append(_HydratedClose(
                    ticker=trig.ticker,
                    exit_reason="time_stop",
                    closed_at=bar_date_obj,
                ))
                time_stop_triggered = True
                _logger.info(
                    "paper time_stop SELL %s qty=%d held=%d "
                    "days (threshold=%d)",
                    trig.ticker, existing_pos.qty,
                    trig.holding_days, trig.max_holding_days,
                )
        if time_stop_triggered:
            return 1

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
            # Strategy referenced a feature we don't yet have
            # (typical at session start before SMA windows
            # accumulate). No-op for this bar; fires once
            # history fills in. Same semantics as the backtest
            # runner's KeyError guard.
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
        # exits land via stop_loss / time_stop above. Mirrors the
        # backtest runner's pre-AST-eval position; here it lands
        # post-AST because paper evaluates one ticker per bar (the
        # AST eval is cheap and the cooldown skip is rare).
        cd_days = (
            self._strategy.risk.per_trade
            .cooldown_after_failed_exit_days
        )
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
                "paper cooldown SKIP %s — recent failed exit "
                "within %d days",
                bar.ticker, cd_days,
            )
            return 0

        # MIS "no new entries after T-1h" gate — same rule the
        # backtest runner enforces, imported from the shared
        # helper so all four runtimes (backtest / paper / dry-run
        # / live) stay in lockstep. SELL / exit signals are
        # unaffected.
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
                        mode="paper",
                        type_="signal_rejected",
                        payload={
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
        self._events.append(
            event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="paper",
                type_="signal_generated",
                payload={
                    "ticker": signal.ticker,
                    "side": signal.side,
                    "qty": signal.qty,
                    # REGIME-6 — attribution context. Backward
                    # compatible additive keys; readers must use
                    # .get() since pre-v3 events lack them.
                    **_attribution_payload_extension(features),
                },
            )
        )

        account = self._account_snapshot()
        decision = self._risk.gate(
            signal=signal,
            account=account,
            risk=self._strategy.risk.model_dump(),
            last_price=last_price,
        )
        if decision.outcome == "reject":
            self._events.append(
                event_row(
                    session_id=self._session_id,
                    user_id=self._user_id,
                    strategy_id=self._strategy.id,
                    mode="paper",
                    type_="signal_rejected",
                    payload={
                        "reason": (
                            decision.reason.value
                            if decision.reason
                            else "unknown"
                        ),
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
        fill = self._broker.execute(
            signal=signal,
            last_price=last_price,
            fill_date=bar_date_obj,
        )
        self._positions.apply_fill(fill)
        self._events.append(
            event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="paper",
                type_="order_filled",
                payload={
                    "ticker": fill.ticker,
                    "side": fill.side,
                    "qty": fill.qty,
                    "fill_price": str(fill.fill_price),
                    "fill_date": fill.fill_date.isoformat(),
                    "fees_inr": str(fill.fees_inr),
                    "fee_rates_version": fill.fee_rates_version,
                },
            )
        )

        # ASETPLTFRM-402 / FE-5 — per-fill feature snapshot
        # for alpha research. ADDITIVE write to
        # stocks.trade_feature_snapshots; the promotion
        # gate's algo.events ``mode='paper' AND
        # type='order_filled'`` scan is untouched.
        # Snapshot failure never blocks the fill.
        try:
            from backend.algo.features.snapshots import (
                write_trade_feature_snapshot,
            )

            _snap_fid = f"{self._session_id}:{fill.ticker}:{fill.intent_id}"
            write_trade_feature_snapshot(
                fill_id=_snap_fid,
                run_id=str(self._session_id),
                strategy_id=str(self._strategy.id),
                ticker=fill.ticker,
                side=fill.side,
                qty=fill.qty,
                fill_price=fill.fill_price,
                fill_ts_ns=(
                    fill.fill_ts_ns
                    if fill.fill_ts_ns is not None
                    else bar.bar_open_ts_ns
                ),
                bar_date=fill.fill_date.isoformat(),
                mode="paper",
                features=features,
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "trade_feature_snapshot hook failed "
                "(non-fatal): ticker=%s mode=paper",
                fill.ticker,
            )
        return 1

    def _action_to_signal(
        self,
        action: dict,
        *,
        ticker: str,
        bar_date_ns: int,
        last_price: Decimal | None = None,
    ) -> Signal | None:
        t = action.get("type")
        if t == "buy":
            qty_spec = action["qty"]
            # REGIME-4 — vol-target / Kelly route through composer
            # using paper-runtime NAV + factor cache. Legacy
            # {shares} bypasses for byte-for-byte compat.
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
            )
        if t == "set_target_weight":
            # Mirror of the backtest runner's resolution:
            # target_qty = floor(weight * current_equity / last_price)
            # Diff vs existing position drives the signal direction.
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
                )
            if diff < 0:
                return Signal(
                    strategy_id=self._strategy.id,
                    user_id=self._user_id,
                    ticker=ticker,
                    side="SELL",
                    qty=int(-diff),
                    emitted_at_ns=bar_date_ns,
                )
            return None
        return None

    def _size_via_composer(
        self,
        *,
        qty_spec: dict,
        ticker: str,
        bar_date_ns: int,
        last_price: Decimal | None,
    ) -> int:
        """REGIME-4 — assemble SizingContext from runtime state and
        delegate to compose_qty.  Returns 0 on missing inputs (e.g.
        no realized_vol_60d for the ticker) — caller treats as
        "skip"."""
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

    def _account_snapshot(self) -> AccountState:
        open_qty = {
            t: p.qty for t, p in self._positions.open_positions().items()
        }
        # Approximate equity = initial + realised. Unrealised
        # left to caller-supplied marks (Slice 8b reconciles
        # with live ticks).
        return AccountState(
            user_id=self._user_id,
            day_date=datetime.now(timezone.utc).date(),
            initial_capital_inr=self._initial,
            current_equity_inr=(
                self._initial + self._positions.total_realised_pnl_inr()
            ),
            daily_realised_pnl_inr=(self._positions.total_realised_pnl_inr()),
            daily_unrealised_pnl_inr=Decimal("0"),
            open_positions=open_qty,
            open_position_count=len(open_qty),
            kill_switch_active=self._kill_switch_active,
        )

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
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from backend.algo.attribution.payload import (
    attribution_payload_extension as _attribution_payload_extension,
)
from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.positions import PositionTracker
from backend.algo.broker.kite_client import KiteClient
# REGIME-2a — pre-computed nightly factor library overlay.
from backend.algo.factors.repo import get_factors_window
from backend.algo.live.safety import (
    LiveRejectReason, pre_trade_check,
)
from backend.algo.paper.types import AccountState, Signal
# REGIME-4 — vol-target / Kelly sizer (legacy modes bypass).
from backend.algo.sizing.composer import SizingContext, compose_qty
from backend.algo.stream.resampler import Resampler
from backend.algo.stream.sources import TickSource
from backend.algo.strategy.ast import Strategy

_logger = logging.getLogger(__name__)

UTC = timezone.utc


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
        caps: dict[str, Any],
        run_id: UUID,
        caps_repo: Any,
        kill_switch_repo: Any,
    ) -> None:
        if not caps.get("live_orders_enabled"):
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
        self._dry_run: bool = bool(getattr(kite, "dry_run", False))
        self._caps = caps
        self._run_id = run_id
        self._caps_repo = caps_repo
        self._kill_switch_repo = kill_switch_repo
        self._evaluator = Evaluator()
        self._resampler = Resampler(intervals=(60,))
        self._positions = PositionTracker()
        self._session_id = uuid4()
        self._events: list[dict[str, Any]] = []
        self._in_flight: list[dict[str, Any]] = []
        self._bars_by_ticker: dict[str, list] = {}

        # Pre-load NIFTY regime + trend features so strategies
        # gated on ``nifty_above_sma200`` / ``nifty_30d_return_pct``
        # don't silent-fail every bar with a KeyError. Same
        # pattern as PaperRuntime + the backtest runner.
        self._market_regime: dict[Any, Decimal] = {}
        self._market_trend: dict[Any, Decimal] = {}
        try:
            from datetime import date as _date, timedelta
            from backend.algo.backtest.indicators import (
                compute_market_regime as _cmr,
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

        # REGIME-2a — per-ticker cached factor row, lazy-loaded
        # on first sight of a ticker (same rationale as
        # PaperRuntime: ``strategy.universe`` is a scope spec,
        # not a ticker list).
        self._factor_cache: dict[
            tuple[str, date], dict[str, Decimal]
        ] = {}
        self._factor_loaded_for_ticker: set[str] = set()
        # REGIME-1 — regime_label + stress_prob lookup, loaded
        # lazily on first bar so live sessions resolve regime
        # features identically to backtest + paper.
        self._regime_by_date: dict[date, dict[str, Any]] = {}
        self._regime_loaded: bool = False

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
                "regime-aware templates will silent-skip", exc,
            )

    def _ensure_factor_cache(
        self, ticker: str, bar_date_obj: date,
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
                    for k, v in r.values.items() if v is not None
                }
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "LiveRuntime: factor cache load for %s failed: "
                "%s — strategies referencing factor.* keys will "
                "silent-skip until backfill catches up",
                ticker, exc,
            )

    # ----------------------------------------------------------
    # Main run loop
    # ----------------------------------------------------------

    async def run(self, source: TickSource) -> int:
        """Drain the tick source. Returns fill count."""
        fills = 0
        last_price_per_ticker: dict[str, Decimal] = {}
        tick_count = 0
        bar_count = 0
        signal_count = 0
        _logger.info(
            "LiveRuntime: starting drain user=%s strat=%s "
            "run_id=%s",
            self._user_id, self._strategy.id, self._run_id,
        )
        try:
            async for tick in source:
                tick_count += 1
                if tick_count == 1 or tick_count % 500 == 0:
                    _logger.info(
                        "LiveRuntime: tick #%d ticker=%s",
                        tick_count, tick.ticker,
                    )
                last_price_per_ticker[tick.ticker] = (
                    Decimal(str(tick.ltp))
                )
                self._resampler.feed(tick)
                for bar in self._resampler.pop_completed():
                    bar_count += 1
                    lp = last_price_per_ticker.get(
                        bar.ticker, Decimal(str(bar.close)),
                    )
                    n = await self._on_bar_close(
                        bar=bar, last_price=lp,
                    )
                    fills += n
                    if n > 0:
                        signal_count += n
            _logger.info(
                "LiveRuntime: drain complete ticks=%d bars=%d "
                "fills=%d events_buffered=%d",
                tick_count, bar_count, fills, len(self._events),
            )
        finally:
            for bar in self._resampler.close_partial_bars():
                lp = last_price_per_ticker.get(
                    bar.ticker, Decimal(str(bar.close)),
                )
                fills += await self._on_bar_close(
                    bar=bar, last_price=lp,
                )
            if self._events:
                _logger.info(
                    "LiveRuntime: flushing %d events to "
                    "algo.events", len(self._events),
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
    ) -> int:
        """Evaluate → gate → submit to Kite. Returns 1 if filled."""
        from backend.algo.backtest.indicators import compute_indicators
        from backend.algo.backtest.types import BarData as _BackBar
        from datetime import datetime, timezone

        bar_date_obj = datetime.fromtimestamp(
            bar.bar_open_ts_ns / 1_000_000_000,
            tz=timezone.utc,
        ).date()
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
        # REGIME-2a — lazy-load cached factor rows for this
        # ticker on first sight; subsequent bars are O(1).
        self._ensure_factor_cache(bar.ticker, bar_date_obj)
        self._ensure_regime_cache(bar_date_obj)
        features = {
            **ind_map.get(
                bar_date_obj,
                {
                    "today_ltp": bar.close,
                    "today_vol": Decimal(bar.volume),
                },
            ),
            "nifty_above_sma200": self._market_regime.get(
                bar_date_obj, Decimal("0"),
            ),
            "nifty_30d_return_pct": self._market_trend.get(
                bar_date_obj, Decimal("0"),
            ),
            # REGIME-2a — cached factor row overlay (disjoint
            # from indicator + regime keys by design).
            **self._factor_cache.get(
                (bar.ticker, bar_date_obj), {},
            ),
            # REGIME-1 — regime_label + stress_prob overlay.
            **self._regime_by_date.get(bar_date_obj, {}),
        }

        existing_pos = self._positions.open_positions().get(bar.ticker)
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

        self._events.append(event_row(
            session_id=self._session_id,
            user_id=self._user_id,
            strategy_id=self._strategy.id,
            mode="live",
            type_="signal_generated",
            payload={
                "dry_run": self._dry_run,
                "ticker": signal.ticker,
                "side": signal.side,
                "qty": signal.qty,
                # REGIME-6 — attribution context (additive).
                **_attribution_payload_extension(features),
            },
        ))

        # Fresh caps read — do NOT trust stale constructor copy
        # for daily counters; read atomically.
        current_caps = await self._caps_repo.get(
            self._user_id, self._strategy.id,
        ) or self._caps

        account = self._account_snapshot(
            kill_switch_active=await self._kill_switch_repo
            .is_active(self._user_id),
        )
        day_state = {
            "cumulative_inr_today": current_caps.get(
                "cumulative_inr_today", Decimal("0"),
            ),
            "orders_count_today": current_caps.get(
                "orders_count_today", 0,
            ),
        }

        decision = pre_trade_check(
            signal=signal,
            caps=current_caps,
            day_state=day_state,
            account=account,
            strategy_risk=self._strategy.risk.model_dump(),
            last_price=last_price,
        )

        if decision.outcome == "reject":
            reason_str = (
                decision.reason.value
                if hasattr(decision.reason, "value")
                else str(decision.reason)
                if decision.reason else "unknown"
            )
            self._events.append(event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="live",
                type_="signal_rejected",
                payload={
                    "dry_run": self._dry_run,
                    "reason": reason_str,
                    "ticker": signal.ticker,
                    "side": signal.side,
                    "qty": signal.qty,
                    "threshold": (
                        str(decision.threshold)
                        if decision.threshold is not None else None
                    ),
                    "observed_value": (
                        str(decision.observed_value)
                        if decision.observed_value is not None
                        else None
                    ),
                },
            ))
            return 0

        effective_qty = (
            decision.adjusted_qty
            if decision.outcome == "scale" and decision.adjusted_qty
            else signal.qty
        )
        signal = signal.model_copy(update={"qty": effective_qty})

        return await self._submit_order(
            signal=signal, last_price=last_price,
        )

    # ----------------------------------------------------------
    # Kite order submission
    # ----------------------------------------------------------

    async def _submit_order(
        self,
        *,
        signal: Signal,
        last_price: Decimal,
    ) -> int:
        """Submit one order to Kite. Returns 1 on success, 0 on error."""
        internal_order_id = str(uuid4())
        now_iso = datetime.now(UTC).isoformat()

        # Determine exchange — Indian .NS → NSE
        exchange = "NSE"
        symbol = signal.ticker.replace(".NS", "")
        side = "BUY" if signal.side == "BUY" else "SELL"

        try:
            kite_order_id = await asyncio.to_thread(
                self._kite.place_order,
                tradingsymbol=symbol,
                exchange=exchange,
                transaction_type=side,
                quantity=signal.qty,
                order_type="MARKET",
                product="CNC",
                variety="regular",
                tag=f"algo-{str(self._strategy.id)[:8]}",
            )
        except Exception as exc:
            rejection_reason = str(exc)
            self._events.append(event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="live",
                type_="order_rejected_live",
                payload={
                    "dry_run": self._dry_run,
                    "internal_order_id": internal_order_id,
                    "symbol": symbol,
                    "side": side,
                    "qty": signal.qty,
                    "rejection_reason": rejection_reason,
                    "kite_order_id": None,
                },
            ))
            _logger.error(
                "live order rejected: symbol=%s side=%s qty=%d "
                "reason=%s",
                symbol, side, signal.qty, rejection_reason,
            )
            return 0

        is_dry = kite_order_id.startswith("DRY_")

        # Record in-flight
        in_flight_entry = {
            "kite_order_id": kite_order_id,
            "internal_order_id": internal_order_id,
            "symbol": symbol,
            "side": side,
            "qty": signal.qty,
            "submitted_at": now_iso,
            "status": "submitted",
        }
        self._in_flight.append(in_flight_entry)
        await self._caps_repo.update_in_flight(
            self._user_id, self._run_id, self._in_flight,
        )

        # Persist order_submitted_live event
        self._events.append(event_row(
            session_id=self._session_id,
            user_id=self._user_id,
            strategy_id=self._strategy.id,
            mode="live",
            type_="order_submitted_live",
            payload={
                "dry_run": self._dry_run,
                "internal_order_id": internal_order_id,
                "kite_order_id": kite_order_id,
                "symbol": symbol,
                "side": side,
                "qty": signal.qty,
                "order_type": "MARKET",
                "limit_price": None,
                "dry_run": is_dry,
            },
        ))

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
                ),
                name=f"dry_fill_{kite_order_id}",
            )

        # Bump daily counters
        order_notional = Decimal(signal.qty) * last_price
        await self._caps_repo.increment_daily_counters(
            self._user_id, self._strategy.id,
            inr_amount=order_notional,
        )

        _logger.info(
            "live order submitted: symbol=%s side=%s qty=%d "
            "kite_order_id=%s internal=%s",
            symbol, side, signal.qty,
            kite_order_id, internal_order_id,
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
    ) -> None:
        """Simulate a Kite fill for dry-run mode.

        Sleeps ``_DRY_FILL_DELAY_S`` then:
        1. Computes fees via IndianFeeModel.
        2. Applies the fill to PositionTracker.
        3. Emits an ``order_filled_live`` event with
           ``dry_run: true`` in the payload.
        4. Marks the in-flight entry as filled.
        """
        await asyncio.sleep(self._DRY_FILL_DELAY_S)

        from backend.algo.backtest.types import Fill
        from backend.algo.fees import IndianFeeModel, Trade

        today = datetime.now(UTC).date()
        fee_model = IndianFeeModel(as_of=today)
        product = "DELIVERY"
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

        # Mark in-flight entry filled
        in_flight_entry["status"] = "filled"

        # Emit fill event
        self._events.append(event_row(
            session_id=self._session_id,
            user_id=self._user_id,
            strategy_id=self._strategy.id,
            mode="live",
            type_="order_filled_live",
            payload={
                "dry_run": self._dry_run,
                "internal_order_id": internal_order_id,
                "kite_order_id": kite_order_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": str(fill_price),
                "fees_inr": str(fees.total_inr),
                "dry_run": True,
            },
        ))

        _logger.info(
            "[DRY_RUN] synthetic fill: symbol=%s side=%s qty=%d "
            "price=%s fees=%s kite_order_id=%s",
            symbol, side, qty, fill_price,
            fees.total_inr, kite_order_id,
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
            e for e in self._in_flight
            if e.get("status") == "submitted"
        ]
        cancelled = 0
        failed = 0
        for entry in in_flight:
            kite_id = entry.get("kite_order_id")
            if not kite_id:
                continue
            try:
                await asyncio.to_thread(
                    self._kite.cancel_order, kite_id,
                )
                entry["status"] = "cancelled"
                cancelled += 1
                self._events.append(event_row(
                    session_id=self._session_id,
                    user_id=self._user_id,
                    strategy_id=self._strategy.id,
                    mode="live",
                    type_="order_cancelled_live",
                    payload={
                        "dry_run": self._dry_run,
                        "kite_order_id": kite_id,
                        "reason": "kill_switch_armed",
                    },
                ))
            except Exception as exc:
                failed += 1
                _logger.error(
                    "cancel_in_flight FAILED: kite_order_id=%s "
                    "error=%s",
                    kite_id, exc,
                )
                self._events.append(event_row(
                    session_id=self._session_id,
                    user_id=self._user_id,
                    strategy_id=self._strategy.id,
                    mode="live",
                    type_="order_cancel_failed",
                    payload={
                        "dry_run": self._dry_run,
                        "kite_order_id": kite_id,
                        "error": str(exc),
                    },
                ))

        # Persist updated in-flight list
        await self._caps_repo.update_in_flight(
            self._user_id, self._run_id, self._in_flight,
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
        self, *, kill_switch_active: bool = False,
    ) -> AccountState:
        open_qty = {
            t: p.qty
            for t, p in self._positions.open_positions().items()
        }
        return AccountState(
            user_id=self._user_id,
            day_date=datetime.now(UTC).date(),
            initial_capital_inr=self._initial,
            current_equity_inr=(
                self._initial
                + self._positions.total_realised_pnl_inr()
            ),
            daily_realised_pnl_inr=(
                self._positions.total_realised_pnl_inr()
            ),
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
            bar_date_ns / 1_000_000_000, tz=timezone.utc,
        ).date()
        nav = (
            self._initial
            + self._positions.total_realised_pnl_inr()
        )
        factor_row = self._factor_cache.get(
            (ticker, bar_date_obj), {},
        )
        realized_vol = factor_row.get(
            "realized_vol_60d", Decimal("NaN"),
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
            if (
                "vol_target_pct" in qty_spec
                or "kelly_fraction" in qty_spec
            ):
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
                ticker=ticker, side="BUY", qty=qty,
                emitted_at_ns=bar_date_ns,
            )
        if t == "sell":
            qty_spec = action["qty"]
            if qty_spec.get("all"):
                existing = (
                    self._positions.open_positions().get(ticker)
                )
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
                ticker=ticker, side="SELL", qty=qty,
                emitted_at_ns=bar_date_ns,
            )
        if t == "exit":
            existing = self._positions.open_positions().get(ticker)
            if not existing:
                return None
            return Signal(
                strategy_id=self._strategy.id,
                user_id=self._user_id,
                ticker=ticker, side="SELL",
                qty=existing.qty,
                emitted_at_ns=bar_date_ns,
            )
        if t == "set_target_weight":
            if last_price is None or last_price <= 0:
                return None
            current_equity = (
                self._initial
                + self._positions.total_realised_pnl_inr()
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
                    ticker=ticker, side="BUY",
                    qty=int(diff),
                    emitted_at_ns=bar_date_ns,
                )
            if diff < 0:
                return Signal(
                    strategy_id=self._strategy.id,
                    user_id=self._user_id,
                    ticker=ticker, side="SELL",
                    qty=int(-diff),
                    emitted_at_ns=bar_date_ns,
                )
            return None
        return None

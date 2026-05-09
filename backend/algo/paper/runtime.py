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

from backend.algo.backtest.event_writer import (
    event_row, flush_events,
)
from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.positions import PositionTracker
from backend.algo.paper.broker import PaperBroker
from backend.algo.paper.risk_engine import RiskEngine
from backend.algo.paper.types import AccountState, Signal
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

    async def run(self, source: TickSource) -> int:
        """Drain the source. Returns the count of fills emitted."""
        fills = 0
        last_price_per_ticker: dict[str, Decimal] = {}
        try:
            async for tick in source:
                last_price_per_ticker[tick.ticker] = (
                    Decimal(str(tick.ltp))
                )
                self._resampler.feed(tick)
                for bar in self._resampler.pop_completed():
                    fills += self._on_bar_close(
                        bar=bar,
                        last_price=last_price_per_ticker.get(
                            bar.ticker, Decimal(str(bar.close)),
                        ),
                    )
        finally:
            for bar in self._resampler.close_partial_bars():
                fills += self._on_bar_close(
                    bar=bar,
                    last_price=last_price_per_ticker.get(
                        bar.ticker, Decimal(str(bar.close)),
                    ),
                )
            if self._events:
                flush_events(self._events)
                self._events = []
        return fills

    def _on_bar_close(
        self,
        *,
        bar,  # noqa: ANN001 — Bar
        last_price: Decimal,
    ) -> int:
        """Evaluate the strategy on this bar; route accepted
        signals to the broker. Returns the count of fills."""
        existing_pos = self._positions.open_positions().get(bar.ticker)
        bar_date_obj = datetime.fromtimestamp(
            bar.bar_open_ts_ns / 1_000_000_000, tz=timezone.utc,
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
        features = ind_map.get(
            bar_date_obj, _features_for_bar(bar),
        )

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
        )
        if signal is None:
            return 0
        self._events.append(event_row(
            session_id=self._session_id,
            user_id=self._user_id,
            strategy_id=self._strategy.id,
            mode="paper",
            type_="signal_generated",
            payload={
                "ticker": signal.ticker, "side": signal.side,
                "qty": signal.qty,
            },
        ))

        account = self._account_snapshot()
        decision = self._risk.gate(
            signal=signal, account=account,
            risk=self._strategy.risk.model_dump(),
            last_price=last_price,
        )
        if decision.outcome == "reject":
            self._events.append(event_row(
                session_id=self._session_id,
                user_id=self._user_id,
                strategy_id=self._strategy.id,
                mode="paper",
                type_="signal_rejected",
                payload={
                    "reason": (
                        decision.reason.value
                        if decision.reason else "unknown"
                    ),
                    "ticker": signal.ticker, "side": signal.side,
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
            if decision.outcome == "scale"
            and decision.adjusted_qty
            else signal.qty
        )
        signal = signal.model_copy(update={"qty": effective_qty})
        fill = self._broker.execute(
            signal=signal,
            last_price=last_price,
            fill_date=bar_date_obj,
        )
        self._positions.apply_fill(fill)
        self._events.append(event_row(
            session_id=self._session_id,
            user_id=self._user_id,
            strategy_id=self._strategy.id,
            mode="paper",
            type_="order_filled",
            payload={
                "ticker": fill.ticker, "side": fill.side,
                "qty": fill.qty,
                "fill_price": str(fill.fill_price),
                "fill_date": fill.fill_date.isoformat(),
                "fees_inr": str(fill.fees_inr),
                "fee_rates_version": fill.fee_rates_version,
            },
        ))
        return 1

    def _action_to_signal(
        self,
        action: dict,
        *,
        ticker: str,
        bar_date_ns: int,
    ) -> Signal | None:
        t = action.get("type")
        if t == "buy":
            qty = int(action["qty"].get("shares") or 0)
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
                ticker=ticker, side="SELL", qty=existing.qty,
                emitted_at_ns=bar_date_ns,
            )
        return None

    def _account_snapshot(self) -> AccountState:
        open_qty = {
            t: p.qty
            for t, p in self._positions.open_positions().items()
        }
        # Approximate equity = initial + realised. Unrealised
        # left to caller-supplied marks (Slice 8b reconciles
        # with live ticks).
        return AccountState(
            user_id=self._user_id,
            day_date=datetime.now(timezone.utc).date(),
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
            kill_switch_active=self._kill_switch_active,
        )

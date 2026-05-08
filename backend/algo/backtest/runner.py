"""Backtest orchestrator. Walks daily bars over a closed period,
evaluates the strategy AST per (ticker, bar), routes action
results to SimBroker, accumulates positions, and emits an event
log + summary.

Per CLAUDE.md §4.1: single bulk OHLCV read, single Iceberg
commit at the end (not per-event), no per-ticker hot loops.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from backend.algo.backtest.data_source import load_ohlcv_window
from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.sim_broker import (
    NoBarAvailableError,
    SimBroker,
)
from backend.algo.backtest.types import (
    BacktestRequest,
    BacktestSummary,
    EquityPoint,
    OrderIntent,
    TradeRow,
)
from backend.algo.strategy.ast import Strategy

_logger = logging.getLogger(__name__)


def _features_for_bar(bar) -> dict[str, Decimal]:  # noqa: ANN001
    """Minimal v1 feature map — runner currently only exposes
    OHLCV-derived leaves. Slice 7b extends with technical /
    fundamental joins."""
    return {
        "today_ltp": bar.close,
        "today_vol": Decimal(bar.volume),
    }


def _trade_row(p, fill_price: Decimal) -> TradeRow:  # noqa: ANN001
    """Project a closed Position into a TradeRow for the UI."""
    holding_days = (
        (p.closed_at - p.opened_at).days if p.closed_at else 0
    )
    return_pct = (
        ((fill_price - p.avg_price) / p.avg_price) * Decimal("100")
        if p.avg_price > 0 else Decimal("0")
    )
    return TradeRow(
        ticker=p.ticker,
        qty=p.qty,
        avg_price=p.avg_price,
        fill_price=fill_price,
        opened_at=p.opened_at,
        closed_at=p.closed_at,
        holding_days=holding_days,
        realised_pnl_inr=p.realised_pnl_inr,
        return_pct=return_pct,
    )


def run_backtest(
    *,
    strategy: Strategy,
    request: BacktestRequest,
    user_id: UUID,
    universe: list[str],
) -> BacktestSummary:
    """Run a backtest end-to-end and return the summary.

    Caller responsibilities:
    - Persist the Strategy AST and generate ``request``.
    - Resolve ``universe`` from ``strategy.universe`` (Slice 7
      uses the user's watchlist union holdings; this function
      treats it as opaque input).
    - Persist the returned ``BacktestSummary`` to ``algo.runs``
      and the events emitted by ``flush_events`` to
      ``algo.events`` — both happen automatically at the end of
      this call.
    """
    started_at = datetime.now(timezone.utc)
    run_id = uuid4()
    session_id = run_id
    events: list[dict[str, Any]] = []

    events.append(event_row(
        session_id=session_id,
        user_id=user_id,
        strategy_id=strategy.id,
        mode="backtest",
        type_="backtest_run_started",
        payload={
            "period_start": request.period_start.isoformat(),
            "period_end": request.period_end.isoformat(),
            "universe_size": len(universe),
            "initial_capital_inr": str(request.initial_capital_inr),
        },
    ))

    bars = load_ohlcv_window(
        tickers=universe,
        period_start=request.period_start,
        period_end=request.period_end,
    )
    sim = SimBroker(bars=bars, fee_as_of=request.period_start)
    evaluator = Evaluator()
    pt = PositionTracker()

    fee_rates_version = ""
    total_fees = Decimal("0")
    equity_points: list[EquityPoint] = []
    peak_equity = request.initial_capital_inr
    max_drawdown_pct = Decimal("0")

    # Walk bars chronologically. We zip each ticker's series so
    # bar dates that are common across the universe step in lockstep.
    all_dates = sorted({
        b.date for blist in bars.values() for b in blist
    })

    for bar_date in all_dates:
        for ticker in universe:
            blist = bars.get(ticker)
            if not blist:
                continue
            current = next(
                (b for b in blist if b.date == bar_date), None,
            )
            if current is None:
                continue
            open_pos = pt.open_positions().get(ticker)
            ctx = EvalContext(
                ticker=ticker,
                bar_date=bar_date,
                features=_features_for_bar(current),
                open_qty=open_pos.qty if open_pos else 0,
            )
            action = evaluator.eval_node(
                strategy.root.model_dump(by_alias=True),
                ctx,
            )

            intent = _action_to_intent(
                action, ticker=ticker, bar_date=bar_date,
                pt=pt,
            )
            if intent is None:
                continue
            try:
                fill = sim.execute(intent)
            except NoBarAvailableError:
                continue
            if fill is None:
                continue

            pt.apply_fill(fill)
            total_fees += fill.fees_inr
            fee_rates_version = fill.fee_rates_version

            events.append(event_row(
                session_id=session_id,
                user_id=user_id,
                strategy_id=strategy.id,
                mode="backtest",
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
            ))

        # End-of-day equity snapshot.
        marks = {
            t: blist[-1].close
            for t, blist in bars.items()
            if blist and blist[-1].date <= bar_date
        }
        equity = (
            request.initial_capital_inr
            + pt.total_realised_pnl_inr()
            + pt.unrealised_pnl_inr(marks)
            - total_fees
        )
        equity_points.append(EquityPoint(
            bar_date=bar_date, equity_inr=equity,
        ))
        if equity > peak_equity:
            peak_equity = equity
        if peak_equity > 0:
            dd = (peak_equity - equity) / peak_equity * Decimal("100")
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd

    final_equity = (
        equity_points[-1].equity_inr if equity_points
        else request.initial_capital_inr
    )
    total_pnl = final_equity - request.initial_capital_inr
    total_pnl_pct = (
        (total_pnl / request.initial_capital_inr) * Decimal("100")
        if request.initial_capital_inr > 0 else Decimal("0")
    )
    closed = pt.closed_positions()
    winning = sum(1 for p in closed if p.realised_pnl_inr > 0)
    losing = sum(1 for p in closed if p.realised_pnl_inr <= 0)
    win_rate = (
        Decimal(winning) / Decimal(len(closed)) * Decimal("100")
        if closed else Decimal("0")
    )

    trade_rows: list[TradeRow] = []
    for p in closed:
        implied_fill = (
            p.avg_price + (p.realised_pnl_inr / Decimal(p.qty))
            if p.qty > 0 else p.avg_price
        )
        trade_rows.append(_trade_row(p, implied_fill))

    summary = BacktestSummary(
        run_id=run_id,
        strategy_id=strategy.id,
        status="completed",
        period_start=request.period_start,
        period_end=request.period_end,
        initial_capital_inr=request.initial_capital_inr,
        final_equity_inr=final_equity,
        total_pnl_inr=total_pnl,
        total_pnl_pct=total_pnl_pct,
        total_fees_inr=total_fees,
        total_trades=len(closed),
        winning_trades=winning,
        losing_trades=losing,
        win_rate_pct=win_rate,
        max_drawdown_pct=max_drawdown_pct,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        fee_rates_version=fee_rates_version or "n/a",
        equity_curve=equity_points,
        trade_list=trade_rows,
    )

    events.append(event_row(
        session_id=session_id,
        user_id=user_id,
        strategy_id=strategy.id,
        mode="backtest",
        type_="backtest_run_completed",
        payload=summary.model_dump(mode="json"),
    ))
    flush_events(events)
    return summary


def _action_to_intent(
    action: dict,
    *,
    ticker: str,
    bar_date,  # noqa: ANN001
    pt: PositionTracker,
) -> OrderIntent | None:
    """Translate an evaluator action dict to an OrderIntent (or None)."""
    t = action.get("type")
    if t == "buy":
        qty = action["qty"].get("shares") or 0
        if qty <= 0:
            return None
        return OrderIntent(
            ticker=ticker, side="BUY", qty=int(qty),
            intent_emitted_at=bar_date,
        )
    if t == "sell":
        qty_spec = action["qty"]
        if qty_spec.get("all"):
            existing = pt.open_positions().get(ticker)
            if not existing:
                return None
            return OrderIntent(
                ticker=ticker, side="SELL", qty=existing.qty,
                intent_emitted_at=bar_date,
            )
        qty = qty_spec.get("shares") or 0
        if qty <= 0:
            return None
        return OrderIntent(
            ticker=ticker, side="SELL", qty=int(qty),
            intent_emitted_at=bar_date,
        )
    if t == "exit":
        existing = pt.open_positions().get(ticker)
        if not existing:
            return None
        return OrderIntent(
            ticker=ticker, side="SELL", qty=existing.qty,
            intent_emitted_at=bar_date,
        )
    # set_target_weight + hold = no-op in 7a (weight resolution
    # lands in 7b alongside the universe sizer).
    return None

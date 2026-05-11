"""Backtest orchestrator. Walks daily bars over a closed period,
evaluates the strategy AST per (ticker, bar), routes action
results to SimBroker, accumulates positions, and emits an event
log + summary.

Per CLAUDE.md §4.1: single bulk OHLCV read, single Iceberg
commit at the end (not per-event), no per-ticker hot loops.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from backend.algo.backtest.data_source import load_ohlcv_window
# REGIME-7 — pre-load 60d ADTV per ticker for slippage model.
from backend.db.duckdb_engine import query_iceberg_table
from backend.algo.backtest.evaluator import EvalContext, Evaluator
from backend.algo.backtest.event_writer import event_row, flush_events
from backend.algo.backtest.indicators import (
    DEFAULT_WARMUP_BARS,
    compute_indicators_for_universe,
    compute_market_regime,
    compute_market_trend_strength,
)
from backend.algo.backtest.positions import PositionTracker
from backend.algo.backtest.sim_broker import (
    NoBarAvailableError,
    SimBroker,
)
# REGIME-2a — pre-computed nightly factor library overlay.
from backend.algo.factors.repo import get_factors_window
from backend.algo.backtest.types import (
    BacktestRequest,
    BacktestSummary,
    EquityPoint,
    OrderIntent,
    TradeRow,
)
from backend.algo.paper.risk_engine import RiskEngine
from backend.algo.paper.types import AccountState, Signal
# REGIME-4 — 3-stage sizer (vol-target / Kelly → caps → DD throttle).
from backend.algo.sizing.composer import SizingContext, compose_qty
from backend.algo.strategy.ast import Strategy

_logger = logging.getLogger(__name__)


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

    # Load with warmup history so SMA200 etc. are well-formed at
    # period_start. Indicators are computed once over the FULL
    # series; the bar walk below skips warmup-only dates.
    bars = load_ohlcv_window(
        tickers=universe,
        period_start=request.period_start,
        period_end=request.period_end,
        warmup_days=DEFAULT_WARMUP_BARS,
    )
    indicators = compute_indicators_for_universe(bars)
    # Top-level regime feature, injected into every (ticker, bar)
    # feature dict below so strategies can gate entries on
    # `{"feature": "nifty_above_sma200"}`. Empty dict if ^NSEI
    # absent → callers fall back to Decimal("0") (regime off).
    market_regime = compute_market_regime(
        period_start=request.period_start,
        period_end=request.period_end,
    )
    market_trend = compute_market_trend_strength(
        period_start=request.period_start,
        period_end=request.period_end,
    )
    # REGIME-2a — pre-load cached daily factor rows for the
    # period. Disjoint from indicator keys by design; overlaid
    # AFTER the indicator dict in the per-bar features assembly
    # below. Empty dict if backfill hasn't run yet — strategies
    # that don't reference factor keys are unaffected.
    factor_rows = get_factors_window(
        tickers=universe,
        start=request.period_start,
        end=request.period_end,
    )
    factors_by_key: dict[tuple[str, date], dict[str, Decimal]] = {}
    for r in factor_rows:
        factors_by_key[(r.ticker, r.bar_date)] = {
            k: Decimal(str(v)) for k, v in r.values.items()
            if v is not None
        }
    # REGIME-1 — pre-load regime_label + stress_prob for the
    # period so per-bar features can resolve regime-aware
    # templates (`{"feature": "regime_label"}`,
    # `{"feature": "stress_prob"}`). Empty dict if
    # regime_history is empty for this window — strategies that
    # don't reference regime keys are unaffected.
    regime_by_date: dict[date, dict[str, Any]] = {}
    try:
        from backend.algo.regime.repo import get_regime_history
        rh_rows = get_regime_history(
            request.period_start, request.period_end,
        )
        for rh in rh_rows:
            entry: dict[str, Any] = {"regime_label": rh.regime_label}
            if rh.stress_prob is not None:
                entry["stress_prob"] = Decimal(str(rh.stress_prob))
            regime_by_date[rh.bar_date] = entry
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "regime_history lookup failed (%s) — regime-aware "
            "templates will silently no-op for this run", exc,
        )
    # REGIME-7 — pre-compute 60d ADTV per ticker so SimBroker can
    # apply ``max(5, 50 * order_value / ADTV) bps`` slippage.
    # Empty universe → empty lookup → SimBroker falls back to the
    # 5bps minimum on every leg.
    adtv_lookup: dict[str, Decimal] = {}
    if universe:
        from datetime import timedelta as _td
        adtv_start = request.period_start - _td(days=90)
        try:
            adtv_rows = query_iceberg_table(
                "stocks.ohlcv",
                "SELECT ticker, AVG(close * volume) AS adtv "
                "FROM ohlcv "
                "WHERE ticker IN ({}) "
                "  AND date BETWEEN ? AND ? "
                "GROUP BY ticker".format(
                    ",".join(["?"] * len(universe))
                ),
                [*universe, adtv_start, request.period_start],
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "ADTV lookup failed (%s) — falling back to 5bps "
                "minimum slippage on every leg",
                exc,
            )
            adtv_rows = []
        for r in adtv_rows:
            adtv_lookup[r["ticker"]] = Decimal(
                str(r["adtv"] or 0)
            )

    sim = SimBroker(
        bars=bars,
        fee_as_of=request.period_start,
        adtv_lookup=adtv_lookup,
    )
    evaluator = Evaluator()
    pt = PositionTracker()
    risk = RiskEngine()
    risk_payload = strategy.risk.model_dump()

    fee_rates_version = ""
    total_fees = Decimal("0")
    equity_points: list[EquityPoint] = []
    peak_equity = request.initial_capital_inr
    max_drawdown_pct = Decimal("0")
    rejected_count = 0
    scaled_count = 0
    # Per-feature KeyError counter — surfaces the most-frequently
    # missing strategy feature so users can spot wrong/typo'd
    # references vs genuinely-not-yet-computed factors. Logged in
    # the run summary line, capped at top 5.
    _key_err_counts: dict[str, int] = {}
    # Day-bucket realised P&L so RiskEngine.daily_loss_cap fires
    # on intra-day drawdown, not cumulative-from-start. Reset at
    # the top of each bar_date.
    day_start_realised = Decimal("0")
    last_bar_date = None
    # Per-ticker most-recent close at-or-before the current
    # bar_date. Used to mark open positions to market for the
    # unrealised-P&L contribution to the daily equity curve.
    # Updated as we walk the inner loop; persists across days
    # so a ticker that doesn't trade today (holiday / new
    # listing gap) keeps its prior close as the mark.
    last_close: dict[str, Decimal] = {}

    # Walk bars chronologically. We zip each ticker's series so
    # bar dates that are common across the universe step in lockstep.
    all_dates = sorted({
        b.date for blist in bars.values() for b in blist
    })

    for bar_date in all_dates:
        # Warmup-only bars feed the indicator engine but never
        # see strategy evaluation — the user asked to backtest
        # period_start..period_end, not the warmup range.
        if bar_date < request.period_start:
            continue
        if bar_date != last_bar_date:
            day_start_realised = pt.total_realised_pnl_inr()
            last_bar_date = bar_date
        for ticker in universe:
            blist = bars.get(ticker)
            if not blist:
                continue
            current = next(
                (b for b in blist if b.date == bar_date), None,
            )
            if current is None:
                continue
            # Refresh the mark-to-market price for this ticker
            # before any strategy logic runs on the bar — the
            # end-of-day equity snapshot below uses last_close.
            last_close[ticker] = current.close
            open_pos = pt.open_positions().get(ticker)
            ticker_features = {
                **indicators.get(ticker, {}).get(
                    bar_date,
                    {
                        "today_ltp": current.close,
                        "today_vol": Decimal(current.volume),
                    },
                ),
                "nifty_above_sma200": market_regime.get(
                    bar_date, Decimal("0"),
                ),
                "nifty_30d_return_pct": market_trend.get(
                    bar_date, Decimal("0"),
                ),
                # REGIME-2a — cached factor row overlay (disjoint
                # from indicator keys by design).
                **factors_by_key.get((ticker, bar_date), {}),
                # REGIME-1 — regime_label + stress_prob overlay.
                **regime_by_date.get(bar_date, {}),
            }
            ctx = EvalContext(
                ticker=ticker,
                bar_date=bar_date,
                features=ticker_features,
                open_qty=open_pos.qty if open_pos else 0,
            )
            try:
                action = evaluator.eval_node(
                    strategy.root.model_dump(by_alias=True),
                    ctx,
                )
            except KeyError as _ke:
                # Strategy referenced a feature we couldn't
                # compute for this (ticker, bar) — typically
                # because the ticker has insufficient OHLCV
                # history to yet form the rolling window
                # (newly-listed stocks, recent additions to the
                # registry, etc.). No-op for this bar; the
                # strategy will start firing once history fills.
                _key_err_counts[str(_ke)] = (
                    _key_err_counts.get(str(_ke), 0) + 1
                )
                continue

            current_equity = (
                request.initial_capital_inr
                + pt.total_realised_pnl_inr()
                - total_fees
            )
            # REGIME-4 — assemble sizing context for new modes.
            # Legacy {shares}/{notional_inr} bypass this block.
            factor_row = factors_by_key.get((ticker, bar_date), {})
            realized_vol = factor_row.get(
                "realized_vol_60d", Decimal("NaN"),
            )
            sizing_ctx = SizingContext(
                ticker=ticker,
                bar_date=bar_date,
                nav=current_equity,
                cash=current_equity,
                stock_price=current.close,
                realized_vol_annual=realized_vol,
                sector=None,
                sector_exposure=Decimal("0"),
                equity_curve=[
                    (p.bar_date, p.equity_inr)
                    for p in equity_points
                ],
            )
            intent = _action_to_intent(
                action, ticker=ticker, bar_date=bar_date,
                pt=pt,
                last_price=current.close,
                current_equity=current_equity,
                sizing_ctx=sizing_ctx,
            )
            if intent is None:
                continue

            # 3-tier RiskEngine gate (per-trade / daily / portfolio)
            # — same logic that PaperRuntime uses, so a strategy
            # behaves identically across backtest and paper.
            open_qty_map = {
                t: p.qty
                for t, p in pt.open_positions().items()
            }
            day_realised = (
                pt.total_realised_pnl_inr() - day_start_realised
            )
            account_state = AccountState(
                user_id=user_id,
                day_date=bar_date,
                initial_capital_inr=request.initial_capital_inr,
                current_equity_inr=current_equity,
                daily_realised_pnl_inr=day_realised,
                daily_unrealised_pnl_inr=Decimal("0"),
                open_positions=open_qty_map,
                open_position_count=len(open_qty_map),
                kill_switch_active=False,
            )
            signal = Signal(
                strategy_id=strategy.id,
                user_id=user_id,
                ticker=intent.ticker,
                side=intent.side,
                qty=intent.qty,
                emitted_at_ns=int(
                    datetime(
                        bar_date.year, bar_date.month, bar_date.day,
                        tzinfo=timezone.utc,
                    ).timestamp() * 1_000_000_000
                ),
            )
            decision = risk.gate(
                signal=signal,
                account=account_state,
                risk=risk_payload,
                last_price=current.close,
            )
            if decision.outcome == "reject":
                rejected_count += 1
                events.append(event_row(
                    session_id=session_id,
                    user_id=user_id,
                    strategy_id=strategy.id,
                    mode="backtest",
                    type_="signal_rejected",
                    payload={
                        "ticker": intent.ticker,
                        "side": intent.side,
                        "qty": intent.qty,
                        "reason": (
                            decision.reason.value
                            if decision.reason else "unknown"
                        ),
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
                ))
                continue
            if (
                decision.outcome == "scale"
                and decision.adjusted_qty
                and decision.adjusted_qty > 0
                and decision.adjusted_qty < intent.qty
            ):
                scaled_count += 1
                intent = intent.model_copy(
                    update={"qty": decision.adjusted_qty},
                )

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

        # End-of-day equity snapshot. last_close holds the
        # most-recent close at-or-before today for each ticker
        # we've seen, so unrealised P&L on open positions
        # tracks the actual market path day-by-day instead of
        # being suppressed until period_end.
        marks = dict(last_close)
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

    _logger.info(
        "backtest run %s: closed=%d trades, "
        "risk-rejected=%d signals, scaled=%d signals, "
        "feature-key-errors=%s",
        run_id, len(closed), rejected_count, scaled_count,
        sorted(
            _key_err_counts.items(), key=lambda x: -x[1],
        )[:5] or "none",
    )

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


_NEW_SIZING_KEYS = ("vol_target_pct", "kelly_fraction")


def _action_to_intent(
    action: dict,
    *,
    ticker: str,
    bar_date,  # noqa: ANN001
    pt: PositionTracker,
    last_price: Decimal | None = None,
    current_equity: Decimal | None = None,
    sizing_ctx: SizingContext | None = None,
) -> OrderIntent | None:
    """Translate an evaluator action dict to an OrderIntent (or None).

    ``last_price`` + ``current_equity`` are required only for
    ``set_target_weight`` resolution.  ``sizing_ctx`` is required
    only when the buy action uses the REGIME-4 sizing modes
    (``vol_target_pct`` / ``kelly_fraction``); legacy modes
    (``shares`` / ``notional_inr``) bypass the composer entirely
    for byte-for-byte backward compatibility.
    """
    t = action.get("type")
    if t == "buy":
        qty_spec = action["qty"]
        if (
            sizing_ctx is not None
            and any(k in qty_spec for k in _NEW_SIZING_KEYS)
        ):
            qty = compose_qty(qty_spec, sizing_ctx)
        elif "notional_inr" in qty_spec:
            # Legacy notional sizing: qty = floor(notional / price).
            # Requires last_price; falls back to no-op if missing.
            if last_price is None or last_price <= 0:
                qty = 0
            else:
                qty = int(
                    Decimal(str(qty_spec["notional_inr"]))
                    // last_price
                )
        else:
            qty = qty_spec.get("shares") or 0
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
    if t == "set_target_weight":
        # Resolve the weight against current equity at this bar.
        # target_qty = floor(weight * equity / last_price)
        # Diff vs existing position emits a BUY (under-weight)
        # or SELL (over-weight). Equal weight is a no-op.
        if last_price is None or last_price <= 0:
            return None
        if current_equity is None or current_equity <= 0:
            return None
        try:
            weight = Decimal(str(action.get("weight", 0)))
        except Exception:  # noqa: BLE001
            return None
        if weight <= 0:
            return None
        target_notional = current_equity * weight
        target_qty = int(target_notional // last_price)
        existing = pt.open_positions().get(ticker)
        current_qty = existing.qty if existing else 0
        diff = target_qty - current_qty
        if diff > 0:
            return OrderIntent(
                ticker=ticker, side="BUY", qty=int(diff),
                intent_emitted_at=bar_date,
            )
        if diff < 0:
            return OrderIntent(
                ticker=ticker, side="SELL", qty=int(-diff),
                intent_emitted_at=bar_date,
            )
        return None
    # `hold` is an explicit no-op.
    return None

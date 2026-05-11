"""Live pre-trade check — V2-5.

``pre_trade_check`` applies the 9-cap risk gate in order:

  1. Kill switch               (KILL_SWITCH)
  2. Allowed tickers           (LIVE_TICKER_NOT_ALLOWED)   ← cheapest
  3. max_orders_per_day        (LIVE_ORDERS_PER_DAY_CAP)
  4. max_inr                   (LIVE_INR_CAP)
  5. Per-trade max_qty         (MAX_QTY)
  6. Daily max_loss_pct        (DAILY_LOSS_CAP)
  7. Daily max_open_positions  (MAX_OPEN_POSITIONS)
  8. Portfolio max_concentration_pct  (POSITION_CAP)
  9. Portfolio max_exposure_pct       (EXPOSURE_CAP — may scale)

v2-new layers (2-4) run BEFORE the v1 layers (5-9) so we short-
circuit cheaply before any portfolio arithmetic.

Per spec §5, the ordering matters for short-circuit efficiency.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from backend.algo.paper.types import (
    AccountState,
    RejectReason,
    RiskDecision,
    Signal,
)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------
# Extended reject reasons for v2 live caps (re-exported for tests)
# ---------------------------------------------------------------

class LiveRejectReason:
    """String constants that mirror the v2 RejectReason enum values."""
    LIVE_TICKER_NOT_ALLOWED = (
        RejectReason.LIVE_TICKER_NOT_ALLOWED.value
    )
    LIVE_INR_CAP = RejectReason.LIVE_INR_CAP.value
    LIVE_ORDERS_PER_DAY_CAP = (
        RejectReason.LIVE_ORDERS_PER_DAY_CAP.value
    )
    LIVE_NOT_ENABLED = RejectReason.LIVE_NOT_ENABLED.value


def _reject_live(
    reason: RejectReason,
    threshold: Decimal | None = None,
    observed: Decimal | None = None,
) -> RiskDecision:
    """Return a RiskDecision with a v2 live-cap reject reason."""
    return RiskDecision(
        outcome="reject",
        reason=reason,
        threshold=threshold,
        observed_value=observed,
    )


def _reject(
    reason: RejectReason,
    threshold: Decimal | None = None,
    observed: Decimal | None = None,
) -> RiskDecision:
    return RiskDecision(
        outcome="reject",
        reason=reason,
        threshold=threshold,
        observed_value=observed,
    )


def _accept() -> RiskDecision:
    return RiskDecision(outcome="accept")


def _scale(qty: int) -> RiskDecision:
    return RiskDecision(outcome="scale", adjusted_qty=qty)


# ---------------------------------------------------------------
# Public API
# ---------------------------------------------------------------

def pre_trade_check(
    *,
    signal: Signal,
    caps: dict[str, Any],
    day_state: dict[str, Any],
    account: AccountState,
    strategy_risk: dict[str, Any],
    last_price: Decimal,
) -> RiskDecision:
    """Apply all 9 caps and return the risk verdict.

    Args:
        signal: The strategy-emitted signal.
        caps: Row from ``algo.live_caps`` for this (user, strategy).
        day_state: Dict with keys ``cumulative_inr_today`` and
            ``orders_count_today`` (current day running totals).
        account: Current account state snapshot.
        strategy_risk: Strategy-level ``risk`` dict (v1 caps).
        last_price: Last traded price for the signal's ticker.

    Returns:
        ``RiskDecision`` with outcome in ``accept`` / ``scale`` /
        ``reject``.  ``reject`` always has a ``reason`` set.
    """
    # ---- Cap 1: Kill switch ------------------------------------
    # Fastest gate: a single bool already in AccountState.
    if account.kill_switch_active:
        _logger.debug(
            "pre_trade_check: REJECT kill_switch signal=%s",
            signal.signal_id,
        )
        return _reject(RejectReason.KILL_SWITCH)

    # ---- Cap 2: Allowed tickers --------------------------------
    # Allow-list is NOT a block-list. Empty list = reject all
    # (no tickers configured). The UX forces the user to set
    # at least one ticker before enabling live trading.
    #
    # Suffix-tolerant compare: the safety belts UI lets users
    # type bare symbols (`JUBLFOOD`) but signals carry the
    # market-suffixed form (`JUBLFOOD.NS`). Strip the .NS / .BO
    # suffix from BOTH sides so both spellings pass.
    def _bare(t: str) -> str:
        for suf in (".NS", ".BO", ".NSI"):
            if t.endswith(suf):
                return t[: -len(suf)]
        return t

    allowed = caps.get("allowed_tickers", [])
    allowed_bare = {_bare(t).upper() for t in (allowed or [])}
    signal_bare = _bare(signal.ticker).upper()
    if not allowed_bare or signal_bare not in allowed_bare:
        _logger.debug(
            "pre_trade_check: REJECT ticker=%s not in allow-list=%s",
            signal.ticker, allowed,
        )
        return _reject_live(
            RejectReason.LIVE_TICKER_NOT_ALLOWED,
        )

    # ---- Cap 3: max_orders_per_day -----------------------------
    max_orders = int(caps.get("max_orders_per_day", 0))
    if max_orders > 0:
        orders_today = int(day_state.get("orders_count_today", 0))
        if orders_today >= max_orders:
            return _reject_live(
                RejectReason.LIVE_ORDERS_PER_DAY_CAP,
                threshold=Decimal(max_orders),
                observed=Decimal(orders_today),
            )

    # ---- Cap 4: max_inr per day --------------------------------
    max_inr = Decimal(str(caps.get("max_inr", 0)))
    if max_inr > 0:
        cum_inr = Decimal(
            str(day_state.get("cumulative_inr_today", 0))
        )
        order_notional = Decimal(signal.qty) * last_price
        headroom = max_inr - cum_inr
        if order_notional > headroom:
            return _reject_live(
                RejectReason.LIVE_INR_CAP,
                threshold=headroom,
                observed=order_notional,
            )

    # ---- Caps 5-9: v1 risk engine logic (inline) ---------------
    # We replicate the logic here rather than calling RiskEngine
    # so that live mode has its own traceable code path with
    # dedicated reject reasons and a ``binding=True`` contract.

    per_trade = strategy_risk.get("per_trade", {})
    max_qty = int(per_trade.get("max_qty", 0))
    if max_qty > 0 and signal.qty > max_qty:
        return _reject(
            RejectReason.MAX_QTY,
            threshold=Decimal(max_qty),
            observed=Decimal(signal.qty),
        )

    daily = strategy_risk.get("daily", {})
    max_loss_pct = Decimal(str(daily.get("max_loss_pct", 0)))
    if max_loss_pct > 0:
        cap_loss_inr = (
            account.initial_capital_inr
            * max_loss_pct / Decimal("100")
        )
        current_loss = -(
            account.daily_realised_pnl_inr
            + account.daily_unrealised_pnl_inr
        )
        if current_loss >= cap_loss_inr:
            return _reject(
                RejectReason.DAILY_LOSS_CAP,
                threshold=cap_loss_inr,
                observed=current_loss,
            )

    max_open = int(daily.get("max_open_positions", 0))
    if (
        max_open > 0
        and signal.side == "BUY"
        and signal.ticker not in account.open_positions
        and account.open_position_count >= max_open
    ):
        return _reject(
            RejectReason.MAX_OPEN_POSITIONS,
            threshold=Decimal(max_open),
            observed=Decimal(account.open_position_count),
        )

    if signal.side == "SELL":
        return _accept()

    portfolio = strategy_risk.get("portfolio", {})
    max_concentration_pct = Decimal(
        str(portfolio.get("max_concentration_pct", 0))
    )
    if (
        max_concentration_pct > 0
        and account.current_equity_inr > 0
    ):
        existing_qty = account.open_positions.get(
            signal.ticker, 0,
        )
        new_notional = (
            Decimal(existing_qty + signal.qty) * last_price
        )
        new_conc_pct = (
            new_notional / account.current_equity_inr
            * Decimal("100")
        )
        if new_conc_pct > max_concentration_pct:
            return _reject(
                RejectReason.POSITION_CAP,
                threshold=max_concentration_pct,
                observed=new_conc_pct,
            )

    max_exposure_pct = Decimal(
        str(portfolio.get("max_exposure_pct", 0))
    )
    if (
        max_exposure_pct > 0
        and account.current_equity_inr > 0
    ):
        cap_inr = (
            account.current_equity_inr
            * max_exposure_pct / Decimal("100")
        )
        existing_exposure = sum(
            (Decimal(q) * last_price
             for q in account.open_positions.values()),
            start=Decimal("0"),
        )
        requested_notional = Decimal(signal.qty) * last_price
        total = existing_exposure + requested_notional
        if total > cap_inr:
            headroom = cap_inr - existing_exposure
            if headroom <= 0:
                return _reject(
                    RejectReason.EXPOSURE_CAP,
                    threshold=cap_inr,
                    observed=existing_exposure,
                )
            scaled_qty = int(headroom // last_price)
            if scaled_qty <= 0:
                return _reject(
                    RejectReason.EXPOSURE_CAP,
                    threshold=cap_inr,
                    observed=total,
                )
            return _scale(scaled_qty)

    return _accept()

"""3-tier risk engine — pure logic, no state.

Same code runs in backtest + paper. Signal in, RiskDecision out.
The runtime persists rejections as ``signal_rejected`` events;
this module is responsible only for the verdict.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from backend.algo.paper.types import (
    AccountState, RejectReason, RiskDecision, Signal,
)

_logger = logging.getLogger(__name__)


def _accept() -> RiskDecision:
    return RiskDecision(outcome="accept")


def _reject(
    reason: RejectReason,
    threshold: Decimal | None = None,
    observed: Decimal | None = None,
) -> RiskDecision:
    return RiskDecision(
        outcome="reject", reason=reason,
        threshold=threshold, observed_value=observed,
    )


def _scale(qty: int) -> RiskDecision:
    return RiskDecision(outcome="scale", adjusted_qty=qty)


class RiskEngine:
    def gate(
        self,
        *,
        signal: Signal,
        account: AccountState,
        risk: dict[str, Any],
        last_price: Decimal,
    ) -> RiskDecision:
        """Apply per-trade → daily → portfolio caps in that order.

        Per-trade and daily are hard rejects; portfolio
        ``max_exposure_pct`` may scale the order rather than reject
        outright (signal still meaningful at smaller size).
        """
        if account.kill_switch_active:
            return _reject(RejectReason.KILL_SWITCH)

        # Per-trade.
        per_trade = risk.get("per_trade", {})
        max_qty = int(per_trade.get("max_qty", 0))
        if max_qty > 0 and signal.qty > max_qty:
            return _reject(
                RejectReason.MAX_QTY,
                threshold=Decimal(max_qty),
                observed=Decimal(signal.qty),
            )

        # Daily.
        daily = risk.get("daily", {})
        max_loss_pct = Decimal(str(daily.get("max_loss_pct", 0)))
        if max_loss_pct > 0:
            cap_loss_inr = (
                account.initial_capital_inr * max_loss_pct
                / Decimal("100")
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
            # SELL reduces exposure; portfolio caps don't apply.
            return _accept()

        # Portfolio — concentration (per-ticker).
        portfolio = risk.get("portfolio", {})
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
            new_concentration_pct = (
                new_notional / account.current_equity_inr
                * Decimal("100")
            )
            if new_concentration_pct > max_concentration_pct:
                return _reject(
                    RejectReason.POSITION_CAP,
                    threshold=max_concentration_pct,
                    observed=new_concentration_pct,
                )

        # Portfolio — total exposure (may scale).
        max_exposure_pct = Decimal(
            str(portfolio.get("max_exposure_pct", 0))
        )
        if (
            max_exposure_pct > 0
            and account.current_equity_inr > 0
        ):
            cap_inr = (
                account.current_equity_inr * max_exposure_pct
                / Decimal("100")
            )
            existing_exposure = sum(
                (Decimal(q) * last_price for q in
                 account.open_positions.values()),
                start=Decimal("0"),
            )
            requested_notional = (
                Decimal(signal.qty) * last_price
            )
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

"""Stop-loss monitor — per-bar exit-trigger detector.

Pure function. Shared by backtest + paper + live runtimes. Each
runtime translates triggers into runtime-appropriate exit orders
(backtest + paper: next-bar-open via SimBroker; live: immediate
MARKET via Kite).

Long-only v1. Short-side positions are out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class StopLossTrigger:
    """One stopped-out position. Runtimes translate to SELL orders."""

    ticker: str
    avg_price: Decimal
    current_close: Decimal
    loss_pct: Decimal
    stop_loss_pct: Decimal


def check_stop_loss_triggers(
    *,
    open_positions: dict[str, dict],
    current_closes: dict[str, Decimal],
    stop_loss_pct: float,
) -> list[StopLossTrigger]:
    """Return triggers for positions whose loss exceeds the threshold.

    For each open long position with ``avg_price > 0`` and a
    ``current_close`` available::

        loss_pct = (current_close - avg_price) / avg_price * 100
        trigger if loss_pct <= -stop_loss_pct

    Returns empty list if ``stop_loss_pct == 0`` (feature disabled)
    or no positions breach. Skips tickers with missing closes
    (data gap; don't fabricate).

    Args:
        open_positions: ``ticker → {"qty": int, "avg_price": Decimal}``
        current_closes: ``ticker → close at this bar``
        stop_loss_pct: From ``strategy.risk.per_trade.stop_loss_pct``

    Returns:
        Empty list when feature disabled or no breaches.
    """
    if stop_loss_pct <= 0:
        return []

    threshold = Decimal(str(stop_loss_pct))
    minus_threshold = -threshold

    triggers: list[StopLossTrigger] = []
    for ticker, pos in open_positions.items():
        avg_price = pos.get("avg_price")
        if avg_price is None or avg_price <= 0:
            continue
        current_close = current_closes.get(ticker)
        if current_close is None:
            continue
        loss_pct = (
            (Decimal(str(current_close)) - Decimal(str(avg_price)))
            / Decimal(str(avg_price))
            * Decimal("100")
        )
        if loss_pct <= minus_threshold:
            triggers.append(StopLossTrigger(
                ticker=ticker,
                avg_price=Decimal(str(avg_price)),
                current_close=Decimal(str(current_close)),
                loss_pct=loss_pct,
                stop_loss_pct=threshold,
            ))
    return triggers

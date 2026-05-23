"""Time-based stop monitor — force-exit positions held N+ days.

ASETPLTFRM-430 Experiment 3. Pure function. Complements
``stop_loss_monitor`` — same shape, different trigger. Runner
calls it before AST eval per bar; force-exits feed through
SimBroker at next-bar-open with ``exit_reason="time_stop"``.

For mean-reversion strategies (RSI(2) Connors et al.) the
reversion either completes within a fixed window (2-5 days) or
fails. A time-based exit fires AFTER the reversion window
without truncating the price action inside it — which is the
gap a price stop creates.

Long-only v1. Calendar-day arithmetic (not trading days) keeps
the math simple and matches the canonical Connors description.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TimeStopTrigger:
    """One time-stopped position. Runtimes translate to SELL."""

    ticker: str
    opened_at: date
    current_date: date
    holding_days: int
    max_holding_days: int


def check_time_stop_triggers(
    *,
    open_positions: dict[str, dict],
    current_date: date,
    max_holding_days: int | None,
) -> list[TimeStopTrigger]:
    """Return triggers for positions held ``>= max_holding_days``.

    Holding-days arithmetic: ``(current_date - opened_at).days``.
    Calendar days, not trading days — matches Connors's published
    framing and removes the need for a calendar dependency in the
    monitor.

    Args:
        open_positions: ``ticker → {"qty", "opened_at"}`` dict
            keyed by ticker. ``opened_at`` is a ``date``.
        current_date: The bar's close date.
        max_holding_days: From
            ``strategy.risk.per_trade.max_holding_days``.
            ``None`` or ``<= 0`` disables the feature.

    Returns:
        Empty list when disabled or no positions exceed the
        threshold.
    """
    if max_holding_days is None or max_holding_days <= 0:
        return []

    triggers: list[TimeStopTrigger] = []
    for ticker, pos in open_positions.items():
        opened_at = pos.get("opened_at")
        if opened_at is None:
            continue
        holding_days = (current_date - opened_at).days
        if holding_days >= max_holding_days:
            triggers.append(TimeStopTrigger(
                ticker=ticker,
                opened_at=opened_at,
                current_date=current_date,
                holding_days=holding_days,
                max_holding_days=max_holding_days,
            ))
    return triggers

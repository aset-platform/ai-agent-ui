"""Repeat-offender cooldown gate (ASETPLTFRM-434 Exp.2).

Pure function. Returns True when a ticker should be skipped for
new entries because it had a recent failed exit (``time_stop`` or
``stop_loss``) within the configured cooldown window.

Motivation: in v2 backtest triage, a small number of tickers
(APOLLO.NS, IDEA.NS, ADANIPOWER.NS) absorbed disproportionate
losses by repeatedly re-triggering oversold entries that all
failed to revert. Each individual entry passed every other risk
gate at order time — but the historical pattern (4 time-stops in
a row, 0 signal exits) was invisible to the entry logic.

The cooldown gate makes that history visible. Pure function takes
the same closed-positions list the rest of the runner already
maintains; no new persistence, no new caches.

Long-only v1. The gate fires regardless of which side the failed
exit was on (only long positions exist in v1 anyway).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Protocol


# Exit reasons that put a ticker in cooldown. ``signal`` (clean
# AST-emitted exit) is the only winner shape; everything else
# represents a thesis failure.
# ASETPLTFRM-435 v4 — ``regime_exit`` joins the failed set: if
# we force-closed a position because the market regime turned,
# re-entering it next bar is the same broken thesis.
_FAILED_EXIT_REASONS = frozenset({
    "time_stop", "stop_loss", "regime_exit",
})


class _ClosedPositionLike(Protocol):
    """Structural protocol — anything with the three fields we
    read. Keeps the function testable with lightweight fixtures
    while still working with the real Position dataclass."""

    ticker: str
    exit_reason: str
    closed_at: date | None


def in_cooldown(
    *,
    ticker: str,
    bar_date: date,
    closed_positions: Iterable[_ClosedPositionLike],
    cooldown_days: int | None,
) -> bool:
    """True iff ``ticker`` has a failed exit within the window.

    Args:
        ticker: The ticker the runner is about to evaluate.
        bar_date: Current bar's close date.
        closed_positions: Every closed position in the run so far
            (``pt.closed_positions()``). Filtered + scanned in-line;
            for typical runs (< 5000 closes) this is microseconds.
        cooldown_days: From
            ``strategy.risk.per_trade.cooldown_after_failed_exit_days``.
            ``None`` or ``<= 0`` disables the gate.

    Returns:
        ``True`` to skip new entries on this ticker, ``False``
        to allow normal AST evaluation.
    """
    if cooldown_days is None or cooldown_days <= 0:
        return False

    cutoff = bar_date - timedelta(days=cooldown_days)
    for pos in closed_positions:
        if pos.ticker != ticker:
            continue
        if pos.exit_reason not in _FAILED_EXIT_REASONS:
            continue
        if pos.closed_at is None:
            continue
        if pos.closed_at >= cutoff:
            return True
    return False

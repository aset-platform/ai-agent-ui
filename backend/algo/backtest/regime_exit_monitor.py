"""Mid-trade regime-exit monitor (ASETPLTFRM-435).

Pure function. Per-bar: if the strategy's ``mid_trade_regime_check``
condition evaluates False against the current bar's market features,
ALL open positions are force-exited at next-bar-open (backtest /
paper) or immediate LIMIT SELL (live) with
``exit_reason="regime_exit"``.

Motivation: v3's regime gate is entry-only. Once positions are open,
the regime is never re-evaluated. During cluster-decay days (broad
market turning hostile after entries land), every open RSI(2)
position bleeds together until the 5-day time-stop fires. Mid-trade
regime exit cuts the systemic loss BEFORE the cluster-decay
completes — a proactive complement to the reactive cooldown gate.

Long-only v1. The trigger evaluates one shared condition tree
against market-level features; per-ticker conditions inside the
tree would still work but should be avoided (mid-trade exit is a
portfolio-wide kill, not a per-name decision).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from backend.algo.backtest.evaluator import EvalContext, Evaluator


_logger = logging.getLogger(__name__)

# Singleton evaluator. Stateless, safe to share across calls.
_EVALUATOR = Evaluator()


@dataclass(frozen=True)
class RegimeExitTrigger:
    """One position to force-exit because the regime turned hostile."""

    ticker: str
    bar_date: date


def check_regime_exit_triggers(
    *,
    open_positions: dict[str, dict],
    bar_date: date,
    market_features: dict[str, Decimal],
    regime_check: dict | None,
) -> list[RegimeExitTrigger]:
    """Return one trigger per open position if regime turned hostile.

    Args:
        open_positions: ``{ticker → {"qty": ...}}`` — runner's
            in-memory open positions map. Empty dict short-circuits.
        bar_date: Current bar's close date — stamped onto each
            trigger for the runner's logging.
        market_features: Per-bar market features (regime_label,
            stress_prob, nifty_above_sma200, nifty_30d_return_pct,
            vix_close, etc.). Same source as the entry-time gate
            consumes via EvalContext.
        regime_check: AST condition tree (the dump of
            ``Strategy.mid_trade_regime_check.model_dump(by_alias=True)``).
            ``None`` disables the feature.

    Returns:
        Empty list when disabled, no positions, or regime still
        safe. Otherwise one trigger per open ticker — all-or-nothing,
        because mid-trade exit is a portfolio-wide kill.
    """
    if regime_check is None:
        return []
    if not open_positions:
        return []

    # Synthetic context: ticker is a sentinel; the regime check
    # should only reference market-level features. ``open_qty=0``
    # because we're checking portfolio-level conditions, not
    # individual positions.
    ctx = EvalContext(
        ticker="__market__",
        bar_date=bar_date,
        features=market_features,
        open_qty=0,
    )
    try:
        is_safe = bool(_EVALUATOR.eval_node(regime_check, ctx))
    except KeyError as ke:
        # Missing market feature — fail open (don't force-exit
        # on a transient regime-cache gap). Logged so operators
        # can spot recurring gaps; runner's feature-key-errors
        # tally surfaces the same issue at session end.
        _logger.warning(
            "regime_exit_monitor: missing feature in market "
            "context (%s) — failing open (no exit)", ke,
        )
        return []
    except Exception:  # pragma: no cover — defensive
        _logger.exception(
            "regime_exit_monitor: regime_check eval crashed — "
            "failing open (no exit)",
        )
        return []

    if is_safe:
        return []

    return [
        RegimeExitTrigger(ticker=t, bar_date=bar_date)
        for t in sorted(open_positions)
    ]

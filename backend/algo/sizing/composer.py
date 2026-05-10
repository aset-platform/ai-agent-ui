"""Sizing composer — orchestrates vol-target → caps → DD throttle.

Pure function. Pluggable by all three runtimes (backtest / paper /
live). Caller assembles a SizingContext with NAV, cash, factor cache
lookup, sector lookup, and the strategy's equity curve.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from backend.algo.sizing.caps import PositionCaps
from backend.algo.sizing.drawdown_throttle import (
    compute_dd_pct,
    dd_multiplier,
)
from backend.algo.sizing.vol_target import vol_target_qty

_logger = logging.getLogger(__name__)


@dataclass
class SizingContext:
    ticker: str
    bar_date: date
    nav: Decimal
    cash: Decimal
    stock_price: Decimal
    realized_vol_annual: Decimal
    sector: str | None
    sector_exposure: Decimal
    equity_curve: list[tuple[date, Decimal]]
    n_positions_target: int = 10
    expected_edge: Decimal | None = None
    caps: PositionCaps = field(default_factory=PositionCaps)


def _resolve_base_qty(
    qty_spec: dict, ctx: SizingContext,
) -> int:
    """Resolve raw qty per AST mode. Returns 0 on unknown mode."""
    if "shares" in qty_spec:
        return int(qty_spec["shares"])
    if "notional_inr" in qty_spec:
        notional = Decimal(str(qty_spec["notional_inr"]))
        if ctx.stock_price <= 0:
            return 0
        return int(notional / ctx.stock_price)
    if "vol_target_pct" in qty_spec:
        return vol_target_qty(
            target_portfolio_vol_pct=Decimal(
                str(qty_spec["vol_target_pct"])
            ),
            nav=ctx.nav,
            stock_price=ctx.stock_price,
            stock_realized_vol_annual=ctx.realized_vol_annual,
            n_positions_target=ctx.n_positions_target,
        )
    if "kelly_fraction" in qty_spec:
        if ctx.expected_edge is None:
            _logger.warning(
                "kelly_fraction sizing requested for %s but no "
                "expected_edge in strategy metadata — skipping",
                ctx.ticker,
            )
            return 0
        edge = Decimal(str(ctx.expected_edge))
        vol = ctx.realized_vol_annual
        if vol.is_nan() or vol <= 0:
            return 0
        f_star = edge / (vol * vol)
        capital = (
            f_star
            * Decimal(str(qty_spec["kelly_fraction"]))
            * ctx.nav
        )
        if ctx.stock_price <= 0 or capital <= 0:
            return 0
        return int(capital / ctx.stock_price)
    if "all" in qty_spec:
        # All-cash entry — bounded by per-position cap downstream.
        if ctx.stock_price <= 0:
            return 0
        return int(ctx.cash / ctx.stock_price)
    _logger.warning(
        "Unknown sizing mode %s for %s — skipping",
        list(qty_spec.keys()), ctx.ticker,
    )
    return 0


def compose_qty(qty_spec: dict, ctx: SizingContext) -> int:
    """3-stage pipeline: resolve → cap → DD throttle. Returns 0 to
    signal "skip" on any invalid input."""
    base = _resolve_base_qty(qty_spec, ctx)
    if base <= 0:
        return 0
    intended_value = Decimal(base) * ctx.stock_price
    capped = ctx.caps.cap(
        intended_qty=base,
        intended_value=intended_value,
        nav=ctx.nav,
        stock_price=ctx.stock_price,
        sector=ctx.sector,
        current_sector_exposure=ctx.sector_exposure,
        current_cash=ctx.cash,
    )
    if capped <= 0:
        return 0
    mult = dd_multiplier(compute_dd_pct(ctx.equity_curve))
    if mult == Decimal("0"):
        return 0
    return int(Decimal(capped) * mult)

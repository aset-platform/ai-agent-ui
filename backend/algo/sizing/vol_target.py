"""Volatility-targeted position sizer.

Per spec §3.4 + research §5:
    per_pos_vol_budget = target_portfolio_vol_pct / sqrt(n_positions_target)
    notional           = (per_pos_vol_budget / 100) * nav
                         / stock_realized_vol_annual
    qty                = floor(notional / stock_price)
"""
from __future__ import annotations

from decimal import Decimal


def _is_invalid(d: Decimal) -> bool:
    return d.is_nan() or d <= 0


def vol_target_qty(
    target_portfolio_vol_pct: Decimal,
    nav: Decimal,
    stock_price: Decimal,
    stock_realized_vol_annual: Decimal,
    n_positions_target: int,
) -> int:
    """Return integer share qty.

    Inputs:
      * ``target_portfolio_vol_pct`` — e.g. ``Decimal("1.5")`` for 1.5%
      * ``nav`` — total portfolio NAV in INR
      * ``stock_price`` — current price
      * ``stock_realized_vol_annual`` — annualised realized vol e.g.
        ``Decimal("0.30")`` for 30%
      * ``n_positions_target`` — diversification target (≥ 1)

    Returns 0 on any invalid input (NaN, zero, negative) — sizer
    treats this as "skip the trade".
    """
    if (
        n_positions_target <= 0
        or _is_invalid(target_portfolio_vol_pct)
        or _is_invalid(nav)
        or _is_invalid(stock_price)
        or _is_invalid(stock_realized_vol_annual)
    ):
        return 0
    sqrt_n = Decimal(n_positions_target).sqrt()
    per_pos_vol_budget = target_portfolio_vol_pct / sqrt_n
    notional = (
        (per_pos_vol_budget / Decimal("100") * nav)
        / stock_realized_vol_annual
    )
    return int(notional / stock_price)

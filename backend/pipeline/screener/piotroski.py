"""Piotroski F-Score computation engine.

Pure functions -- no I/O, no database access. Takes two
dicts of annual financial data (current year, previous
year) and returns a scored result.

Reference: Piotroski, J.D. (2000). "Value Investing:
The Use of Historical Financial Statement Information
to Separate Winners from Losers."
"""

from __future__ import annotations

from dataclasses import dataclass


def _safe(val) -> float:
    """Coerce to float; None/NaN -> 0.0."""
    if val is None:
        return 0.0
    try:
        f = float(val)
        return 0.0 if f != f else f  # NaN check
    except (ValueError, TypeError):
        return 0.0


def _ratio(num: float, den: float) -> float | None:
    """Safe division; returns None if denominator is 0."""
    if den == 0.0:
        return None
    return num / den


@dataclass
class PiotroskiResult:
    """Result of Piotroski F-Score computation."""

    total_score: int
    roa_positive: bool
    operating_cf_positive: bool
    roa_increasing: bool
    cf_gt_net_income: bool
    leverage_decreasing: bool
    current_ratio_increasing: bool
    no_dilution: bool
    gross_margin_increasing: bool
    asset_turnover_increasing: bool

    @property
    def label(self) -> str:
        """Human-readable quality label."""
        if self.total_score >= 8:
            return "Strong"
        if self.total_score >= 5:
            return "Moderate"
        return "Weak"


def compute_piotroski(
    current: dict,
    previous: dict,
) -> PiotroskiResult:
    """Compute Piotroski F-Score from two years of data.

    Args:
        current: Current fiscal year financials with keys:
            net_income, total_assets, operating_cashflow,
            total_debt, current_assets,
            current_liabilities, shares_outstanding,
            gross_profit, revenue.
        previous: Prior fiscal year (same keys).

    Returns:
        PiotroskiResult with all 9 criteria and total.
    """
    # Current year values
    ni = _safe(current.get("net_income"))
    ta = _safe(current.get("total_assets"))
    ocf = _safe(current.get("operating_cashflow"))
    td = _safe(current.get("total_debt"))
    ca = _safe(current.get("current_assets"))
    cl = _safe(current.get("current_liabilities"))
    so = _safe(current.get("shares_outstanding"))
    gp = _safe(current.get("gross_profit"))
    rev = _safe(current.get("revenue"))

    # Previous year values
    ni_p = _safe(previous.get("net_income"))
    ta_p = _safe(previous.get("total_assets"))
    td_p = _safe(previous.get("total_debt"))
    ca_p = _safe(previous.get("current_assets"))
    cl_p = _safe(previous.get("current_liabilities"))
    so_p = _safe(previous.get("shares_outstanding"))
    gp_p = _safe(previous.get("gross_profit"))
    rev_p = _safe(previous.get("revenue"))

    # Profitability (4)
    roa_curr = _ratio(ni, ta)
    roa_prev = _ratio(ni_p, ta_p)
    roa_positive = (roa_curr or 0) > 0
    operating_cf_positive = ocf > 0
    roa_increasing = (
        roa_curr is not None and roa_prev is not None and roa_curr > roa_prev
    )
    cf_gt_net_income = ocf > ni

    # Leverage / Liquidity (3)
    lev_curr = _ratio(td, ta)
    lev_prev = _ratio(td_p, ta_p)
    leverage_decreasing = (
        lev_curr is not None and lev_prev is not None and lev_curr < lev_prev
    )

    cr_curr = _ratio(ca, cl)
    cr_prev = _ratio(ca_p, cl_p)
    current_ratio_increasing = (
        cr_curr is not None and cr_prev is not None and cr_curr > cr_prev
    )

    no_dilution = so <= so_p and (so > 0 or so_p > 0)

    # Operating Efficiency (2)
    gm_curr = _ratio(gp, rev)
    gm_prev = _ratio(gp_p, rev_p)
    gross_margin_increasing = (
        gm_curr is not None and gm_prev is not None and gm_curr > gm_prev
    )

    at_curr = _ratio(rev, ta)
    at_prev = _ratio(rev_p, ta_p)
    asset_turnover_increasing = (
        at_curr is not None and at_prev is not None and at_curr > at_prev
    )

    criteria = [
        roa_positive,
        operating_cf_positive,
        roa_increasing,
        cf_gt_net_income,
        leverage_decreasing,
        current_ratio_increasing,
        no_dilution,
        gross_margin_increasing,
        asset_turnover_increasing,
    ]

    return PiotroskiResult(
        total_score=sum(criteria),
        roa_positive=roa_positive,
        operating_cf_positive=operating_cf_positive,
        roa_increasing=roa_increasing,
        cf_gt_net_income=cf_gt_net_income,
        leverage_decreasing=leverage_decreasing,
        current_ratio_increasing=current_ratio_increasing,
        no_dilution=no_dilution,
        gross_margin_increasing=gross_margin_increasing,
        asset_turnover_increasing=asset_turnover_increasing,
    )

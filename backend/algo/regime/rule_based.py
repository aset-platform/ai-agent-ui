"""Rule-based regime classifier.

Pure function — no I/O, no fallbacks. Caller is responsible for
substituting SIDEWAYS when inputs are missing/stale (see
``classifier_job._safe_classify``).

Thresholds calibrated from research synthesis §1 + §2.1 (India
VIX bands, NSE breadth empirics). Tracked as module constants
for testability; tunability via PG row is deferred to v3.1.
"""
from __future__ import annotations

import math
from decimal import Decimal

VIX_CALM_MAX: Decimal = Decimal("16")
VIX_NORMAL_MAX: Decimal = Decimal("25")
BULLISH_30D_MIN: Decimal = Decimal("0.02")
BULLISH_60D_MIN: Decimal = Decimal("0.05")
BEARISH_30D_MAX: Decimal = Decimal("-0.02")
BEARISH_60D_MAX: Decimal = Decimal("-0.05")
HEALTHY_BREADTH_MIN: Decimal = Decimal("0.55")


def _is_nan(d: Decimal) -> bool:
    """Decimal NaN check — ``Decimal.is_nan`` is the safe path."""
    if hasattr(d, "is_nan"):
        return bool(d.is_nan())
    return math.isnan(float(d))


def classify_regime(
    nifty_close: Decimal,
    nifty_sma200: Decimal,
    vix_close: Decimal,
    nifty_ret_30d: Decimal,
    nifty_ret_60d: Decimal,
    pct_above_50sma: Decimal,
) -> str:
    """Return ``"BULL"`` | ``"SIDEWAYS"`` | ``"BEAR"`` for the
    given trading day's close-of-day inputs.

    Raises ValueError if any input is NaN — the caller decides
    whether to fall back to SIDEWAYS.
    """
    inputs = (
        nifty_close, nifty_sma200, vix_close,
        nifty_ret_30d, nifty_ret_60d, pct_above_50sma,
    )
    for v in inputs:
        if _is_nan(v):
            raise ValueError("NaN in classify_regime input")

    above_trend = nifty_close > nifty_sma200
    vix_calm = vix_close < VIX_CALM_MAX
    vix_normal = VIX_CALM_MAX <= vix_close <= VIX_NORMAL_MAX
    vix_stress = vix_close > VIX_NORMAL_MAX
    bullish_mom = (
        nifty_ret_30d > BULLISH_30D_MIN
        and nifty_ret_60d > BULLISH_60D_MIN
    )
    bearish_mom = (
        nifty_ret_30d < BEARISH_30D_MAX
        and nifty_ret_60d < BEARISH_60D_MAX
    )
    healthy_breadth = pct_above_50sma > HEALTHY_BREADTH_MIN

    if (
        above_trend
        and (vix_calm or vix_normal)
        and bullish_mom
        and healthy_breadth
    ):
        return "BULL"
    if (not above_trend) and vix_stress and bearish_mom:
        return "BEAR"
    return "SIDEWAYS"

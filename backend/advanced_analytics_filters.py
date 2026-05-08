"""Filter catalog + predicates for Advanced Analytics bundles.

Single source of truth for the technical + fundamentals filter
allowlist used by ``/v1/advanced-analytics/{report}`` and the
``/{report}/export`` endpoint. Frontend mirror lives at
``frontend/components/advanced-analytics/filterCatalogs.ts``;
sync verified by ``test_filter_catalog_sync.py``.
"""

from __future__ import annotations

import math
from typing import Callable, Literal

from fastapi import HTTPException

from backend.advanced_analytics_models import AdvancedRow

# ---- Type literals --------------------------------------------------

TechKey = Literal[
    "golden_recent",
    "golden_established",
    "price_gt_sma50",
    "price_gt_sma200",
    "rsi_oversold",
    "rsi_neutral",
    "rsi_overbought",
    "vol_surge",
    "near_52w_high",
]
FundKey = Literal[
    "fscore_ge_7",
    "fscore_le_3",
    "debt_lt_0_5",
    "roce_gt_20",
    "sales_3y_gt_15",
    "profit_3y_gt_15",
    "prom_hld_gt_50",
    "pledged_lt_5",
]


# ---- NaN-safe comparators -----------------------------------------


def _is_nan(x: float | int | None) -> bool:
    if x is None:
        return True
    if isinstance(x, float) and math.isnan(x):
        return True
    return False


def _gt(a: float | None, b: float | None) -> bool:
    if _is_nan(a) or _is_nan(b):
        return False
    return float(a) > float(b)  # type: ignore[arg-type]


def _ge(a: float | None, b: float | None) -> bool:
    if _is_nan(a) or _is_nan(b):
        return False
    return float(a) >= float(b)  # type: ignore[arg-type]


def _lt(a: float | None, b: float | None) -> bool:
    if _is_nan(a) or _is_nan(b):
        return False
    return float(a) < float(b)  # type: ignore[arg-type]


def _le(a: float | None, b: float | None) -> bool:
    if _is_nan(a) or _is_nan(b):
        return False
    return float(a) <= float(b)  # type: ignore[arg-type]


# ---- Predicate dicts -----------------------------------------------

TECH_PREDICATES: dict[str, Callable[[AdvancedRow], bool]] = {
    "golden_recent": lambda r: (
        r.golden_cross_days_ago is not None
        and 0 <= r.golden_cross_days_ago <= 10
    ),
    "golden_established": lambda r: (
        r.golden_cross_days_ago is not None and r.golden_cross_days_ago > 10
    ),
    "price_gt_sma50": lambda r: _gt(r.today_ltp, r.sma_50),
    "price_gt_sma200": lambda r: _gt(r.today_ltp, r.sma_200),
    "rsi_oversold": lambda r: _lt(r.rsi, 30.0),
    "rsi_neutral": lambda r: _ge(r.rsi, 30.0) and _le(r.rsi, 70.0),
    "rsi_overbought": lambda r: _gt(r.rsi, 70.0),
    "vol_surge": lambda r: _ge(r.today_x_vol, 2.0),
    "near_52w_high": lambda r: _ge(r.away_from_52week_high, -5.0),
}

FUND_PREDICATES: dict[str, Callable[[AdvancedRow], bool]] = {
    "fscore_ge_7": lambda r: r.pscore is not None and r.pscore >= 7,
    "fscore_le_3": lambda r: r.pscore is not None and r.pscore <= 3,
    "debt_lt_0_5": lambda r: _lt(r.debt_to_eq, 0.5),
    "roce_gt_20": lambda r: _gt(r.roce, 20.0),
    "sales_3y_gt_15": lambda r: _gt(r.sales_growth_3yrs, 15.0),
    "profit_3y_gt_15": lambda r: _gt(r.prft_growth_3yrs, 15.0),
    "prom_hld_gt_50": lambda r: _gt(r.prom_hld, 50.0),
    "pledged_lt_5": lambda r: _lt(r.pledged, 5.0),
}

TECH_KEYS: frozenset[str] = frozenset(TECH_PREDICATES)
FUND_KEYS: frozenset[str] = frozenset(FUND_PREDICATES)


# ---- Public helpers -----------------------------------------------


def parse_filter_csv(
    raw: str,
    allowed: frozenset[str],
    bundle: str,
) -> list[str]:
    """Split, dedupe, sort, validate.

    Returns a deterministic ``sorted(list(unique_keys))`` for the
    benefit of cache-key stability. Raises ``HTTPException(400)``
    on the first unknown token.
    """
    if not raw.strip():
        return []
    seen: set[str] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown {bundle} filter: {token}",
            )
        seen.add(token)
    return sorted(seen)


def passes_bundle_filters(
    row: AdvancedRow,
    tech: list[str],
    fund: list[str],
) -> bool:
    """AND across every selected predicate. NaN => row excluded."""
    for key in tech:
        if not TECH_PREDICATES[key](row):
            return False
    for key in fund:
        if not FUND_PREDICATES[key](row):
            return False
    return True

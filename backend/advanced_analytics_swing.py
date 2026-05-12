"""Swing Setups — regime definitions, thresholds, methodology.

Single source of truth for the bull/sideways/bearish regime filters
and the human-readable methodology block surfaced on the page.
Anyone tuning thresholds edits ONE place; the on-page explanation
and the filter behaviour move in lockstep.

Bullish category set was pinned in plan Task 0 via:
    SELECT category, COUNT(*) FROM stocks.recommendations
    WHERE status = 'active' GROUP BY category;

If categories shift over time, update :data:`BULLISH_CATEGORIES`
and the snapshot test in test_advanced_analytics_swing.py.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from advanced_analytics_models import (
    AdvancedRow,
    ESTABLISHED_CROSS_DAYS,
)

Regime = Literal["bull", "sideways", "bearish"]
REGIMES: tuple[Regime, ...] = ("bull", "sideways", "bearish")

# Pinned 2026-05-12 from DB inspection (Task 0). Rec engine uses a
# portfolio-action vocabulary (not stock-rating); these four
# categories map semantically to "go long this name". Other live
# categories (defensive, rebalance, risk_alert, gap_fill,
# diversification) are direction-agnostic or bearish. Severity is
# NOT used as a hard gate in Phase A.
BULLISH_CATEGORIES: frozenset[str] = frozenset({
    "offensive",
    "value",
    "growth",
    "hold_accumulate",
})

# ----- Bull regime thresholds -----
BULL_VOL_MIN = 2.0
BULL_VOL_MAX = 5.0
BULL_RSI_MAX = 70.0
BULL_PSCORE_MIN = 5
BULL_PLEDGED_MAX = 10.0
BULL_RANGE_MAX = 0.95  # today_ltp / week52_high
BULL_GOLDEN_CROSS_FRESH_DAYS = 30

# ----- Sideways regime thresholds -----
SIDEWAYS_MA_CONV_MAX = 0.05  # |sma_50 - sma_200| / sma_200
SIDEWAYS_PRICE_NEAR_SMA50 = 0.03
SIDEWAYS_RSI_MIN = 40.0
SIDEWAYS_RSI_MAX = 60.0
SIDEWAYS_VOL_MIN = 0.7
SIDEWAYS_VOL_MAX = 1.3
SIDEWAYS_NOT_FLOOR_INR = 50_000_000.0  # ₹5 crore
SIDEWAYS_NOT_FLOOR_USD = 600_000.0
SIDEWAYS_PSCORE_MIN = 4

# ----- Bearish regime thresholds -----
BEARISH_DEATH_CROSS_FRESH_DAYS = 60
BEARISH_RSI_MAX_RECENT = 60.0
BEARISH_RSI_TODAY_MAX = 50.0
BEARISH_FLOOR_RATIO = 1.05  # today_ltp / week52_low
BEARISH_NOT_FLOOR_INR = 50_000_000.0
BEARISH_NOT_FLOOR_USD = 600_000.0

# ----- Cap -----
SWING_CAP = 25


def _bull_gates() -> list[dict[str, str]]:
    return [
        {
            "label": "Trend stack",
            "rule": (
                "today_ltp > sma_50 > sma_200 OR "
                f"golden_cross_days_ago ≤ "
                f"{BULL_GOLDEN_CROSS_FRESH_DAYS}"
            ),
            "why": "Establishes an uptrend or a fresh reversal.",
        },
        {
            "label": "Volume sweet spot",
            "rule": (
                f"{BULL_VOL_MIN} ≤ today_x_vol ≤ {BULL_VOL_MAX}"
            ),
            "why": (
                "Below 2× lacks conviction; above 5× is usually "
                "news-spike / exhaustion."
            ),
        },
        {
            "label": "Delivery confirmation",
            "rule": "current_dpc > avg_20d_dpc",
            "why": (
                "Today's delivery % above 20-day average — real "
                "buying, not just churn."
            ),
        },
        {
            "label": "Accumulation trend",
            "rule": "x_dv_20d > 1",
            "why": "20-day delivery quantity trending up.",
        },
        {
            "label": "Not exhausted",
            "rule": f"rsi < {BULL_RSI_MAX}",
            "why": "Leaves room before momentum reverses.",
        },
        {
            "label": "Quality floor",
            "rule": (
                f"pscore ≥ {BULL_PSCORE_MIN} AND "
                f"(pledged_pct < {BULL_PLEDGED_MAX} OR pledged_pct IS NULL)"
            ),
            "why": (
                "Filters out distressed names. Pledged NULL is "
                "tolerated — most stocks have no public pledge "
                "filing, so missing = no red flag."
            ),
        },
        {
            "label": "Room to run",
            "rule": (
                f"today_ltp / week52_high < {BULL_RANGE_MAX}"
            ),
            "why": "Not already at the top of the 52-week range.",
        },
        {
            "label": "Rec-engine bullish",
            "rule": (
                "rec_category ∈ "
                f"{sorted(BULLISH_CATEGORIES)}"
            ),
            "why": (
                "Rec engine independently confirms the long "
                "thesis (offensive / value / growth / "
                "hold_accumulate). Skipped if user has no rec "
                "run this month — chip surfaced."
            ),
        },
    ]


def _sideways_gates() -> list[dict[str, str]]:
    return [
        {
            "label": "MA convergence",
            "rule": (
                "|sma_50 - sma_200| / sma_200 < "
                f"{SIDEWAYS_MA_CONV_MAX}"
            ),
            "why": "MAs converged — no directional trend.",
        },
        {
            "label": "Price near SMA-50",
            "rule": (
                "|today_ltp - sma_50| / sma_50 < "
                f"{SIDEWAYS_PRICE_NEAR_SMA50}"
            ),
            "why": "Anchored to the mean, not on an edge.",
        },
        {
            "label": "RSI band",
            "rule": (
                f"{SIDEWAYS_RSI_MIN} ≤ rsi ≤ "
                f"{SIDEWAYS_RSI_MAX}"
            ),
            "why": "Mid-band RSI — no momentum either way.",
        },
        {
            "label": "Neutral volume",
            "rule": (
                f"{SIDEWAYS_VOL_MIN} ≤ today_x_vol ≤ "
                f"{SIDEWAYS_VOL_MAX}"
            ),
            "why": "No surge, no drought — true consolidation.",
        },
        {
            "label": "Liquidity floor",
            "rule": (
                f"today_not > ₹{SIDEWAYS_NOT_FLOOR_INR:,.0f} "
                f"(IN) / ${SIDEWAYS_NOT_FLOOR_USD:,.0f} (US)"
            ),
            "why": (
                "Avoid illiquid names; native-currency notional."
            ),
        },
        {
            "label": "Basic quality",
            "rule": f"pscore ≥ {SIDEWAYS_PSCORE_MIN}",
            "why": "Skips junk-tier consolidators.",
        },
    ]


def _bearish_gates() -> list[dict[str, str]]:
    return [
        {
            "label": "Death-cross active",
            "rule": (
                "sma_50 < sma_200 AND "
                "(death_cross_days_ago ≤ "
                f"{BEARISH_DEATH_CROSS_FRESH_DAYS} OR "
                "death_cross_days_ago == "
                f"{ESTABLISHED_CROSS_DAYS})"
            ),
            "why": (
                "Fresh structural downtrend (≤ 60 d cross) OR "
                "long-confirmed bearish (SMA-50 below SMA-200 for "
                "the entire 215-row window — the "
                f"{ESTABLISHED_CROSS_DAYS} sentinel). Both are "
                "swing-short candidates; stale mid-window crosses "
                "(61-214d) are excluded."
            ),
        },
        {
            "label": "RSI rollover",
            "rule": (
                f"rsi_max_10d ≥ {BEARISH_RSI_MAX_RECENT} AND "
                f"today_rsi ≤ {BEARISH_RSI_TODAY_MAX} AND "
                "today_rsi < rsi_3d_ago"
            ),
            "why": "Strength broken and still declining.",
        },
        {
            "label": "Lower-low break",
            "rule": "today_low < rolling_low_20d_prev",
            "why": "Decisive break of 20-day floor.",
        },
        {
            "label": "Room to fall",
            "rule": (
                f"today_ltp / week52_low > "
                f"{BEARISH_FLOOR_RATIO}"
            ),
            "why": "Not already capitulated — swing-shortable.",
        },
        {
            "label": "Liquidity floor",
            "rule": (
                f"today_not > ₹{BEARISH_NOT_FLOOR_INR:,.0f} "
                f"(IN) / ${BEARISH_NOT_FLOOR_USD:,.0f} (US)"
            ),
            "why": (
                "Avoid illiquid noise masking as breakdowns."
            ),
        },
    ]


_SUMMARY = {
    "bull": (
        "Trend-up stocks with fresh delivery-backed demand "
        "confirmed by the LLM recommendation engine."
    ),
    "sideways": (
        "Range-bound, liquid stocks oscillating around SMA-50 "
        "with neutral momentum — mean-reversion candidates."
    ),
    "bearish": (
        "Active downtrends with RSI rolling over and breaking "
        "below their 20-day low — swing-shortable structure."
    ),
}

_RANK = {
    "bull": {
        "formula": (
            "max(rec_expected_return_pct, 0) * x_dv_20d * "
            "today_x_vol"
        ),
        "direction": "DESC",
        "cap": SWING_CAP,
        "degraded": (
            "When no rec run for user this month, reduces to "
            "x_dv_20d * today_x_vol."
        ),
    },
    "sideways": {
        "formula": (
            "min(today_ltp - rolling_low_20d_prev, "
            "rolling_high_20d_prev - today_ltp) / today_ltp"
        ),
        "direction": "ASC",
        "cap": SWING_CAP,
        "degraded": None,
    },
    "bearish": {
        "formula": (
            "(1 / (death_cross_days_ago + 1)) * "
            "max(0, 60 - today_rsi) * "
            "(rolling_low_20d_prev - today_low) / "
            "rolling_low_20d_prev"
        ),
        "direction": "DESC",
        "cap": SWING_CAP,
        "degraded": None,
    },
}


def build_methodology(regime: Regime) -> dict[str, Any]:
    """Return the structured methodology block for a regime.

    Consumed by the route as the ``methodology`` field of the
    response and by the standalone
    ``/swing-setups/methodology?regime=...`` endpoint.
    """
    if regime == "bull":
        gates = _bull_gates()
    elif regime == "sideways":
        gates = _sideways_gates()
    elif regime == "bearish":
        gates = _bearish_gates()
    else:
        raise ValueError(f"unknown regime: {regime!r}")
    return {
        "regime": regime,
        "summary": _SUMMARY[regime],
        "gates": gates,
        "rank": _RANK[regime],
    }


def _safe_float(v: float | int | None) -> float | None:
    """Return v as float unless NaN/None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


def passes_bull(
    row: AdvancedRow, rec_gate_applied: bool,
) -> bool:
    """Return True iff the row passes ALL bull-regime gates.

    ``rec_gate_applied`` is False when the user has no rec run
    this IST month — in that case the rec-category gate is
    bypassed (graceful degrade; transparency chip surfaced by
    the route).
    """
    today_ltp = _safe_float(row.today_ltp)
    sma_50 = _safe_float(row.sma_50)
    sma_200 = _safe_float(row.sma_200)
    gxa = row.golden_cross_days_ago
    today_x_vol = _safe_float(row.today_x_vol)
    current_dpc = _safe_float(row.current_dpc)
    avg_20d_dpc = _safe_float(row.avg_20d_dpc)
    x_dv_20d = _safe_float(row.x_dv_20d)
    rsi = _safe_float(row.rsi)
    pscore = row.pscore
    pledged = _safe_float(row.pledged)
    w52_high = _safe_float(row.week_52_high)

    # Trend stack OR fresh golden cross.
    stack_ok = (
        today_ltp is not None
        and sma_50 is not None
        and sma_200 is not None
        and today_ltp > sma_50 > sma_200
    )
    fresh_cross = (
        gxa is not None
        and 0 <= gxa <= BULL_GOLDEN_CROSS_FRESH_DAYS
    )
    if not (stack_ok or fresh_cross):
        return False

    # Volume sweet spot.
    if today_x_vol is None or not (
        BULL_VOL_MIN <= today_x_vol <= BULL_VOL_MAX
    ):
        return False

    # Delivery confirmation.
    if (
        current_dpc is None
        or avg_20d_dpc is None
        or current_dpc <= avg_20d_dpc
    ):
        return False

    # Accumulation.
    if x_dv_20d is None or x_dv_20d <= 1.0:
        return False

    # Not exhausted.
    if rsi is None or rsi >= BULL_RSI_MAX:
        return False

    # Quality.
    if pscore is None or pscore < BULL_PSCORE_MIN:
        return False
    # Pledged: tolerate NULL — promoter_holdings coverage is
    # sparse in the dataset (most names have no pledge filing).
    # Only reject when we have a positive signal of distress
    # (pledged >= 10%). Absence is treated as "no red flag",
    # not "fail gate".
    if pledged is not None and pledged >= BULL_PLEDGED_MAX:
        return False

    # Room to run.
    if (
        today_ltp is None
        or w52_high is None
        or w52_high == 0
        or today_ltp / w52_high >= BULL_RANGE_MAX
    ):
        return False

    # Rec engine — skip when degraded. Category only; severity is
    # surfaced on the row but does not gate in Phase A.
    if rec_gate_applied:
        if row.rec_category not in BULLISH_CATEGORIES:
            return False

    return True


def rank_bull(row: AdvancedRow, rec_gate_applied: bool) -> float:
    """Bull rank score; sort DESC. Degrades to vol*delivery when
    rec-engine is unavailable for the user this month.
    """
    rec_ret = (
        _safe_float(row.rec_expected_return_pct)
        if rec_gate_applied else None
    )
    rec_mult = (
        max(rec_ret or 1.0, 0.0) if rec_gate_applied else 1.0
    )
    x_dv = _safe_float(row.x_dv_20d) or 0.0
    x_vol = _safe_float(row.today_x_vol) or 0.0
    return rec_mult * x_dv * x_vol


def passes_sideways(row: AdvancedRow, market: str) -> bool:
    today_ltp = _safe_float(row.today_ltp)
    sma_50 = _safe_float(row.sma_50)
    sma_200 = _safe_float(row.sma_200)
    rsi = _safe_float(row.rsi)
    today_x_vol = _safe_float(row.today_x_vol)
    today_not = _safe_float(row.today_not)
    pscore = row.pscore

    # MA convergence.
    if (
        sma_50 is None
        or sma_200 is None
        or sma_200 == 0
        or abs(sma_50 - sma_200) / abs(sma_200)
        >= SIDEWAYS_MA_CONV_MAX
    ):
        return False

    # Price near SMA-50.
    if (
        today_ltp is None
        or sma_50 is None
        or sma_50 == 0
        or abs(today_ltp - sma_50) / abs(sma_50)
        >= SIDEWAYS_PRICE_NEAR_SMA50
    ):
        return False

    # RSI band.
    if (
        rsi is None
        or not (SIDEWAYS_RSI_MIN <= rsi <= SIDEWAYS_RSI_MAX)
    ):
        return False

    # Volume band.
    if (
        today_x_vol is None
        or not (
            SIDEWAYS_VOL_MIN <= today_x_vol <= SIDEWAYS_VOL_MAX
        )
    ):
        return False

    # Liquidity floor (native currency).
    floor = (
        SIDEWAYS_NOT_FLOOR_USD if market == "us"
        else SIDEWAYS_NOT_FLOOR_INR
    )
    if today_not is None or today_not <= floor:
        return False

    # Quality.
    if pscore is None or pscore < SIDEWAYS_PSCORE_MIN:
        return False

    return True


def rank_sideways(row: AdvancedRow) -> float:
    """Distance-to-band-edge fraction. Sort ASC (smaller = nearer
    edge = higher priority). Returns inf when band is missing.
    """
    today_ltp = _safe_float(row.today_ltp)
    low = _safe_float(row.rolling_low_20d_prev)
    high = _safe_float(row.rolling_high_20d_prev)
    if (
        today_ltp is None
        or low is None
        or high is None
        or today_ltp == 0
    ):
        return float("inf")
    return min(today_ltp - low, high - today_ltp) / today_ltp


def passes_bearish(row: AdvancedRow, market: str) -> bool:
    """Active-distribution short-bias filter.

    Gates: death-cross active+fresh, RSI rollover from >=60 to <=50
    with 3d decline, lower-low break vs 20d prev low, room above
    52w floor, liquidity floor.
    """
    sma_50 = _safe_float(row.sma_50)
    sma_200 = _safe_float(row.sma_200)
    dxa = row.death_cross_days_ago
    rsi = _safe_float(row.rsi)
    rsi_3d = _safe_float(row.rsi_3d_ago)
    rsi_max10 = _safe_float(row.rsi_max_10d)
    today_low = _safe_float(row.today_low)
    rb_low = _safe_float(row.rolling_low_20d_prev)
    today_ltp = _safe_float(row.today_ltp)
    w52_low = _safe_float(row.week_52_low)
    today_not = _safe_float(row.today_not)

    # Death-cross active + fresh-or-established. The
    # ``ESTABLISHED_CROSS_DAYS`` sentinel from
    # :func:`_death_cross_days_ago` means SMA-50 has been below
    # SMA-200 for the entire 215-row window — the *deepest*
    # bearish state (long-confirmed downtrend), exactly what a
    # swing-short candidate wants. Accept both fresh crosses
    # (≤ 60 days) and the established-bearish sentinel.
    if sma_50 is None or sma_200 is None or sma_50 >= sma_200:
        return False
    if dxa is None:
        return False
    if not (
        dxa <= BEARISH_DEATH_CROSS_FRESH_DAYS
        or dxa == ESTABLISHED_CROSS_DAYS
    ):
        return False

    # RSI rollover.
    if rsi_max10 is None or rsi_max10 < BEARISH_RSI_MAX_RECENT:
        return False
    if rsi is None or rsi > BEARISH_RSI_TODAY_MAX:
        return False
    if rsi_3d is None or rsi >= rsi_3d:
        return False

    # Lower-low break.
    if (
        today_low is None or rb_low is None
        or today_low >= rb_low
    ):
        return False

    # Room to fall above 52-week floor.
    if (
        today_ltp is None or w52_low is None or w52_low == 0
        or today_ltp / w52_low <= BEARISH_FLOOR_RATIO
    ):
        return False

    # Liquidity.
    floor = (
        BEARISH_NOT_FLOOR_USD if market == "us"
        else BEARISH_NOT_FLOOR_INR
    )
    if today_not is None or today_not <= floor:
        return False

    return True


def rank_bearish(row: AdvancedRow) -> float:
    """Higher = stronger short. Combines cross freshness, RSI
    severity, and decisiveness of the lower-low break.
    """
    dxa = row.death_cross_days_ago
    rsi = _safe_float(row.rsi)
    today_low = _safe_float(row.today_low)
    rb_low = _safe_float(row.rolling_low_20d_prev)
    if (
        dxa is None or rsi is None
        or today_low is None or rb_low is None
    ):
        return 0.0
    if rb_low == 0:
        return 0.0
    fresh = 1.0 / (dxa + 1)
    severity = max(0.0, 60.0 - rsi)
    decisiveness = (rb_low - today_low) / rb_low
    return fresh * severity * decisiveness

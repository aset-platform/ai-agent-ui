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

from typing import Any, Literal

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
                f"pledged_pct < {BULL_PLEDGED_MAX}"
            ),
            "why": "Filters out distressed names.",
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
                "death_cross_days_ago ≤ "
                f"{BEARISH_DEATH_CROSS_FRESH_DAYS}"
            ),
            "why": (
                "Fresh structural downtrend, not stale weakness."
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

"""Pre-registered feature→dimension map + composite (fixed a-priori)."""
from __future__ import annotations

# Every raw column of algo.entry_labeled_outcomes the analysis may read,
# plus the derived breadth ratio. Keep in sync with the table (guarded by
# test_dimensions + the cohort loader).
KNOWN_COLUMNS: frozenset[str] = frozenset({
    "rsi2_at_entry", "dist_sma50_pct", "dist_sma200_pct",
    "ret_1d_prior", "ret_3d_prior", "gap_pct",
    "ess_absorption_volume_score", "ess_selling_deceleration_score",
    "ess_trend_stability_score", "qm_score", "ess_score",
    "qm_mdd_pctile", "qm_rs_pctile", "qm_sharpe_pctile",
    "breadth_oversold_pct",
})

DIMENSIONS: dict[str, list[str]] = {
    "pullback_absorption": [
        "rsi2_at_entry", "dist_sma50_pct", "dist_sma200_pct",
        "ret_1d_prior", "ret_3d_prior",
        "ess_absorption_volume_score", "ess_selling_deceleration_score",
        "ess_trend_stability_score",
    ],
    "strength_resilience": [
        "qm_score", "ess_score", "qm_mdd_pctile", "qm_rs_pctile",
        "qm_sharpe_pctile",
    ],
    "breadth_regime": ["breadth_oversold_pct"],
    "falling_knife": ["ret_3d_prior", "gap_pct"],
}

# Pre-registered composite — hypothesized directions from the R1 thesis +
# the July RSI2 post-mortem. Fixed BEFORE looking at results (a wrong
# direction shows as component/composite AUC < 0.5). See spec §4B.
COMPOSITE: list[tuple[str, float]] = [
    ("ess_absorption_volume_score", 1.0),   # more absorption = better
    ("qm_mdd_pctile", -1.0),                 # less-severe drawdown = better
    ("breadth_oversold_pct", -1.0),          # fewer oversold = idiosyncratic
    ("ret_3d_prior", 1.0),                   # less-negative 3d = not a knife
]

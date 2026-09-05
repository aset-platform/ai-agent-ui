import pandas as pd

from backend.algo.research.entry_strength_feasibility.analysis import (
    analyze,
    composite_score,
)


def _df():
    # 4 rows, ess_absorption perfectly separates (winners higher)
    return pd.DataFrame({
        "label_win": [False, False, True, True],
        "ess_absorption_volume_score": [1.0, 2.0, 3.0, 4.0],
        "qm_mdd_pctile": [0.9, 0.8, 0.2, 0.1],
        "breadth_oversold_pct": [0.1, 0.1, 0.1, 0.1],
        "ret_3d_prior": [-1.0, -2.0, 0.5, 0.4],
        # remaining feature cols present but flat
        "rsi2_at_entry": [3.0, 3.0, 3.0, 3.0],
        "dist_sma50_pct": [0.0] * 4, "dist_sma200_pct": [0.0] * 4,
        "ret_1d_prior": [0.0] * 4, "gap_pct": [0.0] * 4,
        "ess_selling_deceleration_score": [0.0] * 4,
        "ess_trend_stability_score": [0.0] * 4, "qm_score": [0.0] * 4,
        "ess_score": [0.0] * 4, "qm_rs_pctile": [0.0] * 4,
        "qm_sharpe_pctile": [0.0] * 4,
    })


def test_composite_separates_when_components_aligned():
    comp = composite_score(_df())
    # winners (idx 2,3) should score higher than losers (0,1)
    assert comp.iloc[2] > comp.iloc[0]
    assert comp.iloc[3] > comp.iloc[1]


def test_analyze_ranks_absorption_top_and_flags_min_n():
    res = analyze(_df(), min_n=30, mode="live")
    assert res.n_total == 4 and res.n_win == 2 and res.n_loss == 2
    assert res.min_n_ok is False           # 4 < 30
    assert res.min_n == 30
    top = res.feature_stats[0]
    assert top.feature == "ess_absorption_volume_score"
    assert top.auc == 1.0 and top.direction == "higher"
    assert res.composite.auc >= 0.5


def test_analyze_labels_requested_mode_not_derived():
    # blended --mode all cohort: internal rows carry their own
    # per-trade mode, mixing live and paper.
    df = _df()
    df["mode"] = ["live", "paper", "live", "paper"]
    res = analyze(df, min_n=30, mode="all")
    assert res.mode == "all"

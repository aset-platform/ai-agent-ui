from backend.algo.research.entry_strength_feasibility.analysis import (
    CompositeStat,
    FeasibilityResult,
    FeatureStat,
)
from backend.algo.research.entry_strength_feasibility.report import (
    render_report,
)


def _result():
    fs = FeatureStat(
        dimension="strength_resilience", feature="qm_mdd_pctile", n=44,
        auc=0.71, separation=0.21, win_rate_top=0.75, win_rate_bottom=0.45,
        mwu_p=0.03, direction="lower",
    )
    return FeasibilityResult(
        mode="live", n_total=44, n_win=27, n_loss=17,
        first_date="2026-05-29", last_date="2026-08-11", min_n_ok=False,
        feature_stats=(fs,),
        composite=CompositeStat(n=44, auc=0.63, win_rate_top=0.7,
                                win_rate_bottom=0.5),
    )


def test_render_report_has_caveat_summary_and_feature():
    md = render_report(_result())
    assert "out-of-sample" in md.lower()      # caveat banner present
    assert "n=44" in md or "44" in md
    assert "qm_mdd_pctile" in md               # ranking row
    assert "0.63" in md                         # composite headline AUC
    assert "min_n" in md.lower() or "underpowered" in md.lower()

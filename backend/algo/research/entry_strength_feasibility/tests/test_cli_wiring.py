from unittest.mock import patch

from backend.algo.research.entry_strength_feasibility import __main__ as cli

_RAW = [
    {"mode": "live", "filled": True, "outcome_settled": True,
     "label_win": w, "dry_run": False, "trade_date": "2026-08-01",
     "breadth_oversold": 5, "breadth_total": 100,
     "ess_absorption_volume_score": v, "qm_mdd_pctile": 1 - v / 10.0,
     "breadth_oversold_pct": 0.05, "ret_3d_prior": v,
     "rsi2_at_entry": 3.0, "dist_sma50_pct": 0.0, "dist_sma200_pct": 0.0,
     "ret_1d_prior": 0.0, "gap_pct": 0.0,
     "ess_selling_deceleration_score": 0.0,
     "ess_trend_stability_score": 0.0, "qm_score": 0.0, "ess_score": 0.0,
     "qm_rs_pctile": 0.0, "qm_sharpe_pctile": 0.0}
    for w, v in [(False, 1.0), (False, 2.0), (True, 3.0), (True, 4.0)]
]


def test_cli_writes_report(tmp_path):
    with patch.object(cli, "load_labeled_rows", return_value=_RAW):
        rc = cli.main(["--mode", "live", "--out-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "report.md").exists()
    assert "Entry-Strength Feasibility" in (
        tmp_path / "report.md"
    ).read_text()

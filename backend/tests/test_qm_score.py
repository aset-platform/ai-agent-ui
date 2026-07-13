from __future__ import annotations

from qm_score import compute_qm_scores


def test_compute_qm_scores_returns_one_result_per_ticker():
    inputs = {
        "A.NS": {
            "sharpe_ratio": 1.5,
            "blended_rs": 10.0,
            "mdd_6m": -5.0,
            "atr_pct": 3.5,
            "dist_sma200": 25.0,
        },
        "B.NS": {
            "sharpe_ratio": 0.5,
            "blended_rs": -2.0,
            "mdd_6m": -15.0,
            "atr_pct": 8.0,
            "dist_sma200": 60.0,
        },
    }
    results = compute_qm_scores(inputs)
    assert set(results.keys()) == {"A.NS", "B.NS"}
    assert results["A.NS"].score is not None
    assert results["B.NS"].score is not None


def test_compute_qm_scores_hand_verified_values():
    # A.NS strictly dominates B.NS on every one of the 5 inputs, so it
    # must take 100th-percentile rank on Sharpe/RS/MDD and max
    # closeness on ATR%/SMA200 distance. Hand-computed via the same
    # _pct_rank/_closeness formulas (see task-15-report for the
    # by-hand derivation): A.NS -> 100.0, B.NS -> 8.0.
    inputs = {
        "A.NS": {
            "sharpe_ratio": 1.5,
            "blended_rs": 10.0,
            "mdd_6m": -5.0,
            "atr_pct": 3.5,
            "dist_sma200": 25.0,
        },
        "B.NS": {
            "sharpe_ratio": 0.5,
            "blended_rs": -2.0,
            "mdd_6m": -15.0,
            "atr_pct": 8.0,
            "dist_sma200": 60.0,
        },
    }
    results = compute_qm_scores(inputs)

    a = results["A.NS"]
    assert a.sharpe_pctile == 100.0
    assert a.rs_pctile == 100.0
    assert a.mdd_pctile == 100.0
    assert a.atr_closeness == 100.0
    assert a.sma200_closeness == 100.0
    assert a.score == 100.0

    b = results["B.NS"]
    assert b.sharpe_pctile == 0.0
    assert b.rs_pctile == 0.0
    assert b.mdd_pctile == 0.0
    assert b.atr_closeness == 30.0
    assert b.sma200_closeness == 50.0
    assert b.score == 8.0

    assert a.score > b.score


def test_compute_qm_scores_single_ticker_batch_defaults_to_midpoint():
    # n <= 1 short-circuits _pct_rank to 50.0 for every percentile
    # factor — a lone ticker can't be ranked against anything.
    inputs = {
        "A.NS": {
            "sharpe_ratio": 1.5,
            "blended_rs": 10.0,
            "mdd_6m": -5.0,
            "atr_pct": 3.5,
            "dist_sma200": 25.0,
        },
    }
    result = compute_qm_scores(inputs)["A.NS"]
    assert result.sharpe_pctile == 50.0
    assert result.rs_pctile == 50.0
    assert result.mdd_pctile == 50.0
    # Closeness curves don't depend on batch size — still maxed out.
    assert result.atr_closeness == 100.0
    assert result.sma200_closeness == 100.0
    assert result.score == 60.0  # 0.3*50 + 0.3*50 + 0.2*50 + 0.1*100*2


def test_compute_qm_scores_missing_inputs_renormalize_weights():
    # Only ATR%/SMA200 available (10% + 10% weight) — the weighted
    # average must renormalize over just those two, not divide by
    # the full 1.0 with zeros substituted for the missing factors.
    inputs = {
        "A.NS": {
            "sharpe_ratio": None,
            "blended_rs": None,
            "mdd_6m": None,
            "atr_pct": 3.0,  # closeness 100
            "dist_sma200": 20.0,  # closeness 100
        },
    }
    result = compute_qm_scores(inputs)["A.NS"]
    assert result.sharpe_pctile is None
    assert result.rs_pctile is None
    assert result.mdd_pctile is None
    assert result.atr_closeness == 100.0
    assert result.sma200_closeness == 100.0
    assert result.score == 100.0


def test_compute_qm_scores_all_inputs_missing_returns_none_score():
    inputs = {
        "A.NS": {
            "sharpe_ratio": None,
            "blended_rs": None,
            "mdd_6m": None,
            "atr_pct": None,
            "dist_sma200": None,
        },
    }
    result = compute_qm_scores(inputs)["A.NS"]
    assert result.score is None

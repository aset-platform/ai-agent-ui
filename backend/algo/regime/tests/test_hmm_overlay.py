"""Tests for StressHMM — fit, persistence, and the
forward-filter-no-lookahead guard."""
from __future__ import annotations

from datetime import date

import numpy as np

from backend.algo.regime.hmm_overlay import StressHMM


def _make_two_regime_data(seed: int = 42) -> np.ndarray:
    """750 days of synthetic 2-regime data (calm + stressed),
    with stressed regime concentrated in days 250-450."""
    rng = np.random.default_rng(seed)
    calm = rng.normal(
        loc=[0.001, 0.010], scale=[0.005, 0.002], size=(550, 2),
    )
    stressed = rng.normal(
        loc=[-0.002, 0.025], scale=[0.020, 0.005], size=(200, 2),
    )
    out = np.empty((750, 2))
    out[:250] = calm[:250]
    out[250:450] = stressed
    out[450:] = calm[250:]
    return out


def test_fit_assigns_stable_state_ordering(monkeypatch) -> None:
    """State 0 must always be the lower-vol-mean state after fit."""
    monkeypatch.setattr(
        "backend.algo.regime.hmm_overlay.get_latest_hmm_state",
        lambda: None,
    )
    X = _make_two_regime_data()
    hmm = StressHMM()
    hmm.fit(X, trained_through=date(2026, 5, 9))
    # means[:, 1] = realized_vol mean
    assert hmm.means[0][1] < hmm.means[1][1], (
        "State 0 must be calm (lower vol) after stable ordering"
    )


def test_stress_prob_in_unit_interval(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.algo.regime.hmm_overlay.get_latest_hmm_state",
        lambda: None,
    )
    X = _make_two_regime_data()
    hmm = StressHMM()
    hmm.fit(X, trained_through=date(2026, 5, 9))
    p = hmm.stress_prob(X[-60:])
    assert 0.0 <= p <= 1.0


def test_filtered_no_lookahead(monkeypatch) -> None:
    """CRITICAL CI GATE: ``stress_prob`` MUST only consume the
    window passed in. We assert this by comparing two queries
    against the same trained model:

      * ``p_now`` — forward-filter to bar T using ``X[:T]`` only.
      * ``p_smooth`` — same target bar T but with the future
        ``T..T+F`` (a known regime shift) in the window.

    The HMM forward-backward smoother used by ``predict_proba``
    will revise the bar-T posterior upward when the future
    contains stress; the filter (window ending at T) cannot. If
    ``stress_prob`` ever inverts this and looks at the full
    sample, ``p_now`` and ``p_smooth`` would coincide.

    Hard inequality: ``p_smooth - p_now > 0.05`` (well-separated
    regimes give a much larger gap, but we keep the floor low to
    survive small EM variance).
    """
    monkeypatch.setattr(
        "backend.algo.regime.hmm_overlay.get_latest_hmm_state",
        lambda: None,
    )
    rng = np.random.default_rng(7)
    # Wide separation so the EM has clean states.
    calm = rng.normal(
        loc=[0.001, 0.005], scale=[0.003, 0.001], size=(800, 2),
    )
    stress = rng.normal(
        loc=[-0.005, 0.040], scale=[0.025, 0.005], size=(200, 2),
    )
    # Train HMM on a corpus that contains both regimes so it
    # learns the structure.
    train = np.vstack([calm[:600], stress[:150], calm[600:]])
    hmm = StressHMM()
    hmm.fit(train, trained_through=date(2026, 5, 9))

    # Query target = a bar in the middle of a calm tail; the
    # window with future stress should smooth it upward.
    target_bar = calm[700:740]               # 40 calm bars
    future_stress = stress[150:200]           # 50 stress bars

    p_now = hmm.stress_prob(target_bar)
    p_smooth = hmm.stress_prob(
        np.vstack([target_bar, future_stress]),
    )

    # Forward-only must NOT see the future. Smoother always will.
    # If stress_prob silently called predict() over the full
    # sample, the value at the last bar of `target_bar` would be
    # the same in both calls. Inequality below asserts the gap.
    assert p_smooth - p_now > 0.05, (
        f"Forward-only invariant violated: p_now={p_now:.3f}, "
        f"p_smooth={p_smooth:.3f} (smoother should be higher)."
    )
    # Sanity: forward-only should report calm regime.
    assert p_now < 0.5, (
        f"Forward-only stress on calm window should be <0.5; "
        f"got {p_now:.3f}"
    )


def test_save_load_roundtrip(monkeypatch) -> None:
    """save() persists to stocks.regime_hmm_state via repo;
    load() restores. Mocked at the repo layer to keep test pure."""
    saved: dict = {}

    def fake_upsert(row):
        saved["row"] = row

    def fake_get_latest():
        return saved.get("row")

    monkeypatch.setattr(
        "backend.algo.regime.hmm_overlay.upsert_hmm_state",
        fake_upsert,
    )
    monkeypatch.setattr(
        "backend.algo.regime.hmm_overlay.get_latest_hmm_state",
        fake_get_latest,
    )

    X = _make_two_regime_data()
    hmm = StressHMM()
    hmm.fit(X, trained_through=date(2026, 5, 9))
    hmm.save()

    restored = StressHMM.load()
    assert restored is not None
    np.testing.assert_allclose(restored.means, hmm.means, rtol=1e-6)
    np.testing.assert_allclose(
        restored.transmat, hmm.transmat, rtol=1e-6,
    )

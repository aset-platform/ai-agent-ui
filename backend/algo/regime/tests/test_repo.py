"""Round-trip tests for regime_history + regime_hmm_state via the
real Iceberg catalog. Requires the Docker stack up (DuckDB + Iceberg
SQLite catalog mounted)."""
from __future__ import annotations

from datetime import date

import pytest

from backend.algo.regime.repo import (
    HmmStateRow,
    RegimeRow,
    get_latest_hmm_state,
    get_latest_regime,
    get_regime_history,
    upsert_hmm_state,
    upsert_regime_history,
)


def test_upsert_regime_history_roundtrip() -> None:
    row = RegimeRow(
        bar_date=date(2026, 5, 9),
        regime_label="BULL",
        stress_prob=0.12,
        rule_inputs={"vix": 13.5, "r30": 0.05, "r60": 0.10},
        classifier_version="v1.0",
    )
    upsert_regime_history([row])

    latest = get_latest_regime()
    assert latest is not None
    assert latest.bar_date == date(2026, 5, 9)
    assert latest.regime_label == "BULL"
    assert latest.stress_prob == pytest.approx(0.12)
    assert latest.rule_inputs == {"vix": 13.5, "r30": 0.05, "r60": 0.10}


def test_upsert_is_idempotent_replaces_same_date() -> None:
    """Re-inserting same bar_date overwrites, doesn't duplicate."""
    upsert_regime_history([
        RegimeRow(
            bar_date=date(2026, 5, 8),
            regime_label="SIDEWAYS",
            stress_prob=0.40,
            rule_inputs={"vix": 22.0},
            classifier_version="v1.0",
        )
    ])
    upsert_regime_history([
        RegimeRow(
            bar_date=date(2026, 5, 8),
            regime_label="BULL",   # changed
            stress_prob=0.30,
            rule_inputs={"vix": 14.0},
            classifier_version="v1.0",
        )
    ])
    history = get_regime_history(
        start=date(2026, 5, 8), end=date(2026, 5, 8),
    )
    assert len(history) == 1
    assert history[0].regime_label == "BULL"


def test_hmm_state_roundtrip() -> None:
    row = HmmStateRow(
        trained_through=date(2026, 4, 30),
        transmat=[[0.95, 0.05], [0.10, 0.90]],
        means=[[0.001, 0.012], [-0.002, 0.025]],
        covars=[
            [[0.0001, 0.0], [0.0, 0.0001]],
            [[0.0004, 0.0], [0.0, 0.0004]],
        ],
        n_observations=1500,
    )
    upsert_hmm_state(row)
    got = get_latest_hmm_state()
    assert got is not None
    assert got.trained_through == date(2026, 4, 30)
    assert got.transmat[0][0] == pytest.approx(0.95)
    assert got.n_observations == 1500

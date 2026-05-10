"""Verify the regime_history + regime_hmm_state schemas are
registered with the catalog and have the expected columns."""
from __future__ import annotations

from backend.algo.regime.iceberg_init import (
    REGIME_HISTORY_TABLE,
    REGIME_HMM_STATE_TABLE,
    regime_history_schema,
    regime_hmm_state_schema,
)


def test_regime_history_columns() -> None:
    s = regime_history_schema()
    names = {f.name for f in s.fields}
    assert {
        "bar_date",
        "regime_label",
        "stress_prob",
        "rule_inputs_json",
        "classifier_version",
    } <= names


def test_regime_hmm_state_columns() -> None:
    s = regime_hmm_state_schema()
    names = {f.name for f in s.fields}
    assert {
        "trained_through",
        "transmat_json",
        "means_json",
        "covars_json",
        "n_observations",
    } <= names


def test_table_identifiers_namespaced() -> None:
    assert REGIME_HISTORY_TABLE == "stocks.regime_history"
    assert REGIME_HMM_STATE_TABLE == "stocks.regime_hmm_state"

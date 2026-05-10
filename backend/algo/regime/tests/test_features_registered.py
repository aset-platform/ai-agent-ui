"""Verify regime + breadth + VIX feature keys are registered in
the strategy AST feature catalog."""
from __future__ import annotations

from backend.algo.strategy.features import (
    FEATURE_BY_KEY,
    FEATURE_KEYS,
)


REGIME_KEYS = {
    "regime_label",
    "stress_prob",
    "pct_above_50sma",
    "pct_above_200sma",
    "midcap_largecap_ratio",
    "vix_close",
    "vix_sma_20",
}


def test_regime_keys_registered() -> None:
    missing = REGIME_KEYS - FEATURE_KEYS
    assert not missing, f"Missing feature keys: {missing}"


def test_regime_label_is_string_type() -> None:
    assert FEATURE_BY_KEY["regime_label"].type == "string"


def test_stress_prob_is_float() -> None:
    assert FEATURE_BY_KEY["stress_prob"].type == "float"


def test_regime_features_have_regime_source() -> None:
    for k in ("regime_label", "stress_prob"):
        assert FEATURE_BY_KEY[k].source == "regime"

"""All factor keys must appear in the strategy AST FEATURE_KEYS
registry."""
from __future__ import annotations

from backend.algo.factors.iceberg_init import ALL_FACTOR_KEYS
from backend.algo.strategy.features import FEATURE_BY_KEY, FEATURE_KEYS

NON_BREADTH = [
    k for k in ALL_FACTOR_KEYS
    if k not in {
        "pct_above_50sma", "pct_above_200sma", "midcap_largecap_ratio",
    }
]


def test_all_factor_keys_registered() -> None:
    missing = set(ALL_FACTOR_KEYS) - FEATURE_KEYS
    assert not missing, f"Missing factor keys: {missing}"


def test_non_breadth_factor_keys_have_factor_source() -> None:
    for k in NON_BREADTH:
        assert FEATURE_BY_KEY[k].source == "factor", k


def test_factor_keys_are_float() -> None:
    for k in NON_BREADTH:
        assert FEATURE_BY_KEY[k].type == "float", k

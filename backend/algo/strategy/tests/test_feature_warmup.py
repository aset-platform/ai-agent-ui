"""Tests for feature_warmup helpers (ASETPLTFRM-433)."""

from __future__ import annotations

import json
from pathlib import Path

from backend.algo.strategy.ast import parse_strategy
from backend.algo.strategy.feature_warmup import (
    DEFAULT_WARMUP_DAYS,
    compute_strategy_warmup_days,
    warmup_for_feature,
)


def test_warmup_for_window_suffix_features():
    assert warmup_for_feature("sma_5") == 5
    assert warmup_for_feature("sma_50") == 50
    assert warmup_for_feature("sma_200") == 200
    assert warmup_for_feature("ema_20") == 20
    assert warmup_for_feature("rsi_2") == 2
    assert warmup_for_feature("rsi_14") == 14
    assert warmup_for_feature("roc_5") == 5
    assert warmup_for_feature("atr_14") == 14


def test_warmup_for_distance_from_smaN_pattern():
    assert warmup_for_feature("distance_from_sma5") == 5
    assert warmup_for_feature("distance_from_sma200") == 200
    assert warmup_for_feature("distance_from_sma_20") == 20


def test_warmup_for_market_level_features_is_zero():
    # Per-ticker warmup is 0 — runtime pre-loads the regime cache
    # separately at session start.
    for f in (
        "regime_label", "stress_prob", "nifty_above_sma200",
        "nifty_30d_return_pct", "vix_close",
    ):
        assert warmup_for_feature(f) == 0


def test_warmup_for_unknown_feature_defaults_to_default():
    assert warmup_for_feature("totally_made_up") == DEFAULT_WARMUP_DAYS


def test_compute_strategy_warmup_picks_max_across_features():
    # AST referencing rsi_2 (2), sma_50 (50), sma_200 (200) → 200.
    root = {
        "type": "and",
        "operands": [
            {
                "type": "compare",
                "left": {"feature": "rsi_2"},
                "op": "<=", "right": {"literal": 5},
            },
            {
                "type": "compare",
                "left": {"feature": "sma_50"},
                "op": ">", "right": {"literal": 0},
            },
            {
                "type": "compare",
                "left": {"feature": "sma_200"},
                "op": ">", "right": {"literal": 0},
            },
        ],
    }
    assert compute_strategy_warmup_days(root) == 200


def test_compute_strategy_warmup_ignores_market_level_features():
    # AST with ONLY market-level features → warmup 0.
    root = {
        "type": "and",
        "operands": [
            {
                "type": "compare",
                "left": {"feature": "regime_label"},
                "op": "==", "right": {"literal": "BULL"},
            },
            {
                "type": "compare",
                "left": {"feature": "stress_prob"},
                "op": "<", "right": {"literal": 0.5},
            },
        ],
    }
    assert compute_strategy_warmup_days(root) == 0


def test_compute_strategy_warmup_walks_nested_if_then_else():
    # Mixed depths — make sure the walker descends.
    root = {
        "type": "if",
        "cond": {
            "type": "compare",
            "left": {"feature": "rsi_2"},
            "op": "<=", "right": {"literal": 5},
        },
        "then": {"type": "set_target_weight", "weight": 0.2},
        "else": {
            "type": "if",
            "cond": {
                "type": "compare",
                "left": {"feature": "distance_from_sma200"},
                "op": ">", "right": {"literal": 0},
            },
            "then": {"type": "hold"},
            "else": {"type": "hold"},
        },
    }
    assert compute_strategy_warmup_days(root) == 200


def test_v2_rsi2_template_warmup_is_200():
    # End-to-end check on the shipped template.
    path = (
        Path(__file__).parent.parent
        / "templates"
        / "rsi2_connors_daily_v2.json"
    )
    d = json.loads(path.read_text())
    strategy = parse_strategy(d)
    root = strategy.root.model_dump(by_alias=True)
    # v2 references rsi_2, distance_from_sma200, stress_prob,
    # nifty_above_sma200, nifty_30d_return_pct, distance_from_sma5
    # → max = 200 (distance_from_sma200).
    assert compute_strategy_warmup_days(root) == 200

"""CI gate: backend feature registry must match frontend mirror.

Loads strategyFeatureCatalog.ts as text, regex-extracts every
quoted key from the STRATEGY_FEATURES array, asserts equality
with FEATURE_KEYS.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.algo.strategy.features import FEATURE_KEYS

_FRONTEND_FILE = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "components"
    / "algo-trading"
    / "strategyFeatureCatalog.ts"
)
_BLOCK_RE = re.compile(
    r"export const STRATEGY_FEATURES\s*:\s*StrategyFeature\[\]\s*=\s*"
    r"\[(?P<body>.*?)\];",
    re.DOTALL,
)
_KEY_RE = re.compile(r'key:\s*"([a-z0-9_]+)"')


def _parse_keys() -> set[str]:
    text = _FRONTEND_FILE.read_text(encoding="utf-8")
    block = _BLOCK_RE.search(text)
    assert block is not None, "STRATEGY_FEATURES not found"
    return set(_KEY_RE.findall(block.group("body")))


def test_feature_registry_in_sync():
    backend_keys = set(FEATURE_KEYS)
    frontend_keys = _parse_keys()
    assert backend_keys == frontend_keys, (
        f"Feature registry drift — frontend extra: "
        f"{frontend_keys - backend_keys}; "
        f"backend extra: {backend_keys - frontend_keys}"
    )

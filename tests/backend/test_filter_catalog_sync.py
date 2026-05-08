"""CI gate: backend filter allowlist must match frontend mirror.

Loads the TS literal as text (no Node runtime in the backend
container), regex-extracts every quoted key from the
TECH_FILTER_CATALOG and FUND_FILTER_CATALOG arrays, asserts
equality with TECH_KEYS / FUND_KEYS. Either side adding /
removing a key without the other fails CI.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.advanced_analytics_filters import FUND_KEYS, TECH_KEYS

_FRONTEND_FILE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "components"
    / "advanced-analytics"
    / "filterCatalogs.ts"
)

_BLOCK_RE = re.compile(
    r"export const (?P<name>TECH|FUND)_FILTER_CATALOG"
    r"\s*:\s*FilterOption\[\]\s*=\s*\[(?P<body>.*?)\];",
    re.DOTALL,
)
_KEY_RE = re.compile(r'key:\s*"([a-z0-9_]+)"')


# Remove this xfail mark in Task 5 once filterCatalogs.ts lands.
pytestmark = pytest.mark.xfail(
    reason="frontend filterCatalogs.ts lands in Task 5",
    strict=False,
)


def _parse_keys(name: str) -> set[str]:
    text = _FRONTEND_FILE.read_text(encoding="utf-8")
    block = next(
        (m for m in _BLOCK_RE.finditer(text) if m.group("name") == name),
        None,
    )
    assert block is not None, f"{name}_FILTER_CATALOG not found"
    return set(_KEY_RE.findall(block.group("body")))


def test_tech_catalog_in_sync():
    assert _parse_keys("TECH") == set(TECH_KEYS), (
        "frontend TECH_FILTER_CATALOG drift — update either "
        "filterCatalogs.ts or advanced_analytics_filters.py"
    )


def test_fund_catalog_in_sync():
    assert _parse_keys("FUND") == set(FUND_KEYS), (
        "frontend FUND_FILTER_CATALOG drift — update either "
        "filterCatalogs.ts or advanced_analytics_filters.py"
    )

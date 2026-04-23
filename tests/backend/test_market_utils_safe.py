"""Unit tests for ``backend.market_utils.safe_str`` / ``safe_sector``.

Regression guards against NaN-truthy bugs that leak sentinel tokens
(``"NaN"``, ``"None"``, ``"null"``, ``"N/A"``) into LLM prompts,
Sectors tab groupby keys, and dashboard allocation strings.
"""

from __future__ import annotations

import math

import pytest

from backend.market_utils import safe_sector, safe_str


class TestSafeStrRejectsMissing:
    @pytest.mark.parametrize(
        "val",
        [None, "", "   ", float("nan"), math.nan],
    )
    def test_obvious_missing(self, val):
        assert safe_str(val) is None

    @pytest.mark.parametrize(
        "val",
        [
            "NaN", "nan", "NAN", "  NaN  ",
            "None", "none", "NULL", "null",
            "N/A", "n/a", "NA", "na", "NaT",
        ],
    )
    def test_sentinel_strings(self, val):
        """Regression: yfinance / pandas NaN stringification
        produced these values — must map to None so callers
        fall through to their fallback.
        """
        assert safe_str(val) is None

    @pytest.mark.parametrize(
        "val,expected",
        [
            ("Technology", "Technology"),
            ("  Financial Services  ", "Financial Services"),
            ("Healthcare", "Healthcare"),
            ("Naniwa", "Naniwa"),
        ],
    )
    def test_legit_values_preserved(self, val, expected):
        assert safe_str(val) == expected


class TestSafeSector:
    def test_default_fallback(self):
        assert safe_sector("NaN") == "Other"
        assert safe_sector(None) == "Other"
        assert safe_sector(float("nan")) == "Other"

    def test_custom_fallback(self):
        assert (
            safe_sector("None", fallback="ETF/Other")
            == "ETF/Other"
        )

    def test_legit_value(self):
        assert safe_sector("Technology") == "Technology"

"""_safe_decimal coverage for NaN/None defensiveness."""
from __future__ import annotations

import math
from decimal import Decimal

from backend.algo.backtest.data_source import _safe_decimal


def test_none_returns_none():
    assert _safe_decimal(None) is None


def test_float_nan_returns_none():
    assert _safe_decimal(float("nan")) is None


def test_string_sentinels_return_none():
    for s in [
        "", "  ", "None", "none", "NULL", "NaN",
        "nan", "N/A", "na", "NaT",
    ]:
        assert _safe_decimal(s) is None, s


def test_clean_numeric_string_parses():
    assert _safe_decimal("100.50") == Decimal("100.50")


def test_clean_int_parses():
    assert _safe_decimal(42) == Decimal("42")


def test_clean_float_parses():
    assert _safe_decimal(3.14) == Decimal("3.14")


def test_garbage_string_returns_none():
    assert _safe_decimal("not-a-number") is None
    assert _safe_decimal("$100") is None


def test_decimal_nan_returns_none():
    """Belt-and-braces — even if Decimal('nan') somehow gets in."""
    assert math.isnan(float(Decimal("NaN")))
    # This goes through the .is_nan() branch.
    assert _safe_decimal(Decimal("NaN")) is None

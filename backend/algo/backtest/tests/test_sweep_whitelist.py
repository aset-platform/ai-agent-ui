"""Tests for the sweep field whitelist + validator."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.algo.backtest.sweep_whitelist import (
    SWEEPABLE_FIELDS,
    SweepableField,
    validate_swept_values,
)


def test_whitelist_has_seven_fields():
    """Spec-locked count — change requires updating
    the docs/spec too."""
    assert len(SWEEPABLE_FIELDS) == 7
    assert "cooldown_days" in SWEEPABLE_FIELDS
    assert "stop_loss_pct" in SWEEPABLE_FIELDS
    assert "max_holding_days" in SWEEPABLE_FIELDS
    assert "max_qty" in SWEEPABLE_FIELDS
    assert "min_adtv_inr" in SWEEPABLE_FIELDS
    assert "daily_max_loss_pct" in SWEEPABLE_FIELDS
    assert "max_concentration_pct" in SWEEPABLE_FIELDS


def test_cooldown_field_metadata():
    f = SWEEPABLE_FIELDS["cooldown_days"]
    assert isinstance(f, SweepableField)
    assert f.path == (
        "risk.per_trade.cooldown_after_failed_exit_days"
    )
    assert f.field_type == "int"
    assert f.min_value == Decimal("0")
    assert f.max_value == Decimal("60")


def test_validate_accepts_valid_int_values():
    out = validate_swept_values(
        "cooldown_days", [3, 7, 14, 21],
    )
    assert out == [3, 7, 14, 21]
    assert all(isinstance(v, int) for v in out)


def test_validate_accepts_valid_decimal_values():
    out = validate_swept_values(
        "stop_loss_pct", ["1.0", "2.5", "5.0"],
    )
    assert out == [
        Decimal("1.0"), Decimal("2.5"), Decimal("5.0"),
    ]


def test_validate_rejects_unknown_field():
    with pytest.raises(ValueError, match="unknown field"):
        validate_swept_values("bogus_field", [1, 2, 3])


def test_validate_rejects_single_value():
    with pytest.raises(ValueError, match="at least 2"):
        validate_swept_values("cooldown_days", [7])


def test_validate_rejects_empty():
    with pytest.raises(ValueError, match="at least 2"):
        validate_swept_values("cooldown_days", [])


def test_validate_rejects_duplicates():
    with pytest.raises(ValueError, match="duplicate"):
        validate_swept_values(
            "cooldown_days", [7, 14, 7, 21],
        )


def test_validate_rejects_out_of_range_high():
    with pytest.raises(ValueError, match="out of range"):
        validate_swept_values(
            "cooldown_days", [7, 14, 999],
        )


def test_validate_rejects_out_of_range_low():
    with pytest.raises(ValueError, match="out of range"):
        validate_swept_values(
            "cooldown_days", [-1, 7],
        )


def test_validate_rejects_wrong_type_for_int_field():
    with pytest.raises(ValueError, match="not a valid int"):
        validate_swept_values(
            "cooldown_days", [7, "seven"],
        )


def test_validate_rejects_bool_for_int_field():
    """``True``/``False`` are int subclasses in Python —
    coercer must reject them or the whitelist would happily
    accept ``[True, False]`` as ``[1, 0]``."""
    with pytest.raises(ValueError, match="not a valid int"):
        validate_swept_values(
            "cooldown_days", [True, False],
        )

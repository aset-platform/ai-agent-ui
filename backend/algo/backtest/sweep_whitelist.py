"""Curated whitelist of fields the v1 sweep UI exposes,
with per-field type + range metadata used for validation.

The whitelist is intentionally narrow — seven fields
covering ~90% of practical parameter exploration. Adding
a field is a one-line change here plus the corresponding
unit test.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal


@dataclass(frozen=True)
class SweepableField:
    """One whitelist entry."""

    path: str        # dotted AST path
    label: str       # UI label
    field_type: Literal["int", "decimal"]
    min_value: Decimal
    max_value: Decimal


SWEEPABLE_FIELDS: dict[str, SweepableField] = {
    "cooldown_days": SweepableField(
        path=(
            "risk.per_trade."
            "cooldown_after_failed_exit_days"
        ),
        label="Cooldown (days)",
        field_type="int",
        min_value=Decimal("0"),
        max_value=Decimal("60"),
    ),
    "stop_loss_pct": SweepableField(
        path="risk.per_trade.stop_loss_pct",
        label="Stop loss %",
        field_type="decimal",
        min_value=Decimal("0.5"),
        max_value=Decimal("20.0"),
    ),
    "max_holding_days": SweepableField(
        path="risk.per_trade.max_holding_days",
        label="Max holding days",
        field_type="int",
        min_value=Decimal("1"),
        max_value=Decimal("60"),
    ),
    "max_qty": SweepableField(
        path="risk.per_trade.max_qty",
        label="Max qty per fill",
        field_type="int",
        min_value=Decimal("1"),
        max_value=Decimal("100000"),
    ),
    "min_adtv_inr": SweepableField(
        path="universe.filter.min_adtv_inr",
        label="Min ADTV (₹)",
        field_type="decimal",
        min_value=Decimal("10000000"),
        max_value=Decimal("1000000000"),
    ),
    "daily_max_loss_pct": SweepableField(
        path="risk.daily.max_loss_pct",
        label="Daily max loss %",
        field_type="decimal",
        min_value=Decimal("0.5"),
        max_value=Decimal("10.0"),
    ),
    "max_concentration_pct": SweepableField(
        path="risk.portfolio.max_concentration_pct",
        label="Max position concentration %",
        field_type="decimal",
        min_value=Decimal("5"),
        max_value=Decimal("50"),
    ),
}


def _coerce_one(
    raw: object, field: SweepableField,
) -> int | Decimal:
    if field.field_type == "int":
        if isinstance(raw, bool):
            raise ValueError(
                f"{raw!r} is not a valid int "
                f"(got bool)",
            )
        if isinstance(raw, int):
            v: int | Decimal = raw
        elif isinstance(raw, str):
            try:
                v = int(raw)
            except ValueError as exc:
                raise ValueError(
                    f"{raw!r} is not a valid int",
                ) from exc
        else:
            raise ValueError(
                f"{raw!r} is not a valid int",
            )
        if not (
            field.min_value <= Decimal(v) <= field.max_value
        ):
            raise ValueError(
                f"{v} is out of range "
                f"[{field.min_value}, {field.max_value}] "
                f"for {field.label}",
            )
        return v
    # decimal
    try:
        v = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{raw!r} is not a valid decimal",
        ) from exc
    if not (
        field.min_value <= v <= field.max_value
    ):
        raise ValueError(
            f"{v} is out of range "
            f"[{field.min_value}, {field.max_value}] "
            f"for {field.label}",
        )
    return v


def validate_swept_values(
    field_key: str, values: list[object],
) -> list[int | Decimal]:
    """Validate + coerce. Raises ValueError on bad input.

    Enforces:
      - field_key ∈ SWEEPABLE_FIELDS
      - len(values) ≥ 2
      - each value parses to the field's type
      - each value within [min_value, max_value]
      - all values distinct (no duplicates)
    """
    if field_key not in SWEEPABLE_FIELDS:
        raise ValueError(
            f"unknown field {field_key!r}; "
            f"valid: {sorted(SWEEPABLE_FIELDS)}",
        )
    if len(values) < 2:
        raise ValueError(
            "sweep requires at least 2 values",
        )
    field = SWEEPABLE_FIELDS[field_key]
    coerced = [_coerce_one(v, field) for v in values]
    if len(set(coerced)) != len(coerced):
        raise ValueError(
            f"duplicate values in {coerced}",
        )
    return coerced

"""Position + sector + cash caps. Truncates intended qty to most-
restrictive limit. Per spec §3.4."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PositionCaps:
    per_position_max_pct: Decimal = Decimal("12")
    per_sector_max_pct: Decimal = Decimal("30")
    cash_floor_pct: Decimal = Decimal("5")

    def cap(
        self,
        *,
        intended_qty: int,
        intended_value: Decimal,
        nav: Decimal,
        stock_price: Decimal,
        sector: str | None,
        current_sector_exposure: Decimal,
        current_cash: Decimal | None = None,
    ) -> int:
        if nav <= 0 or stock_price <= 0 or intended_qty <= 0:
            return 0

        # Per-position cap
        max_pos_value = (
            nav * self.per_position_max_pct / Decimal("100")
        )
        if intended_value > max_pos_value:
            intended_value = max_pos_value

        # Per-sector cap
        if sector:
            max_sector_value = (
                nav * self.per_sector_max_pct / Decimal("100")
            )
            sector_room = max_sector_value - current_sector_exposure
            if sector_room < intended_value:
                intended_value = max(Decimal("0"), sector_room)

        # Cash floor (only when we have cash context)
        if current_cash is not None:
            cash_floor_value = (
                nav * self.cash_floor_pct / Decimal("100")
            )
            cash_after = current_cash - intended_value
            if cash_after < cash_floor_value:
                room = current_cash - cash_floor_value
                intended_value = max(Decimal("0"), room)

        return int(intended_value / stock_price)

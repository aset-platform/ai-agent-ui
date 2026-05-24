"""Pydantic types for the algo order budget reservation
system.

Types here are the wire shape between the gate, the repo,
the reconciliation loop, and the HTTP routes. Mirrors
``backend/algo/live/budget_types.py`` shapes in the spec.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReservationState(str, Enum):
    """Lifecycle states of a budget reservation."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"
    PARTIAL_CANCELLED = "PARTIAL_CANCELLED"
    TIMEOUT = "TIMEOUT"


# State sets used by gate + reconciliation queries.
ACTIVE_STATES: frozenset[ReservationState] = frozenset({
    ReservationState.PENDING,
    ReservationState.SUBMITTED,
    ReservationState.PARTIAL,
})

TERMINAL_STATES: frozenset[ReservationState] = frozenset({
    ReservationState.FILLED,
    ReservationState.REJECTED,
    ReservationState.CANCELLED,
    ReservationState.PARTIAL_CANCELLED,
    ReservationState.TIMEOUT,
})


class UserBudget(BaseModel):
    """User-pool allocation row (mutable)."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    allocated_inr: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
    )
    enabled: bool = False
    updated_at: datetime | None = None
    updated_by: UUID | None = None


class BudgetReservation(BaseModel):
    """One row in the append-only reservation event log."""

    model_config = ConfigDict(extra="forbid")

    reservation_id: UUID
    user_id: UUID
    strategy_id: UUID
    state: ReservationState
    ticker: str
    side: str  # BUY | SELL
    qty: int = Field(ge=1)
    reserved_inr: Decimal = Field(ge=Decimal("0"))
    filled_qty: int = Field(default=0, ge=0)
    filled_inr: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"),
    )
    kite_order_id: str | None = None
    transitioned_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_text: str | None = None

"""Round-trip the lifecycle of algo.risk_state via stub session."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.algo.paper.risk_state_repo import RiskStateRepo


class _StubSession:
    def __init__(self) -> None:
        self.rows: dict[tuple, dict] = {}

    async def execute(self, q, params=None):  # noqa: ANN001
        sql = str(q)
        params = dict(params or {})

        class _Res:
            def __init__(self, items):
                self._items = items

            def mappings(self):
                return self

            def first(self):
                return self._items[0] if self._items else None

        if "SELECT user_id, day_date" in sql:
            key = (params["uid"], params["dd"])
            return _Res(
                [self.rows[key]] if key in self.rows else [],
            )
        if (
            "INSERT INTO algo.risk_state" in sql
            and "ON CONFLICT" in sql
        ):
            key = (params["uid"], params["dd"])
            self.rows[key] = {
                "user_id": params["uid"],
                "day_date": params["dd"],
                "daily_realised_pnl_inr": Decimal("0"),
                "daily_unrealised_pnl_inr": Decimal("0"),
                "breaches": [],
            }
            return _Res([])
        if "INSERT INTO algo.risk_state" in sql:
            key = (params["uid"], params["dd"])
            self.rows[key] = {
                "user_id": params["uid"],
                "day_date": params["dd"],
                "daily_realised_pnl_inr": Decimal("0"),
                "daily_unrealised_pnl_inr": Decimal("0"),
                "breaches": [],
            }
            return _Res([])
        if (
            "UPDATE algo.risk_state" in sql
            and "breaches = breaches" in sql
        ):
            import json as _json
            key = (params["uid"], params["dd"])
            row = self.rows.get(key)
            if row:
                row["breaches"].extend(_json.loads(params["b"]))
            return _Res([])
        if "UPDATE algo.risk_state" in sql:
            key = (params["uid"], params["dd"])
            row = self.rows.get(key)
            if row:
                row["daily_realised_pnl_inr"] += params["rd"]
                row["daily_unrealised_pnl_inr"] = params["ud"]
            return _Res([])
        return _Res([])


@pytest.mark.asyncio
async def test_get_or_create_inserts_on_first_call():
    repo = RiskStateRepo()
    session = _StubSession()
    user_id = uuid4()
    state = await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    assert state["daily_realised_pnl_inr"] == Decimal("0")
    again = await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    assert again["user_id"] == user_id


@pytest.mark.asyncio
async def test_update_pnl_increments_realised():
    repo = RiskStateRepo()
    session = _StubSession()
    user_id = uuid4()
    await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    await repo.update_pnl(
        session, user_id=user_id, day_date=date(2026, 4, 1),
        realised_delta=Decimal("500"),
        unrealised_inr=Decimal("200"),
    )
    state = await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    assert state["daily_realised_pnl_inr"] == Decimal("500")
    assert state["daily_unrealised_pnl_inr"] == Decimal("200")


@pytest.mark.asyncio
async def test_append_breach_grows_jsonb_array():
    repo = RiskStateRepo()
    session = _StubSession()
    user_id = uuid4()
    await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    await repo.append_breach(
        session, user_id=user_id, day_date=date(2026, 4, 1),
        breach={"reason": "daily_loss_cap", "qty": 0},
    )
    state = await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    assert len(state["breaches"]) == 1


@pytest.mark.asyncio
async def test_reset_for_day_zeros_all_fields():
    repo = RiskStateRepo()
    session = _StubSession()
    user_id = uuid4()
    await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    await repo.update_pnl(
        session, user_id=user_id, day_date=date(2026, 4, 1),
        realised_delta=Decimal("-500"),
        unrealised_inr=Decimal("-200"),
    )
    await repo.reset_for_day(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    state = await repo.get_or_create(
        session, user_id=user_id, day_date=date(2026, 4, 1),
    )
    assert state["daily_realised_pnl_inr"] == Decimal("0")
    assert state["daily_unrealised_pnl_inr"] == Decimal("0")

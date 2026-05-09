"""Restart-replay rebuilder tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_rebuild_with_no_fills_zeros_pnl():
    from backend.algo.paper.replay_rebuilder import (
        rebuild_risk_state_for_user,
    )
    user_id = uuid4()
    session = MagicMock()
    session.commit = AsyncMock()

    repo = MagicMock()
    repo.reset_for_day = AsyncMock()
    repo.update_pnl = AsyncMock()

    with patch(
        "backend.algo.paper.replay_rebuilder._load_paper_fills_today",
        return_value=[],
    ), patch(
        "backend.algo.paper.replay_rebuilder.RiskStateRepo",
        return_value=repo,
    ):
        out = await rebuild_risk_state_for_user(
            session, user_id=user_id,
        )

    assert out["fills_replayed"] == 0
    assert out["realised_pnl_inr"] == "0"
    repo.reset_for_day.assert_awaited_once()
    repo.update_pnl.assert_awaited_once()


@pytest.mark.asyncio
async def test_rebuild_replays_fills_through_position_tracker():
    from datetime import date as _date
    from decimal import Decimal as _D
    from uuid import uuid4 as _u

    from backend.algo.backtest.types import Fill
    from backend.algo.paper.replay_rebuilder import (
        rebuild_risk_state_for_user,
    )

    user_id = uuid4()
    fills = [
        Fill(
            intent_id=_u(), ticker="X", side="BUY", qty=10,
            fill_price=_D("100"), fill_date=_date(2026, 4, 1),
            fees_inr=_D("0"), fee_rates_version="2026-04-01",
        ),
        Fill(
            intent_id=_u(), ticker="X", side="SELL", qty=10,
            fill_price=_D("110"), fill_date=_date(2026, 4, 1),
            fees_inr=_D("0"), fee_rates_version="2026-04-01",
        ),
    ]

    session = MagicMock()
    session.commit = AsyncMock()
    repo = MagicMock()
    repo.reset_for_day = AsyncMock()
    repo.update_pnl = AsyncMock()

    with patch(
        "backend.algo.paper.replay_rebuilder._load_paper_fills_today",
        return_value=fills,
    ), patch(
        "backend.algo.paper.replay_rebuilder.RiskStateRepo",
        return_value=repo,
    ):
        out = await rebuild_risk_state_for_user(
            session, user_id=user_id,
        )

    assert out["fills_replayed"] == 2
    # (110 - 100) * 10 = 100
    assert out["realised_pnl_inr"] == "100"

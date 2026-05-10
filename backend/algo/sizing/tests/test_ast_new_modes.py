"""AST parses new sizing modes alongside legacy modes."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.algo.strategy.ast import BuyNode


def test_legacy_shares_still_parses() -> None:
    n = BuyNode.model_validate({"type": "buy", "qty": {"shares": 10}})
    assert n.qty.shares == 10


def test_legacy_notional_still_parses() -> None:
    n = BuyNode.model_validate(
        {"type": "buy", "qty": {"notional_inr": 50000.0}}
    )
    assert n.qty.notional_inr == 50000.0


def test_vol_target_parses() -> None:
    n = BuyNode.model_validate(
        {"type": "buy", "qty": {"vol_target_pct": 1.5}}
    )
    assert float(n.qty.vol_target_pct) == 1.5


def test_kelly_parses() -> None:
    n = BuyNode.model_validate(
        {"type": "buy", "qty": {"kelly_fraction": 0.25}}
    )
    assert float(n.qty.kelly_fraction) == 0.25


def test_unknown_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        BuyNode.model_validate(
            {"type": "buy", "qty": {"chocolate": 5}}
        )


def test_vol_target_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        BuyNode.model_validate(
            {"type": "buy", "qty": {"vol_target_pct": 0}}
        )

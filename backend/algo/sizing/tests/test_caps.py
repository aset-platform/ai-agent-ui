"""Position caps tests — per-position + per-sector + cash floor."""
from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from backend.algo.sizing.caps import PositionCaps


def test_per_position_cap_truncates() -> None:
    """Intended 15% of NAV against 12% cap → truncate to 12%."""
    caps = PositionCaps()  # defaults: per_pos=12, per_sector=30, cash=5
    qty = caps.cap(
        intended_qty=15,
        intended_value=Decimal("15000"),
        nav=Decimal("100000"),
        stock_price=Decimal("1000"),
        sector="IT",
        current_sector_exposure=Decimal("0"),
    )
    assert qty == 12


def test_per_sector_cap_truncates() -> None:
    """Sector already at 25%; new position would push to 35%; cap=30%
    → truncate so that final exposure ≤ 30%."""
    caps = PositionCaps()
    qty = caps.cap(
        intended_qty=10,
        intended_value=Decimal("10000"),  # 10%
        nav=Decimal("100000"),
        stock_price=Decimal("1000"),
        sector="IT",
        current_sector_exposure=Decimal("25000"),  # 25%
    )
    # 5000 INR room → 5 shares
    assert qty == 5


def test_cash_floor_truncates() -> None:
    """Cash floor 5%: if intended_value would push cash below 5%,
    truncate. NAV=100k, current cash=10k (10%). New value=8k → cash
    drops to 2k (2%) → cap to 5k (cash 5%)."""
    caps = PositionCaps()
    qty = caps.cap(
        intended_qty=8,
        intended_value=Decimal("8000"),
        nav=Decimal("100000"),
        stock_price=Decimal("1000"),
        sector="IT",
        current_sector_exposure=Decimal("0"),
        current_cash=Decimal("10000"),
    )
    # 10k cash - 5k floor = 5k available → 5 shares
    assert qty == 5


def test_no_cap_below_thresholds() -> None:
    """Intended 8 shares = 8% (within 12% per-pos and 30% sector)."""
    caps = PositionCaps()
    qty = caps.cap(
        intended_qty=8,
        intended_value=Decimal("8000"),
        nav=Decimal("100000"),
        stock_price=Decimal("1000"),
        sector="IT",
        current_sector_exposure=Decimal("0"),
    )
    assert qty == 8


def test_zero_nav_returns_zero() -> None:
    caps = PositionCaps()
    qty = caps.cap(
        intended_qty=5,
        intended_value=Decimal("5000"),
        nav=Decimal("0"),
        stock_price=Decimal("1000"),
        sector="IT",
        current_sector_exposure=Decimal("0"),
    )
    assert qty == 0


# Guard 3 — cash floor zeroes entry visibility
def test_cash_floor_zeroes_entry_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When cash floor leaves room <= 0, a WARNING must be logged
    containing 'cash floor zeroed entry'."""
    caps = PositionCaps()  # cash_floor_pct=5%
    # NAV=100k, current_cash=4900 (below 5% floor of 5000)
    # room = 4900 - 5000 = -100 → zeroed
    with caplog.at_level(
        logging.WARNING, logger="backend.algo.sizing.caps"
    ):
        qty = caps.cap(
            intended_qty=5,
            intended_value=Decimal("4000"),
            nav=Decimal("100000"),
            stock_price=Decimal("1000"),
            sector="IT",
            current_sector_exposure=Decimal("0"),
            current_cash=Decimal("4900"),
        )
    assert qty == 0
    assert any(
        "cash floor zeroed entry" in r.message
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


def test_cash_floor_ample_cash_no_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ample cash → entry proceeds → no warning logged."""
    caps = PositionCaps()
    with caplog.at_level(
        logging.WARNING, logger="backend.algo.sizing.caps"
    ):
        qty = caps.cap(
            intended_qty=5,
            intended_value=Decimal("5000"),
            nav=Decimal("100000"),
            stock_price=Decimal("1000"),
            sector="IT",
            current_sector_exposure=Decimal("0"),
            current_cash=Decimal("50000"),
        )
    assert qty == 5
    assert not any(
        "cash floor zeroed entry" in r.message
        for r in caplog.records
    )

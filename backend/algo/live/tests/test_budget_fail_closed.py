"""broker-cash cap fail-closed tests (Task 0.2).

Tests:
1. Kite error in LIVE mode → returns Decimal("0"), not Decimal("inf").
2. live_balance == 0 is honored (not masked by cash fallback).
3. Positive live_balance is returned correctly.
4. cash fallback when live_balance is absent.
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)

pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason=(
        "Requires pyarrow + Python >=3.10"
        " (run inside Docker backend container)"
    ),
)

_USER_ID = uuid4()
_PATCH_TARGET = (
    "backend.algo.live.budget._kite_margins_for_user"
)
# get_cache is imported lazily inside fetch_kite_available_cash;
# patch at its source module so all lazy imports see the stub.
_CACHE_PATCH = "backend.cache.get_cache"


@pytest.mark.asyncio
async def test_kite_error_returns_zero_not_inf():
    """On ANY Kite exception, fetch_kite_available_cash must
    return Decimal('0') (fail-closed), never Decimal('inf').
    """
    from backend.algo.live.budget import fetch_kite_available_cash

    with patch(
        _PATCH_TARGET,
        new=AsyncMock(side_effect=RuntimeError("token expired")),
    ), patch(
        _CACHE_PATCH,
        return_value=None,
    ):
        result = await fetch_kite_available_cash(_USER_ID)

    assert result == Decimal("0"), (
        f"Expected Decimal('0') on error but got {result!r}"
    )
    assert result != Decimal("inf"), (
        "fail-open Decimal('inf') must never be returned in live mode"
    )


@pytest.mark.asyncio
async def test_zero_live_balance_is_honored_not_masked():
    """live_balance == 0 must return Decimal('0'), not fall
    through to avail.get('cash', 0) via the old 'or' pattern.
    """
    from backend.algo.live.budget import fetch_kite_available_cash

    margins_payload = {
        "available": {
            "live_balance": 0,   # legitimate zero
            "cash": 50000,       # must NOT be used
        }
    }
    with patch(
        _PATCH_TARGET,
        new=AsyncMock(return_value=margins_payload),
    ), patch(
        _CACHE_PATCH,
        return_value=None,
    ):
        result = await fetch_kite_available_cash(_USER_ID)

    assert result == Decimal("0"), (
        f"Expected Decimal('0') for zero live_balance but got {result!r};"
        " the old 'or' pattern incorrectly fell back to cash=50000"
    )


@pytest.mark.asyncio
async def test_positive_live_balance_returned():
    """Normal case: positive live_balance is returned as-is."""
    from backend.algo.live.budget import fetch_kite_available_cash

    margins_payload = {
        "available": {
            "live_balance": 12345.67,
            "cash": 99999,
        }
    }
    with patch(
        _PATCH_TARGET,
        new=AsyncMock(return_value=margins_payload),
    ), patch(
        _CACHE_PATCH,
        return_value=None,
    ):
        result = await fetch_kite_available_cash(_USER_ID)

    assert result == Decimal("12345.67"), (
        f"Expected Decimal('12345.67') but got {result!r}"
    )


@pytest.mark.asyncio
async def test_cash_fallback_when_live_balance_absent():
    """When live_balance key is absent, fall back to cash."""
    from backend.algo.live.budget import fetch_kite_available_cash

    margins_payload = {
        "available": {
            "cash": 8888.0,
        }
    }
    with patch(
        _PATCH_TARGET,
        new=AsyncMock(return_value=margins_payload),
    ), patch(
        _CACHE_PATCH,
        return_value=None,
    ):
        result = await fetch_kite_available_cash(_USER_ID)

    assert result == Decimal("8888.0"), (
        f"Expected Decimal('8888.0') from cash fallback but got {result!r}"
    )

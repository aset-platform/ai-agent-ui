"""HTTP-level tests for /v1/algo/sweep/* endpoints.

Tests the route handlers via the lifted ``_impl``
functions so no full HTTP harness is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest


def test_sweep_fields_returns_whitelist():
    from backend.algo.routes.sweep import (
        _sweep_fields_impl,
    )
    out = _sweep_fields_impl()
    assert "fields" in out
    keys = {f["key"] for f in out["fields"]}
    assert "cooldown_days" in keys
    assert "stop_loss_pct" in keys
    for f in out["fields"]:
        assert "key" in f
        assert "label" in f
        assert "field_type" in f
        assert "min_value" in f
        assert "max_value" in f


@pytest.mark.asyncio
async def test_sweep_start_validates_whitelist_field():
    """Unknown field → 400."""
    from fastapi import HTTPException
    from backend.algo.backtest.sweep_types import (
        SweepConfig,
    )
    from backend.algo.routes.sweep import (
        _sweep_start_impl,
    )

    body = SweepConfig.model_validate({
        "base_strategy_id": str(uuid4()),
        "period_start": "2025-11-23",
        "period_end": "2026-05-23",
        "swept_field": "bogus_field",
        "swept_values": [1, 2, 3],
    })

    user_id = uuid4()
    with pytest.raises(HTTPException) as exc:
        await _sweep_start_impl(
            body=body, user_id=user_id,
            background_tasks=MagicMock(),
        )
    assert exc.value.status_code == 400
    assert "unknown field" in str(
        exc.value.detail,
    ).lower()


@pytest.mark.asyncio
async def test_sweep_start_rejects_single_value():
    from fastapi import HTTPException
    from backend.algo.backtest.sweep_types import (
        SweepConfig,
    )
    from backend.algo.routes.sweep import (
        _sweep_start_impl,
    )

    body = SweepConfig.model_validate({
        "base_strategy_id": str(uuid4()),
        "period_start": "2025-11-23",
        "period_end": "2026-05-23",
        "swept_field": "cooldown_days",
        "swept_values": [7],
    })
    with pytest.raises(HTTPException) as exc:
        await _sweep_start_impl(
            body=body, user_id=uuid4(),
            background_tasks=MagicMock(),
        )
    assert exc.value.status_code == 400
    assert "at least 2" in str(exc.value.detail).lower()

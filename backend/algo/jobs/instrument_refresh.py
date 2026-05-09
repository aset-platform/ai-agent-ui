"""Daily 07:00 IST instrument-master refresh.

Wraps backend.algo.instruments.loader.run_instruments_refresh
behind the @register_job dispatch so the scheduler can fire it.
"""
from __future__ import annotations

from typing import Any

from backend.algo.instruments.loader import run_instruments_refresh


async def run(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return await run_instruments_refresh(payload)

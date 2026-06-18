"""PR4 — guard algo.events maintenance enrollment.

Incident 2026-06-18: algo.events bloated to 8.2 GB partly because its
maintenance/retention coverage was never exercised. algo.events MUST
stay enrolled in BOTH maintenance table lists (CLAUDE.md §4.3 #21) so a
future refactor can't silently drop it and let the bloat return.
"""
from __future__ import annotations

import importlib.util
import sys

import pytest

_RUNTIME_AVAILABLE = (
    importlib.util.find_spec("pyarrow") is not None
    and sys.version_info >= (3, 10)
)
pytestmark = pytest.mark.skipif(
    not _RUNTIME_AVAILABLE,
    reason="Requires pyarrow + Python >=3.10 (Docker backend container)",
)


def test_algo_events_in_all_tables():
    from backend.maintenance.iceberg_maintenance import ALL_TABLES

    assert "algo.events" in ALL_TABLES


def test_algo_events_in_hot_iceberg_tables():
    from backend.jobs.executor import _HOT_ICEBERG_TABLES

    assert "algo.events" in _HOT_ICEBERG_TABLES

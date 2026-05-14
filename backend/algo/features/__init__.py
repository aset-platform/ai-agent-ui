"""Centralized feature engine — Phase-1 entrypoint.

Public API:

- ``compute_intraday_features(bars)`` — per-ticker engine.
- ``compute_intraday_features_for_universe(bars_by_ticker)``
  — universe fan-out.
- ``FEATURE_SET_VERSION`` — string stamped onto persisted rows
  by FE-3 / FE-5.

Internal helpers live in ``backend.algo.features.primitives``
and ``backend.algo.features.engine``; import those directly
for fine-grained access (e.g. tests pinning a specific
primitive against a numpy reference).
"""

from __future__ import annotations

from backend.algo.features.engine import (
    compute_intraday_features,
    compute_intraday_features_for_universe,
)
from backend.algo.features.version import FEATURE_SET_VERSION

__all__ = [
    "FEATURE_SET_VERSION",
    "compute_intraday_features",
    "compute_intraday_features_for_universe",
]

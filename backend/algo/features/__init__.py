"""Centralized feature engine — Phase-1 entrypoint.

Public API:

- ``compute_intraday_features(bars)`` — per-ticker engine.
- ``compute_intraday_features_for_universe(bars_by_ticker)``
  — universe fan-out.
- ``FEATURE_SET_VERSION`` — string stamped onto persisted rows
  by FE-3 / FE-5.
- ``DEFAULT_INTRADAY_SMA_WINDOWS`` / ``DEFAULT_INTRADAY_WARMUP_DAYS``
  / ``NO_CROSS_SENTINEL`` — intraday compute constants (moved
  from ``backtest/indicators.py`` in FE-4).
- ``load_intraday_features_window`` — partition-chunk Redis +
  Iceberg loader; the canonical entrypoint for the intraday
  backtest runner (FE-4).
- ``FeaturePanelMissingError`` — raised when a requested
  ``(ticker, year_month, interval_sec)`` chunk is still empty
  after on-demand backfill.

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
from backend.algo.features.loader import (
    FeaturePanelMissingError,
    load_intraday_features_window,
)
from backend.algo.features.version import (
    DEFAULT_INTRADAY_SMA_WINDOWS,
    DEFAULT_INTRADAY_WARMUP_DAYS,
    FEATURE_SET_VERSION,
    NO_CROSS_SENTINEL,
)

__all__ = [
    "DEFAULT_INTRADAY_SMA_WINDOWS",
    "DEFAULT_INTRADAY_WARMUP_DAYS",
    "FEATURE_SET_VERSION",
    "FeaturePanelMissingError",
    "NO_CROSS_SENTINEL",
    "compute_intraday_features",
    "compute_intraday_features_for_universe",
    "load_intraday_features_window",
]

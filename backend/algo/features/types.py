"""Type aliases shared across the feature engine.

Kept ultra-light: feature values are either ``Decimal`` (every
numeric feature) or ``str`` (only ``time_of_day_bucket`` in
Phase 1).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypeAlias

FeatureValue: TypeAlias = Decimal | str
FeatureMap: TypeAlias = dict[str, FeatureValue]
# Per-(bar_open_ts_ns) feature map for a single ticker.
TickerFeaturePanel: TypeAlias = dict[int, FeatureMap]
# Per-(ticker) panel for a universe scan.
UniverseFeaturePanel: TypeAlias = dict[str, TickerFeaturePanel]

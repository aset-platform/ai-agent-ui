"""Feature-set version stamp + intraday compute constants.

Every row written to ``stocks.intraday_features`` carries a
``feature_set_version`` so strategies can pin the semantic
contract they require. Bumping this string is a deliberate act:
any formula change to a Phase-1 feature MUST bump the minor
version and trigger a one-time backfill via FE-3.

Versioning policy (semver-lite):
- ``v1.0`` — Phase-1 launch (FE-2): 26 intraday features as
  documented in the spec §4. The two RS-vs-* features are
  deferred to Phase 2 (FE-8) because they depend on the index
  / sector intraday bar tables built in FE-6 / FE-7.
- ``v1.x`` — additive feature; old rows still readable.
- ``v2.0`` — breaking semantic change to an existing feature
  (e.g. switching EMA seeding from SMA to plain decay).

Constants exported here (moved out of ``backtest/indicators.py``
in FE-4 so the centralized feature engine is fully
self-contained — see ASETPLTFRM-402 spec §7.3 / §13):

- :data:`DEFAULT_INTRADAY_SMA_WINDOWS` — SMA windows emitted by
  the centralized intraday engine.
- :data:`DEFAULT_INTRADAY_WARMUP_DAYS` — calendar-day warmup
  used by the intraday backtest loader so SMA(200) is
  well-formed at ``period_start``.
- :data:`NO_CROSS_SENTINEL` — value emitted for
  ``golden_cross_*`` features before the first cross fires, so
  a strategy condition like ``<= 10`` always fails until a
  genuine cross is detected.
"""

from __future__ import annotations

from decimal import Decimal

FEATURE_SET_VERSION: str = "v1.0"

# ASETPLTFRM-400 slice 4b — calendar-day warmup for the intraday
# indicator path. SMA(200) at 15m = 200 × 15min = ~8 NSE trading
# days; weekends bump that to ~12 calendar days. 20d gives
# comfortable buffer + works at the 1m / 5m cadences as well
# (each shorter cadence needs FEWER calendar days for the same
# bar count).
DEFAULT_INTRADAY_WARMUP_DAYS: int = 20

# SMA windows for intraday strategies, per slice-4b operator
# spec: SMA 20 / 50 / 100 / 200 + RSI 14 + VWAP all available
# at the bar-level. SMA 100 is added vs the daily default
# because intraday strategies typically use shorter MAs and
# the SMA(50) → SMA(100) crossover is a common signal.
DEFAULT_INTRADAY_SMA_WINDOWS: tuple[int, ...] = (20, 50, 100, 200)

# Sentinel for "no crossover seen yet" — large enough that any
# ``<= N`` comparison in a strategy condition fails. Decimal so
# it sorts / compares correctly against Decimal-valued features.
NO_CROSS_SENTINEL: Decimal = Decimal("999")

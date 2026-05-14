"""Feature-set version stamp.

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
"""

from __future__ import annotations

FEATURE_SET_VERSION: str = "v1.0"

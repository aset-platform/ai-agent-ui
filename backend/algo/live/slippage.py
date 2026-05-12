"""Liquidity-bucket → slippage-bps lookup (PR #2 order-safety).

Replaces the single hardcoded 30 bps slippage constant in
``backend/algo/live/runtime.py`` with a composite-signal bucket
classifier and a per-bucket bps cap. Defaults match the spec
matrix (`docs/superpowers/specs/2026-05-12-algo-order-safety-
hardening-design.md` §3.2 + §7 Q2):

    largecap  → 20 bps
    midcap    → 50 bps
    smallcap  → 100 bps
    unknown   → 30 bps  (preserves today's behaviour)

Each cap is env-overrideable; parse failures fall back to the
default with a warning (mirrors ``_read_max_ltp_age_s`` in
``kite_client.py``).

Classification (``classify``) takes a market-cap signal (crore) and
a 20-day ADTV signal (crore/day) and returns the MORE CONSERVATIVE
of the two — i.e. whichever yields the higher slippage cap. This
catches a re-rated smallcap (mcap just crossed 20k cr but ADTV still
thin) as smallcap, and a corporate-action-driven volume drought on
a largecap as midcap/smallcap.

The "top-100 by mcap" largecap refinement (spec §7 Q2) lives in
``snapshot_job._derive_liquidity_bucket`` — this standalone helper
uses the mcap threshold alone, since rank-among-peers is not
expressible from a single (mcap, adtv) pair.
"""
from __future__ import annotations

import logging
import math
import os

_logger = logging.getLogger(__name__)


# Bucket → default bps cap. Order matters for "more conservative":
# higher bps = more conservative. unknown is the legacy default.
_DEFAULTS_BPS: dict[str, int] = {
    "largecap": 20,
    "midcap": 50,
    "smallcap": 100,
    "unknown": 30,
}

# Env var per bucket. Read on every call (cheap; matches the
# kite_client.py pattern) so tests can monkeypatch + so ops can
# tune without a restart.
_ENV_VAR_BY_BUCKET: dict[str, str] = {
    "largecap": "ALGO_SLIPPAGE_LARGECAP_BPS",
    "midcap": "ALGO_SLIPPAGE_MIDCAP_BPS",
    "smallcap": "ALGO_SLIPPAGE_SMALLCAP_BPS",
    "unknown": "ALGO_SLIPPAGE_UNKNOWN_BPS",
}

_VALID_BUCKETS: frozenset[str] = frozenset(_DEFAULTS_BPS.keys())


# Thresholds in CRORE (mcap) and CRORE/DAY (adtv). Sourced from
# spec §7 Q2 bucket criteria.
_MCAP_LARGE_MIN_CR = 20_000.0
_MCAP_MID_MIN_CR = 5_000.0
_ADTV_LARGE_MIN_CR = 50.0
_ADTV_MID_MIN_CR = 20.0


def _read_bps_for(bucket: str) -> int:
    """Resolve the bps cap for ``bucket`` from env, with default.

    Defensive: any non-int env value falls back to the default
    with a warning. Mirrors ``_read_max_ltp_age_s`` in
    ``backend/algo/broker/kite_client.py``.
    """
    default = _DEFAULTS_BPS[bucket]
    env_var = _ENV_VAR_BY_BUCKET[bucket]
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not an int — using default %d",
            env_var, raw, default,
        )
        return default


def bps_for(bucket: str | None) -> int:
    """Slippage cap (bps) for ``bucket``. None / unknown → 30 bps.

    Case-insensitive; unrecognised strings collapse to "unknown"
    (the legacy 30 bps behaviour).
    """
    if bucket is None:
        return _read_bps_for("unknown")
    key = bucket.strip().lower()
    if key not in _VALID_BUCKETS:
        return _read_bps_for("unknown")
    return _read_bps_for(key)


def _bucket_by_mcap(mcap_cr: float | None) -> str | None:
    """Bucket from market cap alone. Returns None if missing.

    Note: the "top-100 by mcap" largecap refinement lives in
    ``snapshot_job._derive_liquidity_bucket`` — this single-input
    helper uses the mcap threshold alone. A ticker with
    mcap >= 20k cr that is NOT in the top-100 is still classified
    here as "largecap"; the snapshot-job side downgrades it to
    "midcap" using global rank context. Document explicitly so the
    next reader doesn't think this is a bug.
    """
    if mcap_cr is None or _is_nan(mcap_cr) or mcap_cr <= 0:
        return None
    if mcap_cr >= _MCAP_LARGE_MIN_CR:
        return "largecap"
    if mcap_cr >= _MCAP_MID_MIN_CR:
        return "midcap"
    return "smallcap"


def _bucket_by_adtv(adtv_cr: float | None) -> str | None:
    """Bucket from rolling ADTV alone (currently 60d, see
    ``classify`` docstring). Returns None if missing."""
    if adtv_cr is None or _is_nan(adtv_cr) or adtv_cr <= 0:
        return None
    if adtv_cr >= _ADTV_LARGE_MIN_CR:
        return "largecap"
    if adtv_cr >= _ADTV_MID_MIN_CR:
        return "midcap"
    return "smallcap"


def _is_nan(v: float | None) -> bool:
    """NaN-safe check (CLAUDE.md §6.1 nan-handling)."""
    if v is None:
        return True
    try:
        return math.isnan(v)
    except (TypeError, ValueError):
        return False


# Ordering for "more conservative wins". Higher bps = more
# conservative. Index in this tuple = rank; we pick the max.
_CONSERVATISM_ORDER: tuple[str, ...] = (
    "largecap", "midcap", "smallcap",
)


def _more_conservative(a: str, b: str) -> str:
    """Pick the bucket with the higher bps cap."""
    rank_a = _CONSERVATISM_ORDER.index(a)
    rank_b = _CONSERVATISM_ORDER.index(b)
    return a if rank_a >= rank_b else b


def classify(
    mcap_cr: float | None,
    adtv_cr: float | None,
) -> str:
    """Composite-signal liquidity bucket — more conservative wins.

    Rules (spec §7 Q2):
    - BOTH missing → "unknown" (30 bps; preserves legacy default).
    - EITHER missing → "smallcap" (100 bps; conservative).
    - BOTH present, agree → that bucket.
    - BOTH present, disagree → whichever has higher slippage cap.

    ``mcap_cr`` is market cap in crore (₹). ``adtv_cr`` is rolling
    ADTV in crore (₹) per trading day. The snapshot job currently
    feeds the 60-trading-day rolling ADTV column
    (``adtv_inr_60d``) since that's what exists on
    ``universe_snapshot`` today — spec §7 Q2 suggested 20d, but
    60d is what the cohort gate already computes and the
    20/50 crore thresholds are coarse enough that the smoothing
    window doesn't move most tickers. Tracked as a small
    follow-up if a true 20d window becomes worth the extra column.

    Note: this standalone helper does NOT apply the "top-100 by
    mcap" largecap refinement — see ``_bucket_by_mcap`` docstring
    for the why. The snapshot-job side adds top-100 context.
    """
    mcap_bucket = _bucket_by_mcap(mcap_cr)
    adtv_bucket = _bucket_by_adtv(adtv_cr)
    if mcap_bucket is None and adtv_bucket is None:
        return "unknown"
    if mcap_bucket is None or adtv_bucket is None:
        return "smallcap"
    return _more_conservative(mcap_bucket, adtv_bucket)

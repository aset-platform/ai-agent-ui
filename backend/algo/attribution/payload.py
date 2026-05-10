"""Signal-event payload extension helpers (REGIME-6).

Both PaperRuntime and LiveRuntime stamp every
``signal_generated`` event with attribution context: a snapshot
of the features dict at decision time, the active regime label,
the stress posterior, and the strategy's factor exposures.

All values are coerced to JSON-friendly primitives (float / str)
because ``algo.events.payload_json`` is a string column, and the
``features`` dict typically holds ``Decimal`` from the indicator
pipeline.

Backward compatibility: every key is additive. Pre-REGIME-6
events lack these keys; readers MUST use ``dict.get()``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

# Factor keys persisted on the strategy's exposure snapshot.
# Sourced from REGIME-2a's daily_factors table; subset is fine —
# missing keys silently drop out.
_FACTOR_KEYS = (
    "mom_12_1", "f_score", "realized_vol_60d",
    "adx_14", "rs_vs_nifty_3m",
)


def _to_float(v: Any) -> float | None:
    """Coerce Decimal / int / float / numeric-str to ``float``.
    Returns ``None`` for None or coercion failure (do NOT raise —
    payload extension must never break the signal emit path)."""
    if v is None:
        return None
    if isinstance(v, bool):
        # bool is a subclass of int; we don't want True/False
        # silently turning into 1.0/0.0 in the factor exposures.
        return float(v)
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except (ValueError, OverflowError):
            return None
    if isinstance(v, Decimal):
        try:
            return float(v)
        except (ValueError, OverflowError):
            return None
    try:
        return float(v)  # last-resort: numeric string
    except (TypeError, ValueError):
        return None


def _snapshot_for_payload(features: dict) -> dict:
    """Project the runtime ``features`` dict into a JSON-safe
    snapshot. Decimals → float; tuples / objects best-effort
    stringify; values that fail coercion are dropped."""
    out: dict[str, Any] = {}
    for k, v in features.items():
        if v is None:
            out[k] = None
            continue
        if isinstance(v, Decimal):
            f = _to_float(v)
            if f is not None:
                out[k] = f
            continue
        if isinstance(v, (int, float, str, bool)):
            out[k] = v
            continue
        # Anything else — best-effort stringify to keep the
        # snapshot informative without breaking JSON serialisation.
        try:
            out[k] = str(v)
        except Exception:  # noqa: BLE001 — defensive; never raise
            continue
    return out


def attribution_payload_extension(features: dict) -> dict:
    """Return the additive REGIME-6 payload keys.

    Always returns a dict; missing fields collapse to ``None``
    or empty subdicts. Callers spread it into the existing
    ``signal_generated`` payload.
    """
    if not isinstance(features, dict):
        return {
            "feature_snapshot": {},
            "regime_label": None,
            "stress_prob": None,
            "factor_exposures": {},
        }

    factor_exposures: dict[str, float] = {}
    for k in _FACTOR_KEYS:
        if k in features and features[k] is not None:
            f = _to_float(features[k])
            if f is not None:
                factor_exposures[k] = f

    return {
        "feature_snapshot": _snapshot_for_payload(features),
        "regime_label": features.get("regime_label"),
        "stress_prob": _to_float(features.get("stress_prob")),
        "factor_exposures": factor_exposures,
    }

"""REGIME-6 — payload-extension backward compat + coercion tests."""
from __future__ import annotations

import json
from decimal import Decimal

from backend.algo.attribution.payload import (
    attribution_payload_extension,
)


def test_extension_returns_attribution_keys() -> None:
    features = {
        "rsi_14": Decimal("62.5"),
        "regime_label": "BULL",
        "stress_prob": Decimal("0.18"),
        "mom_12_1": Decimal("0.82"),
        "f_score": 7,
        "realized_vol_60d": Decimal("0.21"),
        # not in factor whitelist — should be in snapshot
        # but NOT in factor_exposures
        "extra_feature": Decimal("1.23"),
    }
    out = attribution_payload_extension(features)
    assert out["regime_label"] == "BULL"
    assert out["stress_prob"] == 0.18
    assert out["factor_exposures"]["mom_12_1"] == 0.82
    assert out["factor_exposures"]["f_score"] == 7.0
    assert "extra_feature" not in out["factor_exposures"]
    # snapshot has all keys, decimals coerced
    assert out["feature_snapshot"]["rsi_14"] == 62.5
    assert out["feature_snapshot"]["extra_feature"] == 1.23
    # JSON-serialisable end-to-end
    json.dumps(out)


def test_extension_handles_missing_attribution_keys() -> None:
    """A pre-REGIME-6 features dict still produces a usable
    extension with None defaults."""
    features = {"rsi_14": Decimal("55.0")}
    out = attribution_payload_extension(features)
    assert out["regime_label"] is None
    assert out["stress_prob"] is None
    assert out["factor_exposures"] == {}
    assert out["feature_snapshot"]["rsi_14"] == 55.0


def test_extension_tolerates_non_dict() -> None:
    """Defensive: if features is somehow None, don't crash."""
    out = attribution_payload_extension(None)  # type: ignore[arg-type]
    assert out["feature_snapshot"] == {}
    assert out["regime_label"] is None
    assert out["factor_exposures"] == {}


def test_legacy_event_payload_parses_without_keys() -> None:
    """Pre-REGIME-6 signal_generated payload (no attribution
    keys) MUST still parse cleanly via dict.get() — the entire
    UI relies on this."""
    legacy = {"ticker": "RELIANCE.NS", "side": "BUY", "qty": 10}
    parsed = json.loads(json.dumps(legacy))
    assert parsed.get("regime_label") is None
    assert parsed.get("stress_prob") is None
    assert dict(parsed.get("factor_exposures") or {}) == {}


def test_factor_exposures_drop_none_values() -> None:
    features = {
        "mom_12_1": None,
        "f_score": Decimal("5"),
        "realized_vol_60d": None,
    }
    out = attribution_payload_extension(features)
    assert out["factor_exposures"] == {"f_score": 5.0}

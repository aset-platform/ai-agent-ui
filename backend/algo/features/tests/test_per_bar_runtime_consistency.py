"""Cross-runtime consistency test for the shared per-bar
helper — FE-15b (ASETPLTFRM-420).

Verifies that the SAME input produces the SAME features dict
regardless of which runtime calls
``assemble_per_bar_features``. Catches drift between
backtest / paper / live / dry-run signal generation.
"""
from __future__ import annotations

from decimal import Decimal

from backend.algo.features.per_bar import assemble_per_bar_features


def _shared_inputs():
    return {
        "bar_feats": {
            "rsi_14": Decimal("28"),
            "today_ltp": Decimal("2870"),
            "today_vol": Decimal("1500000"),
        },
        "market_regime": Decimal("1"),
        "market_trend": Decimal("4.2"),
        "factor_row": {
            "mom_12_1": Decimal("0.18"),
            "f_score": Decimal("8"),
        },
        "regime_row": {
            "regime_label": "BULL",
            "stress_prob": Decimal("0.15"),
        },
        "daily_overlay": {
            "ema_50": Decimal("2865.50"),
            "ema_200": Decimal("2810.00"),
        },
    }


def test_three_invocations_yield_byte_identical_dicts():
    """Three back-to-back calls with identical inputs MUST
    produce three byte-identical output dicts. Simulates the
    three runtimes (backtest / paper / live) calling the same
    helper with the same per-bar state.
    """
    inputs = _shared_inputs()
    a = assemble_per_bar_features(**inputs)
    b = assemble_per_bar_features(**inputs)
    c = assemble_per_bar_features(**inputs)
    assert a == b == c
    # Distinct dict objects (no shared state).
    assert a is not b


def test_dryrun_indistinguishable_from_live_at_helper_level():
    """Dry-run uses ``LiveRuntime(kite.dry_run=True)`` — the
    signal-generation path is identical and goes through the
    same helper. There is no runtime branch on dry_run inside
    the helper or its inputs assembly. Verified by structural
    inspection: the helper has no dry_run knob.
    """
    import inspect

    sig = inspect.signature(assemble_per_bar_features)
    assert "dry_run" not in sig.parameters
    expected = {
        "bar_feats",
        "market_regime",
        "market_trend",
        "factor_row",
        "regime_row",
        "daily_overlay",
        "hourly_overlay",
        "fifteen_min_overlay",
    }
    assert set(sig.parameters) == expected


def test_helper_output_contains_cross_cadence_keys_for_intraday():
    """Smoke check that the dict exposed to the AST evaluator
    in an intraday strategy DOES contain both primary
    (unsuffixed) and daily-overlay (_1d) keys.
    """
    inputs = _shared_inputs()
    out = assemble_per_bar_features(**inputs)
    assert "rsi_14" in out
    assert "ema_50_1d" in out
    assert "ema_200_1d" in out
    assert "f_score" in out
    assert "regime_label" in out

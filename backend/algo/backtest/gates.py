"""5-gate acceptance check for walk-forward results.

Pure logic: takes pre-computed aggregate metrics + per-regime
breakdown + DSR + PBO and returns a dict of bool flags. Used by:
  * the walk-forward aggregator (to persist gates_passed)
  * the GET /v1/algo/walkforward/runs/{id}/gates endpoint
  * the live-mode toggle (when feature flag enabled)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from backend.algo.backtest.metrics import PerRegimeMetrics


@dataclass
class GateThresholds:
    """Default thresholds per spec §6.1.

    Override via WalkForwardConfig.require_* fields when calling
    from the orchestrator.
    """

    max_dd_pct: float = 25.0
    recovery_months_max: int = 18
    require_per_regime_non_negative: bool = True
    dsr_min: float = 0.95
    pbo_max: float = 0.30


def evaluate_5_gates(
    aggregate_max_dd_pct: float,
    recovery_months: int,
    per_regime: list[PerRegimeMetrics],
    dsr: float,
    pbo: float | None,
    thresholds: GateThresholds | None = None,
) -> dict[str, bool]:
    """Evaluate all 5 gates and return a dict of bool flags.

    Gate semantics:
      max_dd_ok           - aggregate max DD ≤ threshold
      recovery_ok         - recovery_months ≤ threshold
      per_regime_non_neg  - every regime has cum_return ≥ 0
      dsr_ok              - DSR ≥ threshold
      pbo_ok              - PBO ≤ threshold (or None / NaN)

    PBO defaults to True (gate passes) when None or NaN — that is
    the V2-2 single-strategy case where N=1 and CSCV is undefined.
    """
    th = thresholds or GateThresholds()

    pbo_ok = True
    if pbo is not None and not math.isnan(pbo):
        pbo_ok = pbo <= th.pbo_max

    if th.require_per_regime_non_negative:
        per_regime_ok = (
            all(r.cum_return_pct >= 0 for r in per_regime)
            if per_regime else True
        )
    else:
        per_regime_ok = True

    return {
        "max_dd_ok": aggregate_max_dd_pct <= th.max_dd_pct,
        "recovery_ok": recovery_months <= th.recovery_months_max,
        "per_regime_non_neg": per_regime_ok,
        "dsr_ok": dsr >= th.dsr_min,
        "pbo_ok": pbo_ok,
    }


def all_gates_pass(gates: dict[str, bool]) -> bool:
    """True iff every gate flag is True. Empty dict → False."""
    return bool(gates) and all(gates.values())

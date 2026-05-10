"""Brinson allocation/selection/interaction decomposition.

Per spec §3.6, sector-level model:

    allocation[s] = (w_p[s] - w_b[s]) * (r_b[s] - r_b_total)
    selection[s]  = w_b[s] * (r_p[s] - r_b[s])
    interaction[s]= (w_p[s] - w_b[s]) * (r_p[s] - r_b[s])

Returns one ``BrinsonComponents`` per sector; the sum of every
component across every sector equals the active return
``R_p - R_b``.

Edge cases:
- A sector present in only one side is treated as weight + return
  zero on the missing side. This keeps the algebraic identity.
- Empty inputs return ``{}`` — the caller can detect "no data".
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BrinsonComponents:
    """Per-sector decomposition row."""

    sector: str
    allocation: float
    selection: float
    interaction: float

    @property
    def total(self) -> float:
        """Sum of the three Brinson effects for this sector."""
        return self.allocation + self.selection + self.interaction


def compute_brinson(
    portfolio_weights: dict[str, float],
    benchmark_weights: dict[str, float],
    portfolio_returns: dict[str, float],
    benchmark_returns: dict[str, float],
) -> dict[str, BrinsonComponents]:
    """Return per-sector Brinson decomposition.

    All four input dicts are keyed by sector. A missing sector on
    one side is treated as 0 (weight + return). Sectors are
    sorted lexicographically in the output for determinism.
    """
    sectors = sorted(
        set(portfolio_weights) | set(benchmark_weights),
    )
    if not sectors:
        return {}
    rb_total = sum(
        benchmark_weights.get(s, 0.0)
        * benchmark_returns.get(s, 0.0)
        for s in sectors
    )
    out: dict[str, BrinsonComponents] = {}
    for s in sectors:
        wp = float(portfolio_weights.get(s, 0.0))
        wb = float(benchmark_weights.get(s, 0.0))
        rp = float(portfolio_returns.get(s, 0.0))
        rb = float(benchmark_returns.get(s, 0.0))
        out[s] = BrinsonComponents(
            sector=s,
            allocation=(wp - wb) * (rb - rb_total),
            selection=wb * (rp - rb),
            interaction=(wp - wb) * (rp - rb),
        )
    return out

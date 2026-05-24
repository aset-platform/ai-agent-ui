"""Cross-variant PBO aggregation primitives.

Three pure functions:
  1. ``variant_equity_curve`` — chains per-window
     equity curves into one continuous variant curve.
  2. ``build_returns_matrix`` — aligns N variants on
     common dates and returns a (T, N) returns matrix.
  3. ``compute_sweep_pbo`` — calls the existing CSCV
     PBO implementation on a returns matrix, returning
     a Decimal or None.

All three are pure; no I/O, no DB. The orchestrator
fetches walk-forward summaries from PG and pipes them
through these helpers.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import numpy as np

from backend.algo.backtest.metrics import (
    probability_of_backtest_overfitting,
)
from backend.algo.backtest.types import BacktestSummary

_logger = logging.getLogger(__name__)


def variant_equity_curve(
    window_summaries: list[BacktestSummary],
    initial_capital: Decimal,
) -> list[tuple[date, Decimal]]:
    """Chain per-window returns into one continuous
    variant equity curve.

    Each walk-forward window's curve starts fresh at
    ``initial_capital_inr`` (the backtest engine resets
    between folds). We compute each window's daily
    MULTIPLIER curve and apply it to a running capital
    that starts at ``initial_capital`` and compounds
    across windows.
    """
    points: list[tuple[date, Decimal]] = []
    running = initial_capital
    for w in sorted(
        window_summaries, key=lambda s: s.period_start,
    ):
        if not w.equity_curve:
            continue
        start_eq = Decimal(
            str(w.equity_curve[0].equity_inr),
        )
        if start_eq == 0:
            _logger.warning(
                "variant_equity_curve: window starts "
                "at zero equity; skipping",
            )
            continue
        for pt in w.equity_curve:
            ratio = (
                Decimal(str(pt.equity_inr)) / start_eq
            )
            points.append((pt.bar_date, running * ratio))
        running = points[-1][1]
    return points


def build_returns_matrix(
    variants_curves: list[
        list[tuple[date, Decimal]]
    ],
) -> tuple[np.ndarray, list[date]]:
    """Align N variants on common dates; return
    ``(R, common_dates)`` where R has shape (T, N).

    ``T = len(common_dates) - 1`` because returns are
    pairwise differences. Returns ``(zeros((0,0)), [])``
    when fewer than 2 common dates.
    """
    if not variants_curves:
        return (np.zeros((0, 0)), [])
    date_sets = [
        {d for d, _ in curve}
        for curve in variants_curves
    ]
    common = sorted(set.intersection(*date_sets))
    if len(common) < 2:
        return (np.zeros((0, 0)), [])

    cols = []
    for curve in variants_curves:
        d2v = {d: float(v) for d, v in curve}
        seq = np.array(
            [d2v[d] for d in common], dtype=float,
        )
        # Pairwise returns; guard div-by-zero
        with np.errstate(
            divide="ignore", invalid="ignore",
        ):
            rets = np.diff(seq) / seq[:-1]
        # Replace inf/nan (zero-equity bars,
        # period-end MTM artifacts) with 0
        rets = np.where(
            np.isfinite(rets), rets, 0.0,
        )
        cols.append(rets)
    R = np.column_stack(cols)
    return (R, common[1:])


def compute_sweep_pbo(R: np.ndarray) -> Decimal | None:
    """Cross-variant PBO. Returns None when undefined
    (N < 2, T < 8, or PBO calc itself returns NaN).
    """
    if R.size == 0:
        return None
    T, N = R.shape
    if N < 2 or T < 8:
        return None
    n_blocks = 16 if T >= 16 else 8
    pbo = probability_of_backtest_overfitting(
        R, n_blocks=n_blocks,
    )
    if pbo != pbo:  # NaN
        return None
    return Decimal(str(round(pbo, 3)))

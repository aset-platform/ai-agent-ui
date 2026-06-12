"""Build a replay JSONL fixture from a user's holdings + watchlist,
targeting dates where the strategy entry AST fires — so a paper
*replay* run produces fills on the user's own universe.

Drift-free: the trigger scan evaluates the SAME strategy AST
(``Evaluator.eval_node``) against features assembled by the SAME
``assemble_per_bar_features`` the paper runtime uses at
``_on_bar_close``. A date this scan accepts reproduces in replay.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date  # noqa: F401 — used by later tasks
from decimal import Decimal  # noqa: F401 — used by later tasks

from backend.algo.backtest.evaluator import EvalContext, Evaluator

_logger = logging.getLogger(__name__)
_EVALUATOR = Evaluator()

_DUMMY_TICKER = "__fixture__"
_DUMMY_DATE = date(2000, 1, 1)


def _entry_fires(cond: dict, features: dict) -> bool:
    """True iff the entry AST ``cond`` is truthy against ``features``.

    A missing feature or any eval error means 'does not fire'.
    """
    try:
        ctx = EvalContext(
            ticker=_DUMMY_TICKER,
            bar_date=_DUMMY_DATE,
            features=features,
            open_qty=0,
        )
        return bool(_EVALUATOR.eval_node(cond, ctx))
    except (KeyError, ValueError, TypeError):
        return False


@dataclass
class FixtureBuildResult:
    filename: str
    n_tickers: int
    n_trigger_dates: int
    n_ticks: int
    trigger_tickers: list[str]

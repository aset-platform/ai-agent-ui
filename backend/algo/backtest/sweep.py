"""Sweep orchestrator + AST mutation helper.

The orchestrator (``run_sweep_job``) is added in a
follow-up task; this module bootstraps with just the
mutation primitive that drives every variant in a sweep.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

_logger = logging.getLogger(__name__)


def _mutate_ast(
    strategy: Any, path: str, value: Any,
) -> Any:
    """Return a deep copy of ``strategy`` with the nested
    field at ``path`` set to ``value``.

    Path is dotted (e.g.
    ``"risk.per_trade.stop_loss_pct"``). Each segment
    must resolve via ``getattr`` on the corresponding
    Pydantic model. If any segment doesn't exist, raises
    ``ValueError`` referencing the failing segment.
    """
    parts = path.split(".")
    if not parts:
        raise ValueError(
            f"empty path: {path!r}",
        )
    new = copy.deepcopy(strategy)
    cur = new
    for seg in parts[:-1]:
        if not hasattr(cur, seg):
            raise ValueError(
                f"cannot resolve path {path!r}: "
                f"segment {seg!r} not found on "
                f"{type(cur).__name__}",
            )
        cur = getattr(cur, seg)
        if cur is None:
            raise ValueError(
                f"cannot resolve path {path!r}: "
                f"segment {seg!r} is None",
            )
    last = parts[-1]
    if not hasattr(cur, last):
        raise ValueError(
            f"cannot resolve path {path!r}: "
            f"final segment {last!r} not found on "
            f"{type(cur).__name__}",
        )
    setattr(cur, last, value)
    return new

"""On-demand entry-strength feasibility report (R2).

    docker compose exec -e PYTHONPATH=.:backend backend python -m \
        backend.algo.research.entry_strength_feasibility \
        [--mode live] [--min-n 30] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import logging
import pathlib

from backend.algo.research.entry_strength_feasibility.analysis import (
    analyze,
)
from backend.algo.research.entry_strength_feasibility.cohort import (
    filter_cohort,
    load_labeled_rows,
    to_frame,
)
from backend.algo.research.entry_strength_feasibility.report import (
    write_outputs,
)

_logger = logging.getLogger(__name__)


def _default_out_dir() -> pathlib.Path:
    from backend.paths import APP_HOME

    return (
        pathlib.Path(APP_HOME)
        / "research_runs"
        / "entry-strength-feasibility"
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["live", "paper", "all"],
                   default="live")
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--out-dir", default=None)
    args = p.parse_args(argv)

    rows = filter_cohort(load_labeled_rows(args.mode), args.mode)
    df = to_frame(rows)
    if df.empty:
        _logger.warning("no labeled cohort for mode=%s — nothing to do.",
                        args.mode)
        return 0
    result = analyze(df, args.min_n, args.mode)
    out_dir = (
        pathlib.Path(args.out_dir) if args.out_dir else _default_out_dir()
    )
    path = write_outputs(result, out_dir)
    top = result.feature_stats[0] if result.feature_stats else None
    _logger.info(
        "feasibility: mode=%s n=%d (w=%d/l=%d) composite_auc=%.3f "
        "top=%s min_n_ok=%s -> %s",
        result.mode, result.n_total, result.n_win, result.n_loss,
        result.composite.auc, top.feature if top else "-",
        result.min_n_ok, path,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

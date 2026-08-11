"""Render the feasibility report (markdown + CSV)."""
from __future__ import annotations

import csv
import pathlib

from backend.algo.research.entry_strength_feasibility.analysis import (
    FeasibilityResult,
)

_CAVEAT = (
    "> ⚠️ **Exploratory, IN-SAMPLE — NOT out-of-sample.** Multiple "
    "comparisons uncorrected; small n. Directional only — do NOT "
    "calibrate thresholds from this report."
)


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:.3f}"


def render_report(result: FeasibilityResult) -> str:
    lines: list[str] = []
    lines.append("# Entry-Strength Feasibility Report")
    lines.append("")
    lines.append(_CAVEAT)
    lines.append("")
    warn = "" if result.min_n_ok else " — ⚠️ UNDERPOWERED (min_n not met)"
    lines.append(
        f"**Cohort:** mode={result.mode} n={result.n_total} "
        f"(win={result.n_win} / loss={result.n_loss}) "
        f"span={result.first_date}..{result.last_date}{warn}"
    )
    lines.append("")
    lines.append(
        f"**Composite headline (pre-registered):** "
        f"AUC={_fmt(result.composite.auc)} "
        f"win-rate top/bottom="
        f"{_fmt(result.composite.win_rate_top)}/"
        f"{_fmt(result.composite.win_rate_bottom)}"
    )
    lines.append("")
    lines.append("## Per-feature separation (ranked)")
    lines.append("")
    lines.append(
        "| dimension | feature | n | AUC | dir | wr top | wr bot | MWU p |"
    )
    lines.append("|---|---|--:|--:|---|--:|--:|--:|")
    for s in result.feature_stats:
        lines.append(
            f"| {s.dimension} | {s.feature} | {s.n} | {_fmt(s.auc)} | "
            f"{s.direction} | {_fmt(s.win_rate_top)} | "
            f"{_fmt(s.win_rate_bottom)} | {_fmt(s.mwu_p)} |"
        )
    lines.append("")
    lines.append("## Go / no-go read")
    lines.append("")
    best = result.feature_stats[0] if result.feature_stats else None
    if best and best.separation >= 0.15:
        lines.append(
            f"Strongest separator: **{best.feature}** "
            f"(AUC {_fmt(best.auc)}). Worth carrying into a "
            "properly-powered (OOS) study as data grows."
        )
    else:
        lines.append(
            "No feature separates winners from losers meaningfully at "
            "this n — consistent with 'indistinguishable at entry'. "
            "Accumulate more labeled trades before revisiting."
        )
    return "\n".join(lines) + "\n"


def write_outputs(
    result: FeasibilityResult, out_dir: pathlib.Path
) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.md"
    report_path.write_text(render_report(result))
    with (out_dir / "feature_ranking.csv").open(
        "w", newline=""
    ) as fh:
        w = csv.writer(fh)
        w.writerow(
            ["dimension", "feature", "n", "auc", "separation",
             "win_rate_top", "win_rate_bottom", "mwu_p", "direction"]
        )
        for s in result.feature_stats:
            w.writerow([
                s.dimension, s.feature, s.n, s.auc, s.separation,
                s.win_rate_top, s.win_rate_bottom, s.mwu_p, s.direction,
            ])
    return report_path

"""Markdown + PNG report generation for the bake-off.

Spec §6.4, §6.5, §6.7.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap


def _git_commit() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).parent,
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        out = (
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=Path(__file__).parent,
            )
            .decode()
            .strip()
        )
        return bool(out)
    except Exception:
        return False


def _df_to_markdown(df: pd.DataFrame, floatfmt: str = ".4f") -> str:
    """Render a DataFrame as a Markdown table.

    Uses tabulate when available; falls back to a manual pipe-table
    to avoid a hard dependency on tabulate inside the container.
    """
    try:
        return df.to_markdown(index=False, floatfmt=floatfmt)
    except ImportError:
        pass

    def _fmt(val: Any) -> str:
        if isinstance(val, float):
            return format(val, floatfmt.lstrip(".") and floatfmt or ".4f")
        return str(val)

    cols = list(df.columns)
    rows_fmt = [[_fmt(df.iloc[r, c]) for c in range(len(cols))]
                for r in range(len(df))]
    # Compute column widths.
    widths = [max(len(str(c)), *(len(row[i]) for row in rows_fmt))
              for i, c in enumerate(cols)]
    header = "| " + " | ".join(
        str(c).ljust(widths[i]) for i, c in enumerate(cols)
    ) + " |"
    separator = "| " + " | ".join("-" * w for w in widths) + " |"
    lines = [header, separator]
    for row in rows_fmt:
        lines.append(
            "| " + " | ".join(
                v.ljust(widths[i]) for i, v in enumerate(row)
            ) + " |"
        )
    return "\n".join(lines)


def write_run_metadata(
    *,
    output_dir: Path,
    summary: dict[str, Any],
    hyperparams: dict[str, Any],
    threshold: float,
    fno_csv_path: Path,
) -> None:
    """Write run_metadata.json — the reproducibility ledger §7.5."""
    fno_sha = hashlib.sha256(fno_csv_path.read_bytes()).hexdigest()
    metadata = {
        "git_commit": _git_commit(),
        "dirty_tree": _git_dirty(),
        "started_at_ist": datetime.now(timezone.utc).isoformat(),
        "fno_universe_sha256": fno_sha,
        "hyperparams": hyperparams,
        "threshold_used": threshold,
        "summary": summary,
    }
    (output_dir / "run_metadata.json").write_text(
        json.dumps(metadata, default=str, indent=2)
    )


def write_feature_ranking_csv(
    aggregated: pd.DataFrame, output_dir: Path,
) -> None:
    """Persist the §4 report table as machine-readable CSV."""
    aggregated.sort_values(
        ["mean_abs_long", "mean_abs_short"], ascending=False,
    ).to_csv(output_dir / "feature_ranking.csv", index=False)


def write_class_balance(
    label_dist: dict, output_dir: Path,
) -> None:
    """Write class_balance.csv.

    label_dist keys may be int or str — coerce both.
    """

    def _get(d: dict, k: int) -> float:
        return d.get(k, d.get(str(k), 0.0))

    pd.DataFrame([
        {"class": "SHORT", "fraction": _get(label_dist, 0)},
        {"class": "FLAT",  "fraction": _get(label_dist, 1)},
        {"class": "LONG",  "fraction": _get(label_dist, 2)},
    ]).to_csv(output_dir / "class_balance.csv", index=False)


def plot_shap_summary(
    sv: np.ndarray,
    X: pd.DataFrame,
    class_name: str,
    out_path: Path,
    top_n: int = 15,
) -> None:
    """Beeswarm of a single class's SHAP values."""
    plt.figure(figsize=(10, 8))
    shap.summary_plot(sv, X, max_display=top_n, show=False)
    plt.title(f"SHAP summary - class {class_name}")
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()


def plot_two_sided_ranking(
    aggregated: pd.DataFrame,
    out_path: Path,
    top_n: int = 20,
) -> None:
    """Two-sided bar chart: short importance (left) vs long (right)."""
    agg = aggregated.copy()
    agg["combined"] = agg["mean_abs_long"] + agg["mean_abs_short"]
    agg = agg.sort_values("combined", ascending=True).tail(top_n)

    fig, ax = plt.subplots(
        figsize=(10, max(6, 0.35 * len(agg)))
    )
    y = np.arange(len(agg))
    ax.barh(y, -agg["mean_abs_short"],
            label="mean_abs_short", color="#c0392b")
    ax.barh(y, agg["mean_abs_long"],
            label="mean_abs_long",  color="#27ae60")
    ax.set_yticks(y)
    ax.set_yticklabels(agg["feature"])
    ax.axvline(0, color="black", linewidth=0.5)
    ax.set_xlabel("mean |SHAP| (short <- -> long)")
    ax.set_title("Per-feature importance: short vs long")
    ax.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()


def write_report_md(
    *,
    summary: dict[str, Any],
    aggregated: pd.DataFrame,
    bucketed: pd.DataFrame,
    stable_features: set[str],
    output_dir: Path,
) -> None:
    """Assemble report.md per spec §6.5."""
    lines: list[str] = []
    lines.append(
        f"# Intraday 15m MIS Bake-Off - "
        f"{datetime.now().date().isoformat()}\n"
    )

    gates = summary["gates"]
    soft_failed = [
        k for k, v in gates.items()
        if isinstance(v, str) and v.startswith("fail")
    ]

    lines.append("## 1. Run metadata\n")
    lines.append(f"- Tickers: {summary['tickers']}")
    lines.append(
        f"- Date window: {summary['date_window'][0]} -> "
        f"{summary['date_window'][1]}"
    )
    lines.append(
        f"- Rows: fit={summary['rows']['fit']}, "
        f"val={summary['rows']['val']}, "
        f"test={summary['rows']['test']}"
    )
    lines.append(f"- Feature count: {summary['feature_count']}")
    lines.append(f"- Best iteration: {summary['best_iteration']}\n")

    lines.append("## 2. Training summary\n")
    lines.append(
        f"- Test mlogloss: **{summary['test_mlogloss']:.4f}**"
    )
    lines.append(
        f"- Random baseline mlogloss: "
        f"{summary['random_baseline_mlogloss']:.4f}\n"
    )

    lines.append("## 3. Caveats - READ FIRST\n")
    if soft_failed:
        lines.append(
            f"> WARNING: {len(soft_failed)} soft gate(s) failed: "
            f"{', '.join(soft_failed)}. "
            f"Treat ranking as exploratory only.\n"
        )
    per_regime = gates.get("per_regime", {})
    for r in ("BULL", "SIDEWAYS", "BEAR"):
        info = per_regime.get(r, {})
        rows = info.get("rows", 0)
        mark = " (underpowered)" if info.get("underpowered") else ""
        lines.append(f"- {r}: {rows} test rows{mark}")
    lines.append("")

    lines.append("## 4. Feature ranking\n")
    cols = [
        "feature", "mean_abs_long", "mean_abs_short",
        "asymmetry", "bucket",
        "directional_long", "directional_short",
    ]
    table = bucketed[cols].sort_values(
        ["mean_abs_long", "mean_abs_short"], ascending=False,
    ).copy()
    table["stable"] = table["feature"].isin(stable_features).map(
        {True: "yes", False: "no"}
    )
    lines.append(_df_to_markdown(table, floatfmt=".4f"))
    lines.append("")

    lines.append("## 5. SHAP plots\n")
    lines.append("![SHAP - LONG class](shap_long.png)\n")
    lines.append("![SHAP - SHORT class](shap_short.png)\n")
    lines.append("![Two-sided ranking](feature_ranking.png)\n")

    lines.append("## 6. Draft AST candidates\n")
    stable_rows = (
        bucketed[bucketed["feature"].isin(stable_features)]
        .sort_values("mean_abs_long", ascending=False)
        .head(8)
    )
    if stable_rows.empty:
        lines.append(
            "> Gate 5 produced no stable features. "
            "Section omitted intentionally - see §3 caveats.\n"
        )
    else:
        for _, row in stable_rows.iterrows():
            direction = (
                "-> LONG"
                if row["directional_long"] > 0
                else "-> SHORT"
            )
            lines.append(
                f"- **{row['feature']}** ({row['bucket']}): "
                f"{direction}, "
                f"|long|={row['mean_abs_long']:.4f}, "
                f"|short|={row['mean_abs_short']:.4f}"
            )
        lines.append(
            "\n(Draft AST JSON is left for the follow-up "
            "strategy spec.)\n"
        )

    lines.append("## 7. Next actions\n")
    lines.append("- Read §3 caveats first.")
    lines.append("- If stable features non-empty and gates pass:")
    lines.append("  -> Proceed to strategy v1 spec.")
    lines.append("- If Gate 2 self-tuned: re-run pinned at new threshold.")
    lines.append(
        "- If BULL is underpowered: queue feature backfill spec."
    )
    lines.append(
        "- If Gate 4 failed: document the negative result and stop."
    )

    (output_dir / "report.md").write_text("\n".join(lines))

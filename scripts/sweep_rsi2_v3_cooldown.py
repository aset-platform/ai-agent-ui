"""RSI(2) Connors Daily v3 — cooldown_days parameter sweep + PBO.

Runs N backtest variants (different
``risk.per_trade.cooldown_after_failed_exit_days``) on the
same period + universe and computes:

  • Per-variant aggregate metrics (PnL, win rate, max DD,
    Sharpe).
  • PBO (Probability of Backtest Overfitting) across
    variants via Bailey-de Prado CSCV.

The PBO answers a question the walk-forward UI cannot
answer with a single fixed strategy: **is the best
cooldown choice statistically robust, or just luck of
the in-sample slice?**

Runtime is dominated by N single-period backtests on the
full universe — ~2-3 min per variant, ~12-15 min total
for N=5.

Usage (from host)::

    docker compose exec -T backend \\
        python /app/scripts/sweep_rsi2_v3_cooldown.py
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

# Cap NumPy / OpenMP threads. Set BEFORE numpy imports.
os.environ.setdefault("OMP_NUM_THREADS", "8")

import numpy as np  # noqa: E402

_logger = logging.getLogger("v3_sweep")

# 6-month window matching the user's UI run (Nov 23 → May 23).
PERIOD_START = date(2025, 11, 23)
PERIOD_END = date(2026, 5, 23)
NAV_INR = 100_000

# Spans the meaningful range: aggressive (3d) → default (7d)
# → 2 weeks → 3 weeks → 4 weeks. Five trials gives PBO
# 16-choose-8 = 12,870 IS/OOS combinations.
COOLDOWN_VALUES = [3, 7, 14, 21, 28]

TEMPLATE_NAME = "rsi2_connors_daily_v3"
TEMPLATE_PATH = Path(
    "/app/backend/algo/strategy/templates"
) / f"{TEMPLATE_NAME}.json"

REPORT_OUT = Path(
    "/app/docs/research/"
    "2026-05-24-rsi2-v3-cooldown-pbo-sweep.md"
)

# System operator sentinel — same UUID used by other
# baseline runners. Not a real PG user.
_SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _mutate_template(template: dict, cooldown_days: int) -> dict:
    """Return a deep-copy of ``template`` with the cooldown
    field mutated. The template's field lives at
    ``risk.per_trade.cooldown_after_failed_exit_days``."""
    spec = copy.deepcopy(template)
    spec["risk"]["per_trade"][
        "cooldown_after_failed_exit_days"
    ] = cooldown_days
    base_name = spec.get("name", TEMPLATE_NAME)
    spec["name"] = f"{base_name} [cd={cooldown_days}]"
    return spec


def _resolve_universe(strategy) -> list[str]:
    """Return the warmup-filtered ticker list for the
    strategy. Mirrors the runner-side resolution used by the
    baseline scripts."""
    from auth.models.response import UserContext
    from backend.algo.backtest.universe import (
        filter_warmup_eligible,
        resolve_universe,
    )
    from backend.algo.strategy.feature_warmup import (
        compute_strategy_warmup_days,
    )

    system_user = UserContext(
        user_id=str(_SYSTEM_USER_ID),
        email="system@internal",
        role="superuser",
        subscription_tier="premium",
        subscription_status="active",
        usage_remaining=None,
    )
    tickers = asyncio.run(
        resolve_universe(user=system_user, strategy=strategy),
    )
    warmup_days = compute_strategy_warmup_days(
        strategy.root.model_dump(by_alias=True),
    )
    return filter_warmup_eligible(
        tickers,
        period_start=PERIOD_START,
        warmup_days=warmup_days,
    )


def _run_variant(spec: dict, tickers: list[str]) -> dict:
    """Run one backtest variant and return a result dict.

    Returns:
        {
          "cooldown_days": int,
          "n_trades": int,
          "win_rate_pct": float,
          "total_pnl_pct": float,
          "max_drawdown_pct": float,
          "final_equity": float,
          "eq_dates": [date],
          "eq_values": [float],
        }
    """
    from backend.algo.backtest.runner import run_backtest
    from backend.algo.backtest.types import BacktestRequest
    from backend.algo.strategy.ast import parse_strategy

    cooldown_days = spec["risk"]["per_trade"][
        "cooldown_after_failed_exit_days"
    ]
    strategy = parse_strategy(spec)

    req = BacktestRequest(
        strategy_id=strategy.id,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        initial_capital_inr=Decimal(str(NAV_INR)),
        interval_sec=86400,
    )

    _logger.info(
        "Running cd=%d (strategy=%r) ...",
        cooldown_days,
        strategy.name,
    )
    summary = run_backtest(
        strategy=strategy,
        request=req,
        user_id=_SYSTEM_USER_ID,
        universe=tickers,
    )

    eq = list(summary.equity_curve)
    eq_dates = [p.bar_date for p in eq]
    eq_values = [float(p.equity_inr) for p in eq]
    return {
        "cooldown_days": cooldown_days,
        "n_trades": int(summary.total_trades),
        "win_rate_pct": float(summary.win_rate_pct),
        "total_pnl_pct": float(summary.total_pnl_pct),
        "max_drawdown_pct": float(summary.max_drawdown_pct),
        "final_equity": eq_values[-1] if eq_values else NAV_INR,
        "eq_dates": eq_dates,
        "eq_values": eq_values,
    }


def _build_returns_matrix(variants: list[dict]) -> np.ndarray:
    """Align variants on a common date set and stack daily
    returns into a (T, N) matrix.

    ``T = number of common trading days − 1``.
    ``N = number of variants``.
    """
    if not variants:
        raise ValueError("no variants to align")
    date_sets = [set(v["eq_dates"]) for v in variants]
    common_dates = sorted(set.intersection(*date_sets))
    if len(common_dates) < 2:
        raise ValueError(
            f"common dates across variants = {len(common_dates)}; "
            "need ≥ 2 to compute returns",
        )

    cols: list[np.ndarray] = []
    for v in variants:
        d2eq = dict(zip(v["eq_dates"], v["eq_values"]))
        seq = np.array(
            [d2eq[d] for d in common_dates], dtype=float,
        )
        rets = np.diff(seq) / seq[:-1]
        cols.append(rets)
    return np.column_stack(cols)


def _annualised_sharpe(returns: np.ndarray) -> float:
    """Daily-returns → annualised Sharpe (rf = 0)."""
    if returns.size < 2:
        return 0.0
    mu = returns.mean()
    sigma = returns.std(ddof=0)
    if sigma < 1e-12:
        return 0.0
    return float((mu / sigma) * (252 ** 0.5))


def _write_report(
    variants_sorted: list[dict],
    pbo: float,
    n_blocks: int,
    n_universe: int,
    n_days: int,
) -> None:
    """Write a markdown report at REPORT_OUT."""
    md = [
        "# RSI(2) Connors Daily v3 — Cooldown Sweep + PBO",
        "",
        f"**Generated:** {date.today().isoformat()}",
        f"**Period:** {PERIOD_START} → {PERIOD_END}",
        f"**Capital per variant:** ₹{NAV_INR:,}",
        f"**Universe:** {n_universe} tickers",
        f"**Cooldown variants:** {COOLDOWN_VALUES}",
        f"**Returns-matrix shape:** "
        f"({n_days} days, {len(variants_sorted)} variants)",
        "",
        "## Per-variant aggregate metrics",
        "",
        "Ranked by annualised Sharpe (daily returns, rf = 0).",
        "",
        "| Rank | Cooldown (d) | Trades | Win Rate % | "
        "Total PnL % | Max DD % | Sharpe | Final Equity ₹ |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, v in enumerate(variants_sorted):
        md.append(
            f"| {i + 1} | {v['cooldown_days']} | "
            f"{v['n_trades']} | {v['win_rate_pct']:.2f} | "
            f"{v['total_pnl_pct']:+.2f} | "
            f"{v['max_drawdown_pct']:.2f} | "
            f"{v['sharpe']:+.3f} | "
            f"{v['final_equity']:,.0f} |"
        )

    pbo_str = (
        f"{pbo:.3f}" if not (pbo != pbo)
        else "NaN (insufficient data)"
    )
    if pbo != pbo:
        verdict = (
            "**Verdict:** PBO undefined — "
            "T too small for the block-size."
        )
    elif pbo <= 0.30:
        verdict = (
            "**Verdict: ROBUST.** PBO ≤ 0.30 — the "
            "best cooldown in-sample tends to also win "
            "out-of-sample. Pick the rank-1 variant."
        )
    elif pbo <= 0.50:
        verdict = (
            "**Verdict: AT-RISK.** PBO sits in the "
            "ambiguous zone. The rank-1 in-sample "
            "winner is partly luck — corroborate with "
            "a longer period or external priors before "
            "promoting."
        )
    else:
        verdict = (
            "**Verdict: LIKELY OVERFIT.** PBO > 0.50 — "
            "the in-sample winner regularly underperforms "
            "out-of-sample. Do NOT pick by this sweep; "
            "use an external prior (e.g. the default "
            "cd=7 from PR #235's heuristic) instead."
        )

    md.extend([
        "",
        "## PBO (Probability of Backtest Overfitting)",
        "",
        f"**PBO = {pbo_str}** (CSCV with n_blocks={n_blocks})",
        "",
        "Interpretation:",
        "- ≤ 0.30 → robust",
        "- 0.30–0.50 → at-risk (winner partly luck)",
        "- > 0.50 → likely overfit",
        "",
        verdict,
        "",
        "## Winner & spread",
        "",
        f"- **Best Sharpe:** cd="
        f"{variants_sorted[0]['cooldown_days']} "
        f"(Sharpe={variants_sorted[0]['sharpe']:+.3f}, "
        f"PnL={variants_sorted[0]['total_pnl_pct']:+.2f}%, "
        f"DD={variants_sorted[0]['max_drawdown_pct']:.2f}%, "
        f"trades={variants_sorted[0]['n_trades']})",
        f"- **Worst Sharpe:** cd="
        f"{variants_sorted[-1]['cooldown_days']} "
        f"(Sharpe={variants_sorted[-1]['sharpe']:+.3f}, "
        f"PnL={variants_sorted[-1]['total_pnl_pct']:+.2f}%, "
        f"DD={variants_sorted[-1]['max_drawdown_pct']:.2f}%, "
        f"trades={variants_sorted[-1]['n_trades']})",
        f"- **Sharpe spread:** "
        f"{variants_sorted[0]['sharpe'] - variants_sorted[-1]['sharpe']:+.3f}",
        "",
        "## Generated by",
        "",
        "`scripts/sweep_rsi2_v3_cooldown.py`",
        "",
        "## Notes",
        "",
        "- Each variant runs against the SAME universe + "
        "OHLCV slice; only `cooldown_after_failed_exit_days` "
        "differs. Any spread comes from the cooldown rule, "
        "not data variation.",
        "- PBO is computed on aligned daily returns (T = "
        "common trading days across variants). Days where any "
        "variant had no equity-curve point are dropped.",
        "- A 0%-return day (no trades) on a variant still "
        "contributes a 0 to its column. Variants that trade "
        "rarely (e.g. cd=28) will have lower realised "
        "variance and thus higher Sharpe spuriously — "
        "interpret the table jointly with Trades / Total "
        "PnL %, not Sharpe alone.",
    ])

    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.write_text("\n".join(md))
    _logger.info("Wrote report → %s", REPORT_OUT)


def main() -> None:
    """Entry point — run the sweep + write report."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not TEMPLATE_PATH.exists():
        _logger.error(
            "Template not found: %s", TEMPLATE_PATH,
        )
        sys.exit(1)
    template = json.loads(TEMPLATE_PATH.read_text())
    _logger.info("Loaded template %s", TEMPLATE_NAME)

    # Resolve universe once from the unmutated template — the
    # cooldown field doesn't affect universe resolution.
    from backend.algo.strategy.ast import parse_strategy
    base_strategy = parse_strategy(template)
    tickers = _resolve_universe(base_strategy)
    _logger.info("Universe size: %d tickers", len(tickers))
    if not tickers:
        _logger.error("Universe resolved to 0 tickers")
        sys.exit(1)

    variants: list[dict] = []
    for cd in COOLDOWN_VALUES:
        spec = _mutate_template(template, cd)
        variants.append(_run_variant(spec, tickers))
        _logger.info(
            "  cd=%d done: trades=%d PnL=%+.2f%% "
            "WR=%.1f%% DD=%.2f%%",
            variants[-1]["cooldown_days"],
            variants[-1]["n_trades"],
            variants[-1]["total_pnl_pct"],
            variants[-1]["win_rate_pct"],
            variants[-1]["max_drawdown_pct"],
        )

    # Stack returns matrix + compute PBO + per-variant Sharpe.
    R = _build_returns_matrix(variants)
    _logger.info(
        "Returns matrix: T=%d days, N=%d variants",
        R.shape[0], R.shape[1],
    )

    from backend.algo.backtest.metrics import (
        probability_of_backtest_overfitting,
    )
    n_blocks = 16 if R.shape[0] >= 16 else 8
    if R.shape[0] < 8:
        _logger.warning(
            "T=%d < 8 — PBO will be NaN", R.shape[0],
        )
    pbo = probability_of_backtest_overfitting(
        R, n_blocks=n_blocks,
    )
    _logger.info(
        "PBO = %s (n_blocks=%d)",
        f"{pbo:.3f}" if not (pbo != pbo) else "NaN",
        n_blocks,
    )

    for i, v in enumerate(variants):
        v["sharpe"] = _annualised_sharpe(R[:, i])

    variants_sorted = sorted(variants, key=lambda v: -v["sharpe"])

    _write_report(
        variants_sorted=variants_sorted,
        pbo=pbo,
        n_blocks=n_blocks,
        n_universe=len(tickers),
        n_days=R.shape[0],
    )

    _logger.info("=== COOLDOWN SWEEP RESULTS ===")
    _logger.info(
        "  %3s %7s %6s %8s %6s %8s",
        "cd", "trades", "WR%", "PnL%", "DD%", "Sharpe",
    )
    _logger.info("  " + "-" * 50)
    for v in variants_sorted:
        _logger.info(
            "  %3d %7d %6.1f %+8.2f %6.2f %+8.3f",
            v["cooldown_days"],
            v["n_trades"],
            v["win_rate_pct"],
            v["total_pnl_pct"],
            v["max_drawdown_pct"],
            v["sharpe"],
        )
    pbo_str = f"{pbo:.3f}" if not (pbo != pbo) else "NaN"
    _logger.info(
        "PBO = %s  (n_blocks=%d)", pbo_str, n_blocks,
    )
    if pbo != pbo:
        verdict_short = "undefined"
    elif pbo <= 0.30:
        verdict_short = "robust"
    elif pbo <= 0.50:
        verdict_short = "at-risk"
    else:
        verdict_short = "overfit"
    _logger.info("  → %s", verdict_short)
    _logger.info(
        "Best: cd=%d",
        variants_sorted[0]["cooldown_days"],
    )
    _logger.info("Report: %s", REPORT_OUT)


if __name__ == "__main__":
    main()

"""RSI(2) Connors Daily v1 — baseline backtest runner.

Reproducible single-shot backtest invocation. Calls
backend.algo.backtest.runner.run_backtest() directly because
the runner exposes a library entry point but no CLI binary.

Outputs:
    /tmp/rsi2_connors_baseline.log    — full log
    /tmp/rsi2_triage.json             — G1-G5 metrics for the report
    /tmp/<tag>_triage.json            — when --tag is overridden

Usage (inside the backend container)::

    PYTHONPATH=/app python /app/scripts/run_rsi2_connors_baseline.py \
        2>&1 | tee /tmp/rsi2_connors_baseline.log

Run from the host::

    docker compose exec -T backend \
        python /app/scripts/run_rsi2_connors_baseline.py \
        2>&1 | tee /tmp/rsi2_baseline.log

Sanity re-run excluding one or more tickers::

    docker compose exec -T backend \
        python /app/scripts/run_rsi2_connors_baseline.py \
        --exclude "DIACABS.NS" \
        --tag "rsi2-connors-daily-ex-diacabs" \
        2>&1 | tee /tmp/rsi2_ex_diacabs.log
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

# Honor 80% CPU cap (8 of 10 cores). Set BEFORE numpy/xgb imports.
os.environ.setdefault("OMP_NUM_THREADS", "8")

import pandas as pd

_logger = logging.getLogger("rsi2_baseline")

PERIOD_START = date(2022, 1, 1)
PERIOD_END = date(2026, 5, 21)
NAV_INR = 1_000_000
_DEFAULT_TAG = "rsi2-connors-daily-baseline"
TEMPLATE_NAME = "rsi2_connors_daily_v1"

# Deterministic run-user UUID — system operator sentinel, not a real
# PG user.  Prevents FK errors if the runner does not need to persist.
_SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RSI(2) Connors Daily v1 baseline backtest runner."
    )
    parser.add_argument(
        "--exclude",
        type=str,
        default="",
        help=(
            "Comma-separated tickers to remove from the universe "
            "before backtest (e.g. 'DIACABS.NS,INFY.NS')."
        ),
    )
    parser.add_argument(
        "--tag",
        type=str,
        default=_DEFAULT_TAG,
        help=(
            "Run tag used for algo.runs and output filename. "
            "Defaults to 'rsi2-connors-daily-baseline'. "
            "Override to distinguish sanity re-runs."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    tag: str = args.tag

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    _logger.info("Tag: %s", tag)
    _logger.info("Loading template %s", TEMPLATE_NAME)

    from backend.algo.strategy.ast import parse_strategy

    template_path = (
        Path(__file__).resolve().parent.parent
        / "backend"
        / "algo"
        / "strategy"
        / "templates"
        / f"{TEMPLATE_NAME}.json"
    )
    if not template_path.exists():
        _logger.error("Template not found: %s", template_path)
        sys.exit(1)

    strategy = parse_strategy(json.loads(template_path.read_text()))
    _logger.info("Template parsed: id=%s name=%r", strategy.id, strategy.name)

    _logger.info("Resolving universe (broad NSE stock registry)")
    from auth.models.response import UserContext
    from backend.algo.backtest.universe import resolve_universe
    import asyncio

    # Use superuser context so the discovery scope returns the full
    # stock universe (the template's universe.filter already restricts
    # to market=india, ticker_type=stock; no additional scoping needed).
    system_user = UserContext(
        user_id=str(_SYSTEM_USER_ID),
        email="system@internal",
        role="superuser",
        subscription_tier="premium",
        subscription_status="active",
        usage_remaining=None,
    )

    tickers = asyncio.run(
        resolve_universe(user=system_user, strategy=strategy)
    )
    _logger.info("Universe size before exclusions: %d tickers", len(tickers))

    if args.exclude:
        exclude_set = {
            t.strip() for t in args.exclude.split(",") if t.strip()
        }
        before = len(tickers)
        tickers = [t for t in tickers if t not in exclude_set]
        _logger.info(
            "Excluding %d ticker(s): %s  (%d -> %d)",
            len(exclude_set),
            sorted(exclude_set),
            before,
            len(tickers),
        )

    if not tickers:
        _logger.error(
            "Universe resolved to 0 tickers — aborting. "
            "Check _scoped_tickers + stock_master data."
        )
        sys.exit(1)

    _logger.info("Effective universe size: %d tickers", len(tickers))

    # Build a BacktestRequest for the daily cadence.
    from backend.algo.backtest.types import BacktestRequest

    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        initial_capital_inr=Decimal(str(NAV_INR)),
        interval_sec=86400,  # daily
    )

    _logger.info(
        "Invoking run_backtest %s -> %s  NAV=₹%s  universe=%d tickers  tag=%s",
        PERIOD_START,
        PERIOD_END,
        NAV_INR,
        len(tickers),
        tag,
    )

    from backend.algo.backtest.runner import run_backtest

    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=_SYSTEM_USER_ID,
        universe=tickers,
    )

    _logger.info(
        "Backtest complete: status=%s  trades=%d  final_equity=₹%.2f",
        summary.status,
        summary.total_trades,
        float(summary.final_equity_inr),
    )

    # ----------------------------------------------------------------
    # G1-G5 triage metrics — mirrors spec §6.3 gate definitions.
    # BacktestSummary already computes many of these (win_rate_pct,
    # max_drawdown_pct, total_trades) but we recompute from
    # trade_list for transparency and to apply the ex-stops win-rate
    # filter that the summary's win_rate_pct does NOT apply.
    # ----------------------------------------------------------------
    trades = summary.trade_list  # list[TradeRow]
    n_trades = len(trades)

    final_nav = float(summary.final_equity_inr)
    net_return_pct = (final_nav / NAV_INR - 1) * 100

    days = (PERIOD_END - PERIOD_START).days
    years = days / 365.25
    cagr_pct = (
        ((final_nav / NAV_INR) ** (1 / years) - 1) * 100
        if years > 0
        else 0.0
    )

    # Build a dataframe of closed trades for analysis.
    rows = [
        {
            "ticker": t.ticker,
            "exit_reason": t.exit_reason,
            "realized_pnl_inr": float(t.realised_pnl_inr),
            "bar_date": t.closed_at,  # TradeRow.closed_at is date
        }
        for t in trades
    ]
    closes_df = pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["ticker", "exit_reason", "realized_pnl_inr", "bar_date"]
    )

    # G3: Win rate excluding stop-loss closes (spec §6.3).
    non_stop = closes_df[closes_df["exit_reason"] != "stop_loss"]
    if len(non_stop):
        win_rate = (
            (non_stop["realized_pnl_inr"] > 0).sum()
            / len(non_stop)
            * 100
        )
    else:
        win_rate = 0.0

    # G4: Max drawdown from equity curve (daily aggregated P&L).
    if len(closes_df):
        daily_pnl = (
            closes_df.groupby("bar_date")["realized_pnl_inr"]
            .sum()
            .sort_index()
        )
        nav_series = NAV_INR + daily_pnl.cumsum()
        peak = nav_series.cummax()
        dd_pct = float(((nav_series - peak) / peak).min() * 100)
    else:
        dd_pct = 0.0

    # G5: Concentration — top ticker's share of total realised P&L.
    if len(closes_df) and closes_df["realized_pnl_inr"].sum() != 0:
        by_ticker = closes_df.groupby("ticker")["realized_pnl_inr"].sum()
        total_pnl = closes_df["realized_pnl_inr"].sum()
        top_pct = float(
            abs(by_ticker[by_ticker.abs().idxmax()] / total_pnl) * 100
        )
        top_ticker = str(by_ticker.abs().idxmax())
    else:
        top_pct = 0.0
        top_ticker = "N/A"

    # ----------------------------------------------------------------
    # Gate evaluation
    # ----------------------------------------------------------------
    excluded_tickers = sorted(
        {t.strip() for t in args.exclude.split(",") if t.strip()}
    ) if args.exclude else []

    result = {
        "tag": tag,
        "period_start": PERIOD_START.isoformat(),
        "period_end": PERIOD_END.isoformat(),
        "universe_size": len(tickers),
        "excluded_tickers": excluded_tickers,
        "initial_capital_inr": NAV_INR,
        "final_equity_inr": round(final_nav, 2),
        "trades": int(n_trades),
        "net_return_pct": round(net_return_pct, 3),
        "cagr_pct": round(cagr_pct, 3),
        "win_rate_pct": round(win_rate, 2),
        "win_rate_summary_pct": round(float(summary.win_rate_pct), 2),
        "max_drawdown_pct": round(dd_pct, 3),
        "max_drawdown_summary_pct": round(
            float(summary.max_drawdown_pct), 3
        ),
        "top_ticker": top_ticker,
        "top_ticker_share_pct": round(top_pct, 2),
        "gates": {
            "G1": (
                "pass"
                if n_trades >= 200
                else f"fail: {n_trades} trades < 200 threshold"
            ),
            "G2": (
                "pass"
                if cagr_pct >= 8.0
                else f"fail: CAGR {cagr_pct:.2f}% < 8% threshold"
            ),
            "G3": (
                "pass"
                if win_rate >= 60.0
                else (
                    f"fail: win rate (ex-stops) "
                    f"{win_rate:.2f}% < 60% threshold"
                )
            ),
            "G4": (
                "pass"
                if dd_pct >= -15.0
                else f"fail: max DD {dd_pct:.2f}% exceeds -15% limit"
            ),
            "G5": (
                "pass"
                if abs(top_pct) <= 20.0
                else (
                    f"fail: top ticker ({top_ticker}) "
                    f"concentration {top_pct:.2f}% > 20%"
                )
            ),
        },
    }

    # Use a tag-derived filename so baseline and sanity re-runs don't
    # overwrite each other.  Baseline (default tag) keeps the original
    # path for backward compatibility.
    if tag == _DEFAULT_TAG:
        out_path = Path("/tmp/rsi2_triage.json")
    else:
        safe_tag = tag.replace("/", "_").replace(" ", "_")
        out_path = Path(f"/tmp/{safe_tag}_triage.json")

    out_path.write_text(json.dumps(result, indent=2, default=str))
    _logger.info("Triage written to %s", out_path)
    _logger.info("Triage JSON:\n%s", json.dumps(result, indent=2, default=str))

    # Summary verdict.
    failures = [g for g, v in result["gates"].items() if v != "pass"]
    if not failures:
        _logger.info("ALL 5 GATES PASS — candidate for paper trading.")
    elif len(failures) == 1:
        _logger.warning(
            "SINGLE GATE FAILURE (%s) — apply spec §6.4 tune and re-run.",
            failures[0],
        )
    else:
        _logger.error(
            "MULTIPLE GATE FAILURES (%s) — negative result.",
            ", ".join(failures),
        )


if __name__ == "__main__":
    main()

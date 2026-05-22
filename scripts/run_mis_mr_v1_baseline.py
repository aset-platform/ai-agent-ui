"""Standalone runner for MIS MR Long v1 baseline backtest.

Usage (inside Docker):
    docker compose exec -T backend python scripts/run_mis_mr_v1_baseline.py \
        2>&1 | tee /tmp/mr_v1_baseline.log

Outputs /tmp/triage.json with G1-G5 gate results.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

# Ensure /app is on sys.path so `from backend.X` resolves when
# the container only pre-populates /app/backend, /app/auth etc.
_APP_ROOT = Path(__file__).parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

TAG = "intraday-mr-v1-baseline"
TEMPLATE_NAME = "mis_intraday_meanrev_long_v1"
PERIOD_START = date(2025, 11, 17)
PERIOD_END = date(2026, 5, 21)
INTERVAL_SEC = 900  # 15m
INITIAL_CAPITAL = Decimal("1000000")
USER_ID = UUID("00000000-0000-0000-0000-000000000001")

# Memory cap: full 209-ticker FNO universe needs ~27 GB for
# intraday features (Python Decimal dict overhead). Docker
# container has ~11 GB. Use a representative 50-ticker stride-4
# slice that still exercises the full F&O universe coverage.
# Set to 0 to use the full universe (OOM risk).
UNIVERSE_CAP = 50


def main() -> None:  # noqa: D103 — standalone script, no public API
    logger = logging.getLogger(__name__)
    logger.info("OMP_NUM_THREADS=%s", os.environ.get("OMP_NUM_THREADS", "8"))
    logger.info("Loading template: %s", TEMPLATE_NAME)
    from backend.algo.strategy.templates.loader import load_template
    strategy = load_template(TEMPLATE_NAME)
    logger.info(
        "Template loaded: %s (id=%s)", strategy.name, strategy.id,
    )

    logger.info("Loading F&O universe from CSV")
    from backend.algo.research.intraday_15m_mis_bakeoff.universe import (
        load_fno_universe,
        fno_universe_checksum,
    )
    universe = load_fno_universe()
    full_universe_size = len(universe)
    if UNIVERSE_CAP and len(universe) > UNIVERSE_CAP:
        # Deterministic slice: take every Nth ticker so we span
        # different sectors, not just A-B alphabetically.
        step = len(universe) // UNIVERSE_CAP
        universe = universe[::step][:UNIVERSE_CAP]
        logger.warning(
            "UNIVERSE_CAP=%d applied: reduced %d → %d tickers "
            "(step=%d). Full 209-ticker run requires ~27 GB RAM.",
            UNIVERSE_CAP,
            full_universe_size,
            len(universe),
            step,
        )
    logger.info(
        "F&O universe: %d tickers (sha256=%s)",
        len(universe),
        fno_universe_checksum()[:12],
    )

    from backend.algo.backtest.types import BacktestRequest
    request = BacktestRequest(
        strategy_id=strategy.id,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        initial_capital_inr=INITIAL_CAPITAL,
        interval_sec=INTERVAL_SEC,
    )
    logger.info(
        "BacktestRequest: %s .. %s  interval_sec=%d  nav=%s",
        PERIOD_START,
        PERIOD_END,
        INTERVAL_SEC,
        INITIAL_CAPITAL,
    )

    logger.info("Running backtest — this may take 20-40 minutes …")
    from backend.algo.backtest.runner import run_backtest
    summary = run_backtest(
        strategy=strategy,
        request=request,
        user_id=USER_ID,
        universe=universe,
    )

    logger.info(
        "run_id=%s  total_trades=%d  final_equity=%.2f  "
        "total_pnl=%.2f (%.3f%%)  max_dd=%.3f%%",
        summary.run_id,
        summary.total_trades,
        float(summary.final_equity_inr),
        float(summary.total_pnl_inr),
        float(summary.total_pnl_pct),
        float(summary.max_drawdown_pct),
    )

    # ----------------------------------------------------------------
    # G1-G5 gate computation directly from BacktestSummary
    # ----------------------------------------------------------------
    trades = summary.trade_list  # list[TradeRow]

    # exit_reason enum values actually observed
    exit_reasons_seen = sorted({t.exit_reason for t in trades})
    logger.info("exit_reason values observed: %s", exit_reasons_seen)

    # G1: trade count >= 100
    g1_trades = len(trades)
    g1_pass = g1_trades >= 100

    # G2: net return > 0%
    net_return_pct = float(summary.total_pnl_pct)
    g2_pass = net_return_pct > 0.0

    # G3: win rate excluding stop-out exits
    STOP_REASONS = {"stop_loss"}
    non_stop = [t for t in trades if t.exit_reason not in STOP_REASONS]
    wins_non_stop = [t for t in non_stop if t.realised_pnl_inr > 0]
    win_rate = (
        len(wins_non_stop) / len(non_stop) * 100 if non_stop else 0.0
    )
    g3_pass = win_rate >= 50.0

    # G4: max drawdown <= 5%
    max_dd = float(summary.max_drawdown_pct)
    g4_pass = max_dd <= 5.0

    # G5: concentration — no single ticker > 20% of total realized PNL
    by_ticker: dict[str, float] = defaultdict(float)
    for t in trades:
        by_ticker[t.ticker] += float(t.realised_pnl_inr)
    total_pnl = sum(by_ticker.values())
    if total_pnl != 0 and by_ticker:
        top_ticker = max(by_ticker, key=lambda k: abs(by_ticker[k]))
        top_pct = (by_ticker[top_ticker] / total_pnl) * 100
    else:
        top_ticker = "n/a"
        top_pct = 0.0
    g5_pass = abs(top_pct) <= 20.0

    # Per-regime breakdown from equity curve (approximate —
    # full per-bar regime not in BacktestSummary; use trade dates)
    regime_trades: dict[str, list[float]] = defaultdict(list)
    try:
        from backend.algo.regime.repo import get_regime_history
        rh_rows = get_regime_history(PERIOD_START, PERIOD_END)
        regime_by_date = {r.bar_date: r.regime_label for r in rh_rows}
        for t in trades:
            label = regime_by_date.get(t.closed_at, "unknown")
            regime_trades[label].append(float(t.realised_pnl_inr))
    except Exception as exc:  # noqa: BLE001
        logger.warning("regime history lookup failed: %s", exc)
        regime_by_date = {}

    by_regime = []
    for label, pnls in sorted(regime_trades.items()):
        n = len(pnls)
        pnl_sum = sum(pnls)
        wr = sum(1 for p in pnls if p > 0) / n * 100 if n else 0
        by_regime.append({
            "regime_label": label,
            "n": n,
            "pnl": round(pnl_sum, 2),
            "win_rate": round(wr, 2),
        })

    # Top-10 tickers by realized P&L
    top10 = sorted(
        by_ticker.items(), key=lambda kv: kv[1], reverse=True,
    )[:10]

    result = {
        "run_id": str(summary.run_id),
        "tag": TAG,
        "template": TEMPLATE_NAME,
        "period": f"{PERIOD_START} → {PERIOD_END}",
        "interval_sec": INTERVAL_SEC,
        "universe_size": len(universe),
        "universe_cap_applied": bool(UNIVERSE_CAP and full_universe_size > UNIVERSE_CAP),
        "initial_capital_inr": float(INITIAL_CAPITAL),
        "final_equity_inr": float(summary.final_equity_inr),
        "total_pnl_inr": round(float(summary.total_pnl_inr), 2),
        "total_fees_inr": round(float(summary.total_fees_inr), 2),
        "trades": g1_trades,
        "winning_trades": summary.winning_trades,
        "losing_trades": summary.losing_trades,
        "net_return_pct": round(net_return_pct, 3),
        "win_rate_overall_pct": round(float(summary.win_rate_pct), 2),
        "win_rate_ex_stops_pct": round(win_rate, 2),
        "max_drawdown_pct": round(max_dd, 3),
        "top_ticker": top_ticker,
        "top_ticker_share_pct": round(top_pct, 2),
        "exit_reasons_observed": exit_reasons_seen,
        "gates": {
            "G1_trade_count": "pass" if g1_pass else f"fail: {g1_trades} < 100",
            "G2_net_return": "pass" if g2_pass else f"fail: {net_return_pct:.3f}%",
            "G3_win_rate_ex_stops": "pass" if g3_pass else f"fail: {win_rate:.2f}% < 50%",
            "G4_max_drawdown": "pass" if g4_pass else f"fail: {max_dd:.3f}% > 5%",
            "G5_concentration": "pass" if g5_pass else f"fail: {top_pct:.2f}% in {top_ticker}",
        },
        "all_gates_pass": all([g1_pass, g2_pass, g3_pass, g4_pass, g5_pass]),
        "by_regime": by_regime,
        "top10_tickers_by_pnl": [
            {"ticker": k, "pnl": round(v, 2)} for k, v in top10
        ],
    }

    out_path = "/tmp/triage.json"
    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2, default=str)

    logger.info("Triage JSON written to %s", out_path)
    logger.info("Triage result:\n%s", json.dumps(result, indent=2, default=str))

    gates = result["gates"]
    logger.info("=== GATE RESULTS ===")
    for gate, verdict in gates.items():
        logger.info("  %s: %s", gate, verdict)
    logger.info(
        "Overall: %s",
        "ALL PASS" if result["all_gates_pass"] else "SOME FAILED",
    )


if __name__ == "__main__":
    main()

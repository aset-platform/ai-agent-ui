#!/usr/bin/env python3
"""POC forecast comparison script — 30-ticker test batch.

Records baseline forecast metrics from Iceberg, re-runs
Prophet forecasts for the 30 POC tickers (large-cap,
mid-cap, volatile), then prints a before/after MAPE and
confidence-score comparison table.

Usage::

    # Inside Docker backend container:
    python scripts/poc_forecast_comparison.py

    # From host (with virtualenv):
    PYTHONPATH=backend python scripts/poc_forecast_comparison.py
"""

import logging
import os
import sys
import uuid
from pathlib import Path

_out = sys.stdout.write

# ── Path setup ───────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))
sys.path.insert(0, str(_PROJECT_ROOT / "stocks"))
sys.path.insert(0, str(_PROJECT_ROOT))
os.chdir(str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
_logger = logging.getLogger(__name__)

# ── Test batch ───────────────────────────────────────────

LARGE_CAP = [
    "TCS.NS",
    "RELIANCE.NS",
    "INFY.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "ITC.NS",
    "LT.NS",
    "WIPRO.NS",
    "BAJFINANCE.NS",
    "HDFC.NS",
]
MID_CAP = [
    "TANLA.NS",
    "IRCTC.NS",
    "PAYTM.NS",
    "ZOMATO.NS",
    "DELHIVERY.NS",
    "NYKAA.NS",
    "POLICYBZR.NS",
    "MAPMYINDIA.NS",
    "HAPPSTMNDS.NS",
    "ROUTE.NS",
]
VOLATILE = [
    "YESBANK.NS",
    "IDEA.NS",
    "PCJEWELLER.NS",
    "RPOWER.NS",
    "SUZLON.NS",
    "JPPOWER.NS",
    "ADANIGREEN.NS",
    "ADANIENT.NS",
    "PNB.NS",
    "TATAMOTORS.NS",
]

ALL_TICKERS: list[str] = LARGE_CAP + MID_CAP + VOLATILE
_HORIZON = 9  # months — matches execute_run_forecasts default


# ── Baseline reader ──────────────────────────────────────

def _read_baseline() -> dict[str, dict]:
    """Read latest forecast_runs per ticker from Iceberg.

    Uses ROW_NUMBER() to select the most recent row per
    ticker for the configured horizon.  Returns a mapping
    of ticker → {mape, confidence_score}.
    """
    from backend.db.duckdb_engine import (
        invalidate_metadata,
        query_iceberg_df,
    )

    invalidate_metadata()
    _logger.info(
        "Reading baseline from Iceberg forecast_runs …"
    )

    tickers_in = ", ".join(
        f"'{t}'" for t in ALL_TICKERS
    )
    sql = f"""
        WITH ranked AS (
            SELECT
                ticker,
                mape,
                confidence_score,
                computed_at,
                ROW_NUMBER() OVER (
                    PARTITION BY ticker
                    ORDER BY computed_at DESC
                ) AS rn
            FROM forecast_runs
            WHERE horizon_months = {_HORIZON}
              AND ticker IN ({tickers_in})
        )
        SELECT ticker, mape, confidence_score
        FROM ranked
        WHERE rn = 1
    """
    df = query_iceberg_df("stocks.forecast_runs", sql)
    baseline: dict[str, dict] = {}
    for _, row in df.iterrows():
        baseline[row["ticker"]] = {
            "mape": row.get("mape"),
            "confidence_score": row.get("confidence_score"),
        }
    _logger.info(
        "Baseline loaded for %d / %d tickers.",
        len(baseline),
        len(ALL_TICKERS),
    )
    return baseline


# ── Monkey-patch helper ──────────────────────────────────

def _apply_ticker_patch() -> None:
    """Restrict executor._analyzable_tickers to test batch.

    Wraps the original function so the forecast run only
    processes ALL_TICKERS regardless of full registry
    contents.
    """
    import jobs.executor as ex

    _orig = ex._analyzable_tickers

    def _patched(registry, tickers):
        result = _orig(registry, tickers)
        filtered = [t for t in result if t in ALL_TICKERS]
        _logger.info(
            "[patch] _analyzable_tickers: %d → %d tickers",
            len(result),
            len(filtered),
        )
        return filtered

    ex._analyzable_tickers = _patched
    _logger.info(
        "Monkey-patched executor._analyzable_tickers "
        "to filter to %d test tickers.",
        len(ALL_TICKERS),
    )


# ── Forecast runner ──────────────────────────────────────

def _run_forecasts(repo) -> str:
    """Execute Prophet forecasts for the test batch.

    Creates a synthetic scheduler run_id and calls
    execute_run_forecasts with force=True so all tickers
    are re-processed regardless of freshness.

    Returns:
        The run_id used for this execution.
    """
    from jobs.executor import execute_run_forecasts

    run_id = f"poc-{uuid.uuid4().hex[:8]}"
    _logger.info(
        "Starting forecast run (run_id=%s, force=True) …",
        run_id,
    )
    execute_run_forecasts(
        scope="india",
        run_id=run_id,
        repo=repo,
        force=True,
    )
    _logger.info(
        "Forecast run %s completed.", run_id
    )
    return run_id


# ── New results reader ───────────────────────────────────

def _read_new_results() -> dict[str, dict]:
    """Read freshly written forecast_runs after the run.

    Invalidates DuckDB metadata cache before querying to
    ensure stale reads don't mask new results.
    """
    from backend.db.duckdb_engine import (
        invalidate_metadata,
        query_iceberg_df,
    )

    invalidate_metadata()
    _logger.info(
        "Reading new results from Iceberg …"
    )

    tickers_in = ", ".join(
        f"'{t}'" for t in ALL_TICKERS
    )
    sql = f"""
        WITH ranked AS (
            SELECT
                ticker,
                mape,
                confidence_score,
                computed_at,
                ROW_NUMBER() OVER (
                    PARTITION BY ticker
                    ORDER BY computed_at DESC
                ) AS rn
            FROM forecast_runs
            WHERE horizon_months = {_HORIZON}
              AND ticker IN ({tickers_in})
        )
        SELECT ticker, mape, confidence_score
        FROM ranked
        WHERE rn = 1
    """
    df = query_iceberg_df("stocks.forecast_runs", sql)
    results: dict[str, dict] = {}
    for _, row in df.iterrows():
        results[row["ticker"]] = {
            "mape": row.get("mape"),
            "confidence_score": row.get("confidence_score"),
        }
    return results


# ── Comparison printer ───────────────────────────────────

def _print_comparison(
    baseline: dict[str, dict],
    new_results: dict[str, dict],
) -> None:
    """Print the before/after comparison table to stdout.

    Args:
        baseline: Pre-run metrics keyed by ticker.
        new_results: Post-run metrics keyed by ticker.
    """
    sep = "-" * 75

    _out("\n")
    _out("=" * 75 + "\n")
    _out(
        f"POC FORECAST COMPARISON"
        f" ({len(ALL_TICKERS)} tickers)\n"
    )
    _out("=" * 75 + "\n")
    _out(
        f"{'Ticker':<18} | "
        f"{'Old MAPE':>9} | "
        f"{'New MAPE':>9} | "
        f"{'Delta':>8} | "
        f"{'Old Conf':>9} | "
        f"{'New Conf':>9} | "
        "Improved\n"
    )
    _out(sep + "\n")

    improved = 0
    degraded = 0
    skipped = 0
    mape_deltas: list[float] = []

    for ticker in ALL_TICKERS:
        old = baseline.get(ticker, {})
        new = new_results.get(ticker, {})

        old_mape = old.get("mape")
        new_mape = new.get("mape")
        old_conf = old.get("confidence_score")
        new_conf = new.get("confidence_score")

        def _fmt_pct(v) -> str:
            return f"{v * 100:.1f}%" if v is not None else "  N/A "

        def _fmt_conf(v) -> str:
            return f"{v:.3f}" if v is not None else "  N/A "

        if old_mape is not None and new_mape is not None:
            delta = new_mape - old_mape
            mape_deltas.append(delta)
            improved_flag = delta < 0
            if improved_flag:
                improved += 1
            else:
                degraded += 1
            improved_str = "YES" if improved_flag else "NO "
            delta_str = (
                f"{delta * 100:+.1f}%"
            )
        else:
            skipped += 1
            improved_str = "N/A"
            delta_str = "  N/A "

        _out(
            f"{ticker:<18} | "
            f"{_fmt_pct(old_mape):>9} | "
            f"{_fmt_pct(new_mape):>9} | "
            f"{delta_str:>8} | "
            f"{_fmt_conf(old_conf):>9} | "
            f"{_fmt_conf(new_conf):>9} | "
            f"{improved_str}\n"
        )

    _out(sep + "\n")

    avg_delta = (
        sum(mape_deltas) / len(mape_deltas)
        if mape_deltas
        else None
    )
    avg_delta_str = (
        f"{avg_delta * 100:+.1f}%"
        if avg_delta is not None
        else "  N/A "
    )
    total_compared = improved + degraded
    _out(
        f"{'AGGREGATE':<18} | "
        f"{'':>9} | "
        f"{'':>9} | "
        f"{avg_delta_str:>8} | "
        f"{'':>9} | "
        f"{'':>9} | "
        f"{improved}/{total_compared}\n"
    )
    _out("=" * 75 + "\n")
    _out(
        f"  Improved: {improved}  |  "
        f"Degraded: {degraded}  |  "
        f"Skipped (no data): {skipped}\n"
    )
    _out("=" * 75 + "\n\n")


# ── Entry point ──────────────────────────────────────────

def main() -> None:
    """Run the full POC comparison flow."""
    from tools._stock_shared import _require_repo

    repo = _require_repo()

    # 1. Capture baseline before the run
    baseline = _read_baseline()

    # 2. Restrict executor to test batch only
    _apply_ticker_patch()

    # 3. Re-run forecasts with force=True
    _run_forecasts(repo)

    # 4. Read new results (fresh DuckDB metadata)
    new_results = _read_new_results()

    # 5. Print comparison
    _print_comparison(baseline, new_results)


if __name__ == "__main__":
    main()

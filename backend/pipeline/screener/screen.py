"""Piotroski F-Score screening orchestrator.

Reads quarterly_results from Iceberg for stock_master
tickers, aggregates to annual, computes F-Score, enriches
with company_info metadata, and persists to
stocks.piotroski_scores.

Usage::

    from backend.pipeline.screener.screen import run_screen
    result = await run_screen()
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date

import pandas as pd

from backend.pipeline.screener.piotroski import (
    compute_piotroski,
)

_logger = logging.getLogger(__name__)


def _aggregate_annual(
    qr_df: pd.DataFrame,
) -> dict[int, dict]:
    """Aggregate quarterly results to annual dicts.

    Income/cashflow rows are summed per fiscal_year.
    Balance rows use latest quarter_end per year.

    Args:
        qr_df: Quarterly results for one ticker.

    Returns:
        Dict keyed by fiscal_year with merged fields.
    """
    if qr_df.empty:
        return {}

    years: dict[int, dict] = {}

    # Income: sum per year
    inc = qr_df[qr_df["statement_type"] == "income"]
    for fy, grp in inc.groupby("fiscal_year"):
        years.setdefault(int(fy), {})
        for col in [
            "revenue",
            "net_income",
            "gross_profit",
        ]:
            vals = grp[col].dropna()
            if not vals.empty:
                years[int(fy)][col] = vals.sum()

    # Cashflow: sum per year
    cf = qr_df[qr_df["statement_type"] == "cashflow"]
    for fy, grp in cf.groupby("fiscal_year"):
        years.setdefault(int(fy), {})
        for col in ["operating_cashflow"]:
            vals = grp[col].dropna()
            if not vals.empty:
                years[int(fy)][col] = vals.sum()

    # Balance: latest quarter per year
    bal = qr_df[qr_df["statement_type"] == "balance"].copy()
    if not bal.empty:
        bal = bal.sort_values("quarter_end")
        for fy, grp in bal.groupby("fiscal_year"):
            years.setdefault(int(fy), {})
            latest = grp.iloc[-1]
            for col in [
                "total_assets",
                "total_debt",
                "current_assets",
                "current_liabilities",
                "shares_outstanding",
            ]:
                val = latest.get(col)
                if pd.notna(val):
                    years[int(fy)][col] = float(val)

    return years


async def run_screen(
    tickers: list[str] | None = None,
) -> dict:
    """Score stocks and persist to Iceberg.

    Args:
        tickers: Optional list of tickers to score.
            If None, scores all active stock_master
            tickers.

    Returns:
        Summary dict with counts and elapsed time.
    """
    t0 = time.monotonic()
    from tools._stock_shared import _require_repo

    repo = _require_repo()

    # Get tickers from stock_master if not provided
    if tickers is None:
        from backend.db.engine import session_factory
        from backend.pipeline.universe import (
            get_all_stocks,
        )

        async with session_factory() as session:
            stocks = await get_all_stocks(
                session,
                active_only=True,
            )
        tickers = [s.yf_ticker for s in stocks]

    _logger.info(
        "Scoring %d tickers for Piotroski F-Score",
        len(tickers),
    )

    # Load company_info for enrichment
    try:
        ci_df = repo._table_to_df(
            "stocks.company_info",
        )
        if not ci_df.empty:
            ci_df = (
                ci_df.sort_values(
                    "fetched_at",
                    ascending=False,
                )
                .groupby("ticker", as_index=False)
                .first()
            )
    except Exception:
        ci_df = pd.DataFrame()

    today = date.today()
    scores: list[dict] = []
    skipped = 0
    failed = 0

    for ticker in tickers:
        try:
            qr_df = repo.get_quarterly_results(ticker)
            if qr_df.empty:
                skipped += 1
                continue

            annual = _aggregate_annual(qr_df)
            if len(annual) < 2:
                skipped += 1
                continue

            # Pick latest 2 fiscal years
            sorted_years = sorted(
                annual.keys(),
                reverse=True,
            )
            curr_year = annual[sorted_years[0]]
            prev_year = annual[sorted_years[1]]

            result = compute_piotroski(
                curr_year,
                prev_year,
            )

            # Enrich with company_info
            ci_row = {}
            if not ci_df.empty:
                match = ci_df[ci_df["ticker"] == ticker]
                if not match.empty:
                    ci_row = match.iloc[0].to_dict()

            scores.append(
                {
                    "score_id": str(uuid.uuid4()),
                    "ticker": ticker,
                    "score_date": today,
                    "total_score": (result.total_score),
                    "label": result.label,
                    "roa_positive": (result.roa_positive),
                    "operating_cf_positive": (result.operating_cf_positive),
                    "roa_increasing": (result.roa_increasing),
                    "cf_gt_net_income": (result.cf_gt_net_income),
                    "leverage_decreasing": (result.leverage_decreasing),
                    "current_ratio_increasing": (
                        result.current_ratio_increasing
                    ),
                    "no_dilution": (result.no_dilution),
                    "gross_margin_increasing": (
                        result.gross_margin_increasing
                    ),
                    "asset_turnover_increasing": (
                        result.asset_turnover_increasing
                    ),
                    "market_cap": ci_row.get(
                        "market_cap",
                    ),
                    "revenue": curr_year.get(
                        "revenue",
                    ),
                    "avg_volume": ci_row.get(
                        "avg_volume",
                    ),
                    "sector": ci_row.get("sector"),
                    "industry": ci_row.get(
                        "industry",
                    ),
                    "company_name": ci_row.get(
                        "company_name",
                    ),
                }
            )
        except Exception:
            _logger.warning(
                "Failed to score %s",
                ticker,
                exc_info=True,
            )
            failed += 1

    # Persist
    written = 0
    if scores:
        written = repo.insert_piotroski_scores(scores)

    strong = sum(1 for s in scores if s["total_score"] >= 8)
    moderate = sum(1 for s in scores if 5 <= s["total_score"] < 8)
    weak = sum(1 for s in scores if s["total_score"] < 5)
    elapsed = time.monotonic() - t0

    summary = {
        "tickers": len(tickers),
        "scored": written,
        "skipped": skipped,
        "failed": failed,
        "strong": strong,
        "moderate": moderate,
        "weak": weak,
        "elapsed_s": round(elapsed, 1),
    }
    _logger.info("Screen complete: %s", summary)
    return summary

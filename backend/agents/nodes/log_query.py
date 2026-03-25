"""Query logger node — persists query metadata.

Logs every query to the ``stocks.query_log`` Iceberg
table with intent, tools used, data sources, timing,
and gap tickers.  Also inserts/increments
``stocks.data_gaps`` for tickers that triggered
external fetches.

Errors in logging never crash the graph — they are
silently warned.
"""

from __future__ import annotations

import logging
import time

_logger = logging.getLogger(__name__)


def _has_fresh_local_data(
    repo, ticker: str,
) -> bool:
    """Check if ticker has fresh OHLCV data."""
    from datetime import date

    try:
        ohlcv = repo.get_ohlcv(ticker)
        if ohlcv.empty:
            return False
        last_date = ohlcv.iloc[-1]["date"]
        if hasattr(last_date, "date"):
            last_date = last_date.date()
        # Fresh if fetched within last 2 days
        delta = (date.today() - last_date).days
        return delta <= 2
    except Exception:
        return False


def log_query(state: dict) -> dict:
    """Persist query metadata to Iceberg.

    Reads state fields populated by earlier nodes
    (guardrail, router, sub-agent) and writes a
    single row to ``query_log``.  Also detects data
    gaps and inserts/increments ``data_gaps``.
    """
    try:
        from tools._stock_shared import _get_repo

        repo = _get_repo()
        if repo is None:
            return {}

        # Compute response time
        start_ns = state.get("start_time_ns", 0)
        elapsed_ms = 0
        if start_ns:
            elapsed_ms = int(
                (time.monotonic_ns() - start_ns)
                / 1_000_000
            )

        # Extract tool names from events
        tools_used = [
            e["tool"]
            for e in state.get("tool_events", [])
            if e.get("type") == "tool_start"
        ]

        data_sources = state.get(
            "data_sources_used", [],
        )
        was_local = (
            "yfinance" not in data_sources
            and "serpapi" not in data_sources
        )

        # Detect gap tickers
        tickers = state.get("tickers", [])
        gap_tickers = [
            t for t in tickers
            if not _has_fresh_local_data(repo, t)
        ]

        repo.insert_query_log({
            "user_id": state.get("user_id", ""),
            "query_text": state.get(
                "user_input", "",
            ),
            "classified_intent": state.get(
                "intent", "",
            ),
            "sub_agent_invoked": state.get(
                "current_agent", "",
            ),
            "tools_used": tools_used,
            "data_sources_used": data_sources,
            "was_local_sufficient": was_local,
            "response_time_ms": elapsed_ms,
            "gap_tickers": gap_tickers,
        })

        # Insert/increment data gaps
        for ticker in gap_tickers:
            repo.insert_data_gap(ticker, "ohlcv")

    except Exception:
        _logger.debug(
            "log_query failed (non-fatal)",
            exc_info=True,
        )

    return {}

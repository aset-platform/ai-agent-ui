"""Resolve a Strategy's stored universe.{scope,filter} to a
concrete list of tickers.

Two-stage pipeline:

1. ``scope`` (``discovery|watchlist|portfolio``) selects the
   candidate set via the existing ``_scoped_tickers`` helper —
   same scoping that powers the Insights tabs.
2. ``filter`` (``market``, ``ticker_type``) trims that candidate
   set against the platform's stock_master registry. The filter
   matches the AST schema in ``backend/algo/strategy/ast.py``
   (``UniverseFilter``).

Backward-compat: if the strategy passes a bare object (no
``filter`` attribute), only stage 1 runs.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Iterable

from auth.models import UserContext
from backend.insights_routes import (
    _scoped_tickers_for_strategy,
)
from backend.market_utils import detect_market

_logger = logging.getLogger(__name__)

_VALID_SCOPES = {"discovery", "watchlist", "portfolio"}


def _registry_meta() -> dict[str, dict[str, Any]]:
    """Lazy stock_master fetch. Cached at the StockRepository
    layer so repeated callers within one request don't re-hit
    Iceberg."""
    from stocks.repository import StockRepository
    return StockRepository().get_all_registry()


def _load_ticker_first_bars(
    tickers: list[str],
) -> dict[str, date]:
    """Earliest OHLCV bar per ticker as ``{ticker: date}``.

    ASETPLTFRM-433. Used by ``filter_warmup_eligible`` to drop
    tickers whose OHLCV history is too short for the strategy's
    longest indicator warmup window.

    Returns an empty dict on Iceberg miss (tested isolation).
    Tickers absent from OHLCV are absent from the returned dict
    too — the caller treats them as warmup-ineligible.
    """
    if not tickers:
        return {}
    from backend.db.duckdb_engine import query_iceberg_table
    rows = query_iceberg_table(
        "stocks.ohlcv",
        "SELECT ticker, MIN(date) AS first_bar "
        "FROM ohlcv GROUP BY ticker",
        [],
    )
    keep = set(tickers)
    return {
        r["ticker"]: r["first_bar"]
        for r in rows
        if r["ticker"] in keep and r["first_bar"] is not None
    }


def filter_warmup_eligible(
    tickers: list[str],
    *,
    period_start: date,
    warmup_days: int,
) -> list[str]:
    """Drop tickers whose earliest OHLCV bar is too recent.

    ASETPLTFRM-433. A ticker is *warmup-eligible* when its first
    available bar lands on or before ``period_start - warmup_days``
    (calendar days, not trading — over-estimates a touch but the
    quantum's irrelevant past 200 days).

    ``warmup_days == 0`` → no-op (every ticker passes; cheaper than
    skipping the call). Tickers absent from the OHLCV table are
    dropped — the runner has no bars to feed indicators anyway.
    """
    if warmup_days <= 0:
        return list(tickers)
    first_bars = _load_ticker_first_bars(tickers)
    if not first_bars:
        _logger.warning(
            "filter_warmup_eligible: OHLCV first-bar map empty — "
            "skipping warmup filter (warmup_days=%d)",
            warmup_days,
        )
        return list(tickers)
    cutoff = period_start - timedelta(days=warmup_days)
    return [
        t for t in tickers
        if first_bars.get(t) is not None
        and first_bars[t] <= cutoff
    ]


def _load_snapshot_adtv() -> dict[str, float]:
    """Latest algo.universe_snapshot keyed by ticker → adtv_inr_60d.

    ASETPLTFRM-430 Exp.1 — used by min_adtv_inr filter. Picks the
    most recent rebalance_date in the snapshot table; returns
    {ticker: adtv_inr_60d}. Empty dict if no snapshot exists yet
    (caller treats as feature-disabled).
    """
    from backend.db.duckdb_engine import query_iceberg_table
    rows = query_iceberg_table(
        "stocks.universe_snapshot",
        "SELECT rebalance_date, ticker, adtv_inr_60d "
        "FROM universe_snapshot",
        [],
    )
    if not rows:
        return {}
    latest = max(r["rebalance_date"] for r in rows)
    return {
        r["ticker"]: float(r["adtv_inr_60d"] or 0.0)
        for r in rows
        if r["rebalance_date"] == latest
    }


def _apply_filter(
    tickers: Iterable[str],
    *,
    markets: set[str] | None,
    ticker_types: set[str] | None,
) -> list[str]:
    """Drop tickers whose market or ticker_type don't match.

    ``markets``: if not None, keep only tickers whose
    ``detect_market(ticker)`` is in the set. ``"all"`` short-
    circuits the market check.

    ``ticker_types``: if not None, keep only tickers whose
    registry ``ticker_type`` is in the set.
    """
    market_check = (
        markets is not None and "all" not in markets
    )
    type_check = ticker_types is not None
    if not market_check and not type_check:
        return list(tickers)

    registry = _registry_meta() if type_check else {}
    out: list[str] = []
    for t in tickers:
        if market_check and detect_market(t) not in markets:
            continue
        if type_check:
            meta = registry.get(t)
            if meta is None:
                # Unknown ticker — skip rather than including a
                # row we can't classify. The runner would also
                # have no OHLCV bars for it.
                continue
            tt = meta.get("ticker_type", "stock")
            if tt not in ticker_types:
                continue
        out.append(t)
    return out


async def resolve_universe(
    *,
    user: UserContext,
    strategy: Any,
) -> list[str]:
    """Return the list of tickers the backtest should iterate.

    Strategy AST stores ``universe.scope`` ∈ ``{discovery,
    watchlist, portfolio}`` and an optional ``universe.filter``
    (``market``, ``ticker_type``). Unknown scopes degrade to
    ``watchlist`` (safest non-empty default).
    """
    raw = getattr(strategy.universe, "scope", "watchlist")
    scope = raw if raw in _VALID_SCOPES else "watchlist"
    candidates = await _scoped_tickers_for_strategy(
        user=user, scope=scope,
    )

    filter_obj = getattr(strategy.universe, "filter", None)
    if filter_obj is None:
        return candidates

    raw_market = getattr(filter_obj, "market", None)
    markets: set[str] | None = (
        {str(raw_market)} if raw_market else None
    )
    raw_types = getattr(filter_obj, "ticker_type", None)
    ticker_types: set[str] | None = (
        {str(t) for t in raw_types} if raw_types else None
    )

    filtered = _apply_filter(
        candidates,
        markets=markets,
        ticker_types=ticker_types,
    )

    # ASETPLTFRM-430 Exp.1 — liquidity floor against latest
    # stocks.universe_snapshot.adtv_inr_60d. Tickers absent from
    # the snapshot are treated as below-floor (excluded) when the
    # filter is active — the snapshot defines the curated liquid
    # universe.
    min_adtv = getattr(filter_obj, "min_adtv_inr", None)
    pre_adtv_count = len(filtered)
    if min_adtv is not None and min_adtv > 0:
        snapshot_adtv = _load_snapshot_adtv()
        if snapshot_adtv:
            filtered = [
                t for t in filtered
                if snapshot_adtv.get(t, 0.0) >= min_adtv
            ]
        else:
            _logger.warning(
                "min_adtv_inr=%s set but universe_snapshot is "
                "empty — ADTV filter skipped",
                min_adtv,
            )

    # ASETPLTFRM — F&O 200 intersect for MIS strategies. Applied
    # after the ADTV floor so an F&O-restricted strategy with
    # min_adtv_inr also honors the liquidity floor.
    is_fno = bool(getattr(filter_obj, "is_fno", False))
    if is_fno:
        from backend.algo.research.intraday_15m_mis_bakeoff.universe import (
            load_fno_universe,
        )
        fno_set = set(load_fno_universe())
        before = len(filtered)
        filtered = [t for t in filtered if t in fno_set]
        _logger.info(
            "resolve_universe is_fno=True: %d -> %d after F&O intersect",
            before, len(filtered),
        )

    _logger.info(
        "resolve_universe scope=%s filter=(market=%s, "
        "ticker_type=%s, min_adtv_inr=%s) → %d candidates → "
        "%d after type/market filter → %d after ADTV filter",
        scope, raw_market, raw_types, min_adtv,
        len(candidates), pre_adtv_count, len(filtered),
    )
    return filtered

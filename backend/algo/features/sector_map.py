"""Equity ticker → sector intraday index lookup (FE-8).

Bridges ``stocks.company_info.sector`` (free-text sector names
like ``"IT"``, ``"Banks"``, ``"Pharmaceutical"``) to the Kite index
tradingsymbol stored in ``stocks.index_intraday_bars`` (e.g.
``"NIFTY IT"``, ``"NIFTY BANK"``).

The factor library at
``backend/algo/factors/compute_job.py::SECTOR_INDEX_MAP`` does the
same translation but for the DAILY factor surface, using Yahoo
notation (``^CNXIT`` etc.). FE-8 is the intraday-feature companion
using Kite notation (``"NIFTY IT"``), so the two stay parallel and
independent.

Tickers whose sector cannot be resolved (NULL sector, sector not
in the map below, or no row in ``stocks.company_info``) are simply
absent from
:func:`build_ticker_to_sector_index_map`'s return. Callers treat
absence as "rs_vs_sector_15m is not emittable for this ticker" per
the FE-8 skip-emission contract.
"""

from __future__ import annotations

import logging

from backend.algo.jobs._index_universe import INDEX_UNIVERSE

_logger = logging.getLogger(__name__)


# Free-text ``stocks.company_info.sector`` value → Kite index
# tradingsymbol from ``stocks.index_intraday_bars``. Multiple
# free-text variants can map to the same index — yfinance + NSE
# return slightly different casing / phrasing for the same sector.
# Keep this list flat (no aliases dict) so the lookup is one
# dict.get() call.
SECTOR_NAME_TO_INDEX: dict[str, str] = {
    "IT": "NIFTY IT",
    "Information Technology": "NIFTY IT",
    "Technology": "NIFTY IT",
    "Banks": "NIFTY BANK",
    "Banking": "NIFTY BANK",
    "Bank": "NIFTY BANK",
    "Auto": "NIFTY AUTO",
    "Automobile": "NIFTY AUTO",
    "Automotive": "NIFTY AUTO",
    "Pharma": "NIFTY PHARMA",
    "Pharmaceutical": "NIFTY PHARMA",
    "Pharmaceuticals": "NIFTY PHARMA",
    "Healthcare": "NIFTY PHARMA",
    "FMCG": "NIFTY FMCG",
    "Consumer Goods": "NIFTY FMCG",
    "Consumer Defensive": "NIFTY FMCG",
    "Metals": "NIFTY METAL",
    "Metal": "NIFTY METAL",
    "Basic Materials": "NIFTY METAL",
    "Energy": "NIFTY ENERGY",
    "Oil & Gas": "NIFTY ENERGY",
    "Realty": "NIFTY REALTY",
    "Real Estate": "NIFTY REALTY",
    "Financial Services": "NIFTY FIN SERVICE",
    "Financials": "NIFTY FIN SERVICE",
}


def _validate_map_against_universe() -> None:
    """Defence-in-depth: every Kite index name in the map must be
    present in :data:`INDEX_UNIVERSE` — otherwise the FE-6 keeper
    will never backfill it and the FE-8 cohort pass will silently
    skip ``rs_vs_sector_15m`` for every mapped ticker.
    """
    universe = set(INDEX_UNIVERSE)
    unknown = {v for v in SECTOR_NAME_TO_INDEX.values() if v not in universe}
    if unknown:
        _logger.warning(
            "[sector-map] indices not in INDEX_UNIVERSE — "
            "rs_vs_sector_15m will be silently absent: %s",
            sorted(unknown),
        )


_validate_map_against_universe()


def resolve_sector_index(sector: str | None) -> str | None:
    """Pure-string lookup: free-text sector → Kite index symbol.

    ``None`` / empty / unknown → ``None``. Strips whitespace so
    trailing-space artefacts from yfinance scrapes don't poison
    the result.
    """
    if not sector:
        return None
    key = str(sector).strip()
    if not key:
        return None
    return SECTOR_NAME_TO_INDEX.get(key)


async def build_ticker_to_sector_index_map(
    tickers: list[str],
) -> dict[str, str]:
    """Resolve equity tickers → Kite sector index tradingsymbol.

    Reads ``stocks.company_info`` via the shared
    :class:`stocks.repository.StockRepository.get_company_info_batch`
    (single Iceberg scan, deduped to latest ``fetched_at`` per
    ticker). Then maps the free-text ``sector`` value through
    :func:`resolve_sector_index`.

    Tickers without a mapped sector (NULL sector, sector not in
    :data:`SECTOR_NAME_TO_INDEX`, or ticker absent from
    ``stocks.company_info``) are not present in the result dict.
    Callers treat absence as "``rs_vs_sector_15m`` is not emittable
    for this ticker".

    The ``session`` parameter is accepted for symmetry with the
    rest of the daily-compute pipeline but is unused — the
    repository drives its own DuckDB / PyIceberg session because
    ``stocks.company_info`` lives in Iceberg, not PG.
    """
    if not tickers:
        return {}
    from stocks.repository import StockRepository

    repo = StockRepository()
    try:
        df = repo.get_company_info_batch([t.upper() for t in tickers])
    except Exception as exc:  # noqa: BLE001
        # company_info read failure is non-fatal — the FE-8
        # rs_vs_sector_15m feature will simply be absent for every
        # ticker this batch. Log with exc_info per CLAUDE.md §4.2
        # so long-running jobs don't silently drop signal.
        _logger.error(
            "[sector-map] company_info read failed for %d "
            "tickers: %s",
            len(tickers),
            exc,
            exc_info=True,
        )
        return {}
    if df is None or df.empty or "sector" not in df.columns:
        return {}
    out: dict[str, str] = {}
    for _idx, row in df.iterrows():
        ticker = row.get("ticker")
        if not ticker:
            continue
        idx = resolve_sector_index(row.get("sector"))
        if idx is None:
            continue
        out[str(ticker)] = idx
    return out

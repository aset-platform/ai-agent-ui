"""Tests for :mod:`backend.algo.features.sector_map`.

Covers the synchronous ``resolve_sector_index`` lookup table and
the async ``build_ticker_to_sector_index_map`` integration with
``stocks.company_info``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.algo.features.sector_map import (
    SECTOR_NAME_TO_INDEX,
    build_ticker_to_sector_index_map,
    resolve_sector_index,
)
from backend.algo.jobs._index_universe import INDEX_UNIVERSE


def test_known_sector_names_resolve_to_universe_member():
    """Every Kite index in ``SECTOR_NAME_TO_INDEX`` must exist in
    ``INDEX_UNIVERSE``; otherwise FE-6 won't ingest it and FE-8
    silently drops ``rs_vs_sector_15m``."""
    universe = set(INDEX_UNIVERSE)
    for sector, idx in SECTOR_NAME_TO_INDEX.items():
        assert idx in universe, (
            f"Mapped index '{idx}' for sector '{sector}' is not "
            f"in INDEX_UNIVERSE — FE-6 won't ingest it"
        )


def test_unknown_sector_returns_none():
    """A sector value not in the map → ``None``."""
    assert resolve_sector_index("Telecom") is None
    assert resolve_sector_index("SomeNewSector") is None


def test_null_or_empty_sector_returns_none():
    """``None`` / empty / whitespace-only → ``None``."""
    assert resolve_sector_index(None) is None
    assert resolve_sector_index("") is None
    assert resolve_sector_index("   ") is None


def test_resolve_strips_whitespace():
    """Trailing whitespace from yfinance scrapes is tolerated."""
    assert resolve_sector_index("  IT  ") == "NIFTY IT"


@pytest.mark.asyncio
async def test_build_map_filters_unmapped_and_null_sectors():
    """End-to-end build: mocked ``get_company_info_batch`` returns
    a DataFrame with mixed sectors; the result dict only contains
    tickers whose sector resolves through the map."""
    df = pd.DataFrame(
        [
            {"ticker": "INFY.NS", "sector": "IT"},
            {"ticker": "TCS.NS", "sector": "Technology"},
            {"ticker": "RELIANCE.NS", "sector": "Energy"},
            {"ticker": "AIRTEL.NS", "sector": "Telecom"},  # unmapped
            {"ticker": "NULLSECT.NS", "sector": None},  # NULL sector
        ]
    )
    fake_repo = MagicMock()
    fake_repo.get_company_info_batch.return_value = df
    with patch(
        "stocks.repository.StockRepository",
        return_value=fake_repo,
    ):
        result = await build_ticker_to_sector_index_map(
            [
                "INFY.NS",
                "TCS.NS",
                "RELIANCE.NS",
                "AIRTEL.NS",
                "NULLSECT.NS",
            ],
        )
    assert result == {
        "INFY.NS": "NIFTY IT",
        "TCS.NS": "NIFTY IT",
        "RELIANCE.NS": "NIFTY ENERGY",
    }


@pytest.mark.asyncio
async def test_build_map_empty_input_short_circuits():
    """Empty ticker list → empty result, no repo call."""
    fake_repo = MagicMock()
    with patch(
        "stocks.repository.StockRepository",
        return_value=fake_repo,
    ):
        result = await build_ticker_to_sector_index_map([])
    assert result == {}
    fake_repo.get_company_info_batch.assert_not_called()


@pytest.mark.asyncio
async def test_build_map_repo_failure_returns_empty():
    """Repo crash is caught + logged; result is empty so FE-8's
    ``rs_vs_sector_15m`` simply goes absent for the batch."""
    fake_repo = MagicMock()
    fake_repo.get_company_info_batch.side_effect = RuntimeError(
        "iceberg blip",
    )
    with patch(
        "stocks.repository.StockRepository",
        return_value=fake_repo,
    ):
        result = await build_ticker_to_sector_index_map(["INFY.NS"])
    assert result == {}


@pytest.mark.asyncio
async def test_build_map_missing_sector_column_returns_empty():
    """If company_info returns a DataFrame without a ``sector``
    column (shouldn't happen in prod but be defensive), result is
    empty rather than KeyError-crashing the batch."""
    df = pd.DataFrame([{"ticker": "INFY.NS"}])  # no sector col
    fake_repo = MagicMock()
    fake_repo.get_company_info_batch.return_value = df
    with patch(
        "stocks.repository.StockRepository",
        return_value=fake_repo,
    ):
        result = await build_ticker_to_sector_index_map(["INFY.NS"])
    assert result == {}

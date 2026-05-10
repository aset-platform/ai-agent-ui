"""GET /v1/algo/factors/* — exposes daily factor cache.

Endpoints:
  * ``GET /v1/algo/factors/{ticker}``                — latest row for ticker
  * ``GET /v1/algo/factors?tickers=A,B,C``           — bulk latest, sorted

Cache TTLs (per CLAUDE.md §5.13):
  * single-ticker — TTL_STABLE (300s, key cache:factors:{ticker})
  * bulk          — TTL_STABLE (300s, key cache:factors:bulk:<sha-of-list>)
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.dependencies import get_current_user
from auth.models.response import UserContext
from backend.algo.factors.iceberg_init import ALL_FACTOR_KEYS
from backend.algo.factors.repo import FactorRow, get_factors_window
from cache import TTL_STABLE, get_cache

_logger = logging.getLogger(__name__)

# How far back we look to find "the latest" row per ticker. Factor
# compute_job runs nightly so 14 days covers any reasonable gap.
LATEST_LOOKBACK_DAYS = 14


class FactorScoreResponse(BaseModel):
    ticker: str
    bar_date: date
    sector: str | None
    values: dict[str, float]


def _row_to_response(row: FactorRow) -> FactorScoreResponse:
    return FactorScoreResponse(
        ticker=row.ticker,
        bar_date=row.bar_date,
        sector=row.sector,
        values=row.values,
    )


def _latest_per_ticker(rows: list[FactorRow]) -> list[FactorRow]:
    """get_factors_window returns rows sorted by ticker, bar_date.
    The last row per ticker is therefore the latest. We collapse
    via dict so the second pass is O(N)."""
    by_ticker: dict[str, FactorRow] = {}
    for r in rows:
        by_ticker[r.ticker] = r  # last write wins (sorted ASC)
    return sorted(by_ticker.values(), key=lambda r: r.ticker)


def create_factors_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/factors", tags=["algo-trading"],
    )

    @router.get(
        "/{ticker}", response_model=FactorScoreResponse,
    )
    def get_one(
        ticker: str,
        _user: UserContext = Depends(get_current_user),
    ) -> FactorScoreResponse:
        cache = get_cache()
        key = f"cache:factors:{ticker}"
        cached = cache.get(key)
        if cached:
            return FactorScoreResponse(**json.loads(cached))

        end = date.today()
        start = end - timedelta(days=LATEST_LOOKBACK_DAYS)
        rows = get_factors_window([ticker], start, end)
        latest = _latest_per_ticker(rows)
        if not latest:
            raise HTTPException(
                404, f"No factor data for {ticker}",
            )
        resp = _row_to_response(latest[0])
        cache.set(key, resp.model_dump_json(), ttl=TTL_STABLE)
        return resp

    @router.get(
        "", response_model=list[FactorScoreResponse],
    )
    def get_bulk(
        tickers: str = Query(
            ..., description="comma-separated tickers",
        ),
        _user: UserContext = Depends(get_current_user),
    ) -> list[FactorScoreResponse]:
        ticker_list = sorted({
            t.strip() for t in tickers.split(",") if t.strip()
        })
        if not ticker_list:
            raise HTTPException(400, "tickers query param required")
        if len(ticker_list) > 200:
            raise HTTPException(400, "max 200 tickers per request")

        # Cache key keyed on the sorted list (sha-1 to keep short)
        sig = hashlib.sha1(
            ",".join(ticker_list).encode("utf-8"),
        ).hexdigest()[:12]
        key = f"cache:factors:bulk:{sig}"
        cache = get_cache()
        cached_str = cache.get(key)
        if cached_str:
            payload = json.loads(cached_str)
            return [FactorScoreResponse(**r) for r in payload]

        end = date.today()
        start = end - timedelta(days=LATEST_LOOKBACK_DAYS)
        rows = get_factors_window(ticker_list, start, end)
        latest = _latest_per_ticker(rows)
        responses = [_row_to_response(r) for r in latest]
        cache.set(
            key,
            json.dumps([r.model_dump(mode="json") for r in responses]),
            ttl=TTL_STABLE,
        )
        return responses

    return router


__all__ = ["create_factors_router", "FactorScoreResponse"]

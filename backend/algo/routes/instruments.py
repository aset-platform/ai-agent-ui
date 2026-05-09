"""GET /v1/algo/instruments  + POST /v1/algo/instruments/refresh."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.instruments.loader import run_instruments_refresh
from backend.algo.instruments.repo import InstrumentsRepo

_logger = logging.getLogger(__name__)


def _get_session_factory():
    from backend.db.engine import get_session_factory
    return get_session_factory()


class InstrumentRow(BaseModel):
    instrument_token: int
    tradingsymbol: str
    exchange: str
    segment: str
    lot_size: int
    tick_size: float
    our_ticker: str | None
    loaded_at: str | None = None


class InstrumentsResponse(BaseModel):
    rows: list[InstrumentRow]
    total: int
    page: int
    page_size: int


class RefreshResponse(BaseModel):
    instruments_loaded: int
    skipped: bool = False


def create_instruments_router() -> APIRouter:
    router = APIRouter(
        prefix="/algo/instruments", tags=["algo-trading"],
    )
    repo = InstrumentsRepo()

    @router.get("", response_model=InstrumentsResponse)
    async def list_(
        user: UserContext = Depends(pro_or_superuser),
        search: str = Query("", max_length=64),
        exchange: str = Query(
            "", pattern="^(|NSE|BSE|NFO|BFO|MCX|CDS)$",
        ),
        segment: str = Query("", max_length=32),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ) -> InstrumentsResponse:
        factory = _get_session_factory()
        offset = (page - 1) * page_size
        async with factory() as session:
            rows = await repo.list_instruments(
                session,
                search=search or None,
                exchange=exchange or None,
                segment=segment or None,
                limit=page_size,
                offset=offset,
            )
            total = await repo.count_instruments(
                session,
                search=search or None,
                exchange=exchange or None,
                segment=segment or None,
            )
        return InstrumentsResponse(
            rows=[
                InstrumentRow(
                    instrument_token=r["instrument_token"],
                    tradingsymbol=r["tradingsymbol"],
                    exchange=r["exchange"],
                    segment=r["segment"],
                    lot_size=r["lot_size"],
                    tick_size=float(r["tick_size"]),
                    our_ticker=r.get("our_ticker"),
                    loaded_at=(
                        r["loaded_at"].isoformat()
                        if r.get("loaded_at") else None
                    ),
                )
                for r in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    @router.post("/refresh", response_model=RefreshResponse)
    async def refresh(
        user: UserContext = Depends(pro_or_superuser),
    ) -> RefreshResponse:
        try:
            result = await run_instruments_refresh()
        except Exception as exc:
            _logger.exception(
                "manual instruments refresh failed: %s", exc,
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    "Failed to refresh from Kite "
                    "— check broker connection."
                ),
            )
        return RefreshResponse(
            instruments_loaded=result.get("instruments_loaded", 0),
            skipped=result.get("skipped", False),
        )

    return router

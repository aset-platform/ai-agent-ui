"""GET /v1/algo/fees/preview — returns a FeeBreakdown for a
synthetic trade. Used by the Settings tab preview widget."""
from __future__ import annotations

import logging
from datetime import date as date_cls
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.dependencies import pro_or_superuser
from auth.models import UserContext
from backend.algo.fees import FeeBreakdown, IndianFeeModel, Trade

_logger = logging.getLogger(__name__)


def create_fees_router() -> APIRouter:
    router = APIRouter(prefix="/algo/fees", tags=["algo-trading"])

    @router.get(
        "/preview",
        response_model=FeeBreakdown,
        name="algo_fees_preview",
    )
    async def preview(
        user: UserContext = Depends(pro_or_superuser),
        symbol: str = Query("RELIANCE", min_length=1, max_length=64),
        exchange: str = Query("NSE", pattern="^(NSE|BSE)$"),
        side: str = Query("BUY", pattern="^(BUY|SELL)$"),
        product: str = Query(
            "DELIVERY", pattern="^(DELIVERY|INTRADAY)$",
        ),
        qty: int = Query(10, ge=0, le=10_000_000),
        price: Decimal = Query(
            Decimal("100.00"), ge=Decimal("0"), le=Decimal("10000000"),
        ),
    ) -> FeeBreakdown:
        try:
            t = Trade(
                symbol=symbol,
                exchange=exchange,  # type: ignore[arg-type]
                side=side,  # type: ignore[arg-type]
                product=product,  # type: ignore[arg-type]
                qty=qty,
                price=price,
            )
            model = IndianFeeModel(as_of=date_cls.today())
            return model.compute(t)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            _logger.exception("algo_fees_preview failed: %s", exc)
            raise HTTPException(
                status_code=500,
                detail="algo fees preview failed",
            )

    return router

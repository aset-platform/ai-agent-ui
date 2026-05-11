"""Live LTP batch endpoint — Redis-first with OHLCV fallback.

Read side of the eager-WS-subscription pipeline:

  KiteWsMultiplexer.on_ticks → cache:ltp:{ticker}  (60s TTL)
  PaperRuntime._on_bar_close → cache:ltp:{ticker}  (60s TTL)
  LiveRuntime._on_bar_close  → cache:ltp:{ticker}  (60s TTL)

This endpoint resolves a batch of tickers to current prices,
falling back to stocks.ohlcv close when no live tick is cached.
The frontend portfolio holdings table + dashboard widgets poll
this every 5s during market hours (60s otherwise).

Per-call cap: 200 tickers. Larger callers should batch.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.dependencies import get_current_user
from auth.models import UserContext

_logger = logging.getLogger(__name__)

# Cap protects Redis pipeline + DuckDB scan. With ~800 NSE
# tickers in the universe, even worst-case "show me everything"
# fits in 4 batches.
MAX_TICKERS_PER_CALL = 200


def create_ltp_router() -> APIRouter:
    router = APIRouter(prefix="/algo/ltp", tags=["algo-trading"])

    @router.get("/batch")
    async def batch(
        tickers: str = Query(
            ...,
            description=(
                "Comma-separated list of tickers, e.g. "
                "RELIANCE.NS,TCS.NS,^NSEI. Max 200 per call."
            ),
        ),
        # Auth: any logged-in user can read public market prices.
        # We don't gate this on `pro_or_superuser` — portfolio &
        # dashboard widgets need it for general users too.
        user: UserContext = Depends(get_current_user),
    ) -> dict[str, Any]:
        """Return ``{ticker: {price, source, age_seconds}}`` for
        the requested tickers.

        - ``source='live_ltp'``: Redis cache hit (sub-60s old)
        - ``source='ohlcv_close'``: end-of-day close fallback
        - ``source='unknown'``: ticker absent from both layers

        Off-market hours every entry will say ``ohlcv_close``
        because the WS multiplexer doesn't get ticks. The
        endpoint never errors on individual misses — bad
        tickers just come back with ``source='unknown'``.
        """
        from backend.cache import get_cache
        from backend.db.duckdb_engine import query_iceberg_table

        ticker_list = [
            t.strip() for t in tickers.split(",") if t.strip()
        ]
        # De-dupe while preserving order — callers occasionally
        # send the same ticker twice (e.g. holdings + watchlist).
        seen: set[str] = set()
        deduped: list[str] = []
        for t in ticker_list:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        ticker_list = deduped
        if not ticker_list:
            return {}
        if len(ticker_list) > MAX_TICKERS_PER_CALL:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Too many tickers: {len(ticker_list)} > "
                    f"{MAX_TICKERS_PER_CALL}. Batch on the "
                    f"client side."
                ),
            )

        try:
            cache = get_cache()
        except Exception:  # noqa: BLE001
            cache = None

        # First pass — Redis. One pipeline call regardless of N.
        live_hits: dict[str, float] = {}
        if cache is not None:
            for t in ticker_list:
                try:
                    raw = cache.get(f"cache:ltp:{t}")
                except Exception:  # noqa: BLE001
                    raw = None
                if raw is not None:
                    try:
                        live_hits[t] = float(raw)
                    except (TypeError, ValueError):
                        pass

        # Second pass — DuckDB OHLCV fallback for whatever's
        # missing from the live cache. Single bulk read with
        # WHERE ticker IN (...) per CLAUDE.md §4.1 #1.
        missing = [t for t in ticker_list if t not in live_hits]
        eod_hits: dict[str, float] = {}
        if missing:
            placeholders = ",".join(["?"] * len(missing))
            try:
                rows = query_iceberg_table(
                    "stocks.ohlcv",
                    f"SELECT ticker, close FROM ("
                    f"  SELECT ticker, close, "
                    f"    ROW_NUMBER() OVER ("
                    f"      PARTITION BY ticker ORDER BY date DESC"
                    f"    ) AS rn "
                    f"  FROM ohlcv "
                    f"  WHERE ticker IN ({placeholders})"
                    f") WHERE rn = 1",
                    missing,
                )
                for r in rows:
                    if r.get("close") is not None:
                        try:
                            eod_hits[r["ticker"]] = float(
                                r["close"],
                            )
                        except (TypeError, ValueError):
                            pass
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "ltp/batch ohlcv fallback failed: %s", exc,
                )

        # Compose response — preserve input order.
        out: dict[str, Any] = {}
        for t in ticker_list:
            if t in live_hits:
                out[t] = {
                    "price": live_hits[t],
                    "source": "live_ltp",
                }
            elif t in eod_hits:
                out[t] = {
                    "price": eod_hits[t],
                    "source": "ohlcv_close",
                }
            else:
                out[t] = {
                    "price": None,
                    "source": "unknown",
                }
        return out

    return router

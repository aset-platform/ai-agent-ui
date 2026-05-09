"""Async list/upsert over ``algo.instruments``.

The Kite ``/instruments`` endpoint returns ~80 000 rows per
exchange — we bulk-upsert with ``ON CONFLICT (instrument_token)
DO UPDATE`` so the daily refresh idempotently re-syncs.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_logger = logging.getLogger(__name__)


class InstrumentsRepo:
    async def list_instruments(
        self,
        session: AsyncSession,
        *,
        search: str | None = None,
        exchange: str | None = None,
        segment: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            clauses.append(
                "(tradingsymbol ILIKE :needle "
                "OR our_ticker ILIKE :needle)"
            )
            params["needle"] = f"%{search}%"
        if exchange:
            clauses.append("exchange = :exchange")
            params["exchange"] = exchange.upper()
        if segment:
            clauses.append("segment = :segment")
            params["segment"] = segment
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = (
            await session.execute(
                text(
                    f"SELECT instrument_token, tradingsymbol, exchange, "
                    f"segment, lot_size, tick_size, our_ticker, "
                    f"loaded_at "
                    f"FROM algo.instruments "
                    f"{where} "
                    f"ORDER BY tradingsymbol "
                    f"LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def count_instruments(
        self,
        session: AsyncSession,
        *,
        search: str | None = None,
        exchange: str | None = None,
        segment: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if search:
            clauses.append(
                "(tradingsymbol ILIKE :needle "
                "OR our_ticker ILIKE :needle)"
            )
            params["needle"] = f"%{search}%"
        if exchange:
            clauses.append("exchange = :exchange")
            params["exchange"] = exchange.upper()
        if segment:
            clauses.append("segment = :segment")
            params["segment"] = segment
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        row = (
            await session.execute(
                text(
                    f"SELECT COUNT(*) AS c "
                    f"FROM algo.instruments {where}"
                ),
                params,
            )
        ).mappings().first()
        return int(row["c"]) if row else 0

    async def get_tokens_for_tickers(
        self,
        session: AsyncSession,
        tickers: list[str],
    ) -> dict[int, str]:
        """Return {instrument_token: our_ticker} for the given tickers.

        Looks up by ``our_ticker`` column (NSE-style e.g. ``RELIANCE.NS``).
        Tickers not found in the instruments table are silently omitted.
        """
        if not tickers:
            return {}
        rows = (
            await session.execute(
                text(
                    "SELECT instrument_token, our_ticker "
                    "FROM algo.instruments "
                    "WHERE our_ticker = ANY(:tickers) "
                    "AND our_ticker IS NOT NULL"
                ),
                {"tickers": tickers},
            )
        ).mappings().all()
        return {
            int(r["instrument_token"]): r["our_ticker"]
            for r in rows
        }

    async def bulk_upsert(
        self,
        session: AsyncSession,
        rows: list[dict[str, Any]],
    ) -> int:
        """Insert-or-update a batch of instrument rows.

        Each row must have at least: instrument_token, tradingsymbol,
        exchange, segment, lot_size, tick_size. ``our_ticker`` is
        optional — soft-linked when populated.
        """
        if not rows:
            return 0
        from datetime import datetime, timezone
        loaded_at = datetime.now(timezone.utc)
        for r in rows:
            await session.execute(
                text(
                    "INSERT INTO algo.instruments "
                    "(instrument_token, tradingsymbol, exchange, "
                    " segment, lot_size, tick_size, our_ticker, "
                    " loaded_at) "
                    "VALUES (:instrument_token, :tradingsymbol, "
                    "        :exchange, :segment, :lot_size, "
                    "        :tick_size, :our_ticker, :loaded_at) "
                    "ON CONFLICT (instrument_token) DO UPDATE SET "
                    "  tradingsymbol = EXCLUDED.tradingsymbol, "
                    "  exchange = EXCLUDED.exchange, "
                    "  segment = EXCLUDED.segment, "
                    "  lot_size = EXCLUDED.lot_size, "
                    "  tick_size = EXCLUDED.tick_size, "
                    "  our_ticker = EXCLUDED.our_ticker, "
                    "  loaded_at = EXCLUDED.loaded_at"
                ),
                {
                    "instrument_token": r["instrument_token"],
                    "tradingsymbol": r["tradingsymbol"],
                    "exchange": r["exchange"],
                    "segment": r["segment"],
                    "lot_size": int(r.get("lot_size") or 1),
                    "tick_size": float(r.get("tick_size") or 0.05),
                    "our_ticker": r.get("our_ticker"),
                    "loaded_at": loaded_at,
                },
            )
        await session.commit()
        return len(rows)

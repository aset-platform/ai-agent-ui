"""Eager NSE universe subscriber for the Kite WS multiplexer.

Loaded by the OAuth-callback path so a Kite-connected user gets
``cache:ltp:{ticker}`` keys populated for the FULL NSE-relevant
universe (stocks + indices + ETFs) within seconds of connecting.

Consumers (portfolio holdings table, dashboard widgets,
recommendations) then read live LTPs via /v1/algo/ltp/batch.

Off-market hours the universe stays subscribed but Kite simply
doesn't emit ticks — the 60s Redis TTL drains and consumers see
``mark_source='ohlcv_close'`` automatically.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from backend.algo.broker.ws_multiplexer import KiteWsMultiplexer
from backend.algo.instruments.repo import InstrumentsRepo

_logger = logging.getLogger(__name__)

# Caps: Kite WS allows ~3000 instruments per connection. NSE
# stock_master is ~800; we keep room for indices + ETFs without
# bumping the cap.
_MAX_UNIVERSE_TOKENS = 1500


async def _load_universe_tickers(session) -> list[str]:
    """Return the union of NSE-suffix tickers + standard
    indices from PG. We *don't* include futures/options strikes —
    too many tokens, low value for portfolio views.
    """
    rows = (
        await session.execute(
            text(
                "SELECT yf_ticker FROM stock_master "
                "WHERE yf_ticker LIKE '%.NS' "
                "  OR yf_ticker LIKE '^%' "
                "ORDER BY yf_ticker"
            ),
        )
    ).mappings().all()
    return [r["yf_ticker"] for r in rows]


async def seed_full_nse_universe(
    mux: KiteWsMultiplexer,
    session_factory,
) -> int:
    """Look up Kite tokens for every NSE stock + index in PG and
    register them with the multiplexer's universe subscription.

    Returns the number of NEW tokens added (excluding ones the
    multiplexer was already tracking). Idempotent.

    Best-effort: any DB/Kite error logs a warning and returns 0
    rather than failing the OAuth callback.
    """
    try:
        async with session_factory() as session:
            tickers = await _load_universe_tickers(session)
            if not tickers:
                _logger.warning(
                    "seed_full_nse_universe: empty stock_master "
                    "→ skipping universe subscription",
                )
                return 0
            repo = InstrumentsRepo()
            token_to_ticker = await repo.get_tokens_for_tickers(
                session, tickers,
            )
        if not token_to_ticker:
            _logger.warning(
                "seed_full_nse_universe: 0 Kite tokens "
                "resolved for %d PG tickers — instruments "
                "table may need a refresh",
                len(tickers),
            )
            return 0
        if len(token_to_ticker) > _MAX_UNIVERSE_TOKENS:
            _logger.warning(
                "seed_full_nse_universe: resolved %d tokens "
                "(> cap %d) — clipping to first %d",
                len(token_to_ticker), _MAX_UNIVERSE_TOKENS,
                _MAX_UNIVERSE_TOKENS,
            )
            clipped = dict(
                list(token_to_ticker.items())[
                    :_MAX_UNIVERSE_TOKENS
                ],
            )
            token_to_ticker = clipped
        added = mux.subscribe_universe(token_to_ticker)
        _logger.info(
            "seed_full_nse_universe: registered %d/%d tokens "
            "user=%s (PG tickers=%d)",
            added, len(token_to_ticker), mux._user_id,
            len(tickers),
        )
        return added
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "seed_full_nse_universe failed: %s — "
            "live LTP cache will populate lazily as strategies "
            "subscribe individual tokens", exc, exc_info=True,
        )
        return 0


def seed_full_nse_universe_background(
    mux: KiteWsMultiplexer,
    session_factory,
) -> asyncio.Task:
    """Fire-and-forget variant for the OAuth callback hot path.

    The callback returns to the user in <100ms while the universe
    seeding runs concurrently (one PG query + one Kite subscribe
    call — typically ~1-2s end-to-end).
    """
    return asyncio.create_task(
        seed_full_nse_universe(mux, session_factory),
    )

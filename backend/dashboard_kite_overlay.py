"""Live Kite OHLC overlay for /v1/dashboard/chart/ohlcv.

Splices today's running OHLC + volume from the user's linked
Kite account onto the last bar of the yfinance-sourced series.
Used only during NSE market hours; outside hours the overlay is
skipped and the existing yfinance flow runs unchanged.

The module is intentionally separate from ``dashboard_routes.py``
to keep that already-large file from growing further and to make
the overlay logic independently testable.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from algo.broker.credentials_repo import BrokerCredentialsRepo
from algo.broker.kite_client import KiteClient
from algo.instruments.repo import InstrumentsRepo
from db.engine import disposable_pg_session

_logger = logging.getLogger(__name__)


async def _try_kite_quote(
    user, ticker: str,
) -> dict | None:
    """Return today's Kite bar for ``ticker``, or ``None`` on any failure.

    Silent fallback by design — Kite hiccups must never break the
    chart. Logged at WARNING (with ``exc_info=False``) on Kite SDK
    errors so outages are visible without spamming.
    """
    user_id = user.user_id
    try:
        async with disposable_pg_session() as session:
            creds_repo = BrokerCredentialsRepo()
            creds = await creds_repo.load(session, user_id)
            if creds is None:
                return None
            if creds.get("access_token") is None:
                return None
            if creds.get("access_token_expired"):
                return None

            inst_repo = InstrumentsRepo()
            tokens = await inst_repo.get_tokens_for_tickers(
                session, [ticker],
            )
            # tokens is {instrument_token: our_ticker} — reverse
            # lookup to find the token for our ticker.
            instrument_token = next(
                (
                    tok
                    for tok, t in tokens.items()
                    if t == ticker
                ),
                None,
            )
            if instrument_token is None:
                return None

        client = KiteClient(
            api_key=creds["api_key"],
            access_token=creds["access_token"],
            user_id=user_id,
        )
        result = client.quote(
            [(ticker, instrument_token)],
        )
        return result.get(ticker)
    except Exception as exc:  # noqa: BLE001 — silent fallback by design
        _logger.warning(
            "kite quote failed user=%s ticker=%s: %s",
            user_id, ticker, exc,
        )
        return None


def _splice_today_bar(
    df: pd.DataFrame,
    quote: dict,
    today: date,
) -> pd.DataFrame:
    """Overlay today's running OHLCV from a Kite quote.

    If ``df.iloc[-1].date == today`` (yfinance already has a partial
    bar): overwrite that row's open/high/low/close/volume.
    Otherwise (yfinance hasn't refreshed today yet): append a new
    row sorted by date.

    Sets ``close`` to ``quote["last_price"]`` (the running close,
    unambiguous semantics) rather than ``quote["close"]`` which is
    the prior-day close during pre-market.

    Returns a new DataFrame; the input is not mutated.
    """
    out = df.copy()
    new_row = {
        "date": today,
        "open": float(quote["open"]),
        "high": float(quote["high"]),
        "low": float(quote["low"]),
        "close": float(quote["last_price"]),
        "volume": int(quote["volume"]),
    }
    if not out.empty and out.iloc[-1]["date"] == today:
        last_idx = out.index[-1]
        for col, val in new_row.items():
            out.at[last_idx, col] = val
        return out
    return pd.concat(
        [out, pd.DataFrame([new_row])],
        ignore_index=True,
    )

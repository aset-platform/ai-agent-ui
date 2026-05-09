"""Gap-fill helper: pull missing 1m OHLCV bars from Kite historical
API and convert them to Tick objects for replay into subscriber
queues.

Called from KiteWsMultiplexer._run_gap_fill() via asyncio.to_thread
so the synchronous kiteconnect SDK call doesn't block the event loop.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from backend.algo.stream.types import Tick

_logger = logging.getLogger(__name__)


def gap_fill_token(
    *,
    api_key: str,
    access_token: str,
    token: int,
    ticker: str,
    last_ns: int,
    now_ns: int,
) -> list[Tick]:
    """Fetch 1-minute historical bars from Kite for *token* covering
    the window [last_ns, now_ns).

    Returns a list of synthetic Ticks (one per bar close) in
    chronological order.  Returns empty list on any error.

    Args:
        api_key: Kite API key.
        access_token: Active access token.
        token: Instrument token.
        ticker: Human-readable ticker string (for Tick.ticker).
        last_ns: Nanosecond timestamp of last tick received.
        now_ns: Nanosecond timestamp of reconnect moment.
    """
    from kiteconnect import KiteConnect

    kc = KiteConnect(api_key=api_key)
    kc.set_access_token(access_token)

    from_dt = datetime.fromtimestamp(
        last_ns / 1_000_000_000, tz=timezone.utc,
    )
    to_dt = datetime.fromtimestamp(
        now_ns / 1_000_000_000, tz=timezone.utc,
    )

    try:
        bars = kc.historical_data(
            token,
            from_dt.strftime("%Y-%m-%d %H:%M:%S"),
            to_dt.strftime("%Y-%m-%d %H:%M:%S"),
            interval="minute",
            continuous=False,
            oi=False,
        )
    except Exception:
        _logger.warning(
            "gap_fill_token: historical_data failed token=%s",
            token, exc_info=True,
        )
        return []

    ticks: list[Tick] = []
    for bar in bars:
        try:
            bar_dt = bar.get("date")
            if bar_dt is None:
                continue
            if hasattr(bar_dt, "timestamp"):
                ts_ns = int(bar_dt.timestamp() * 1_000_000_000)
            else:
                ts_ns = int(time.time() * 1_000_000_000)
            close = float(bar.get("close", 0) or 0)
            volume = int(bar.get("volume", 0) or 0)
            ticks.append(
                Tick(ticker=ticker, ts_ns=ts_ns,
                     ltp=close, volume=volume),
            )
        except Exception:
            _logger.debug(
                "gap_fill_token: skipping bar %s", bar,
                exc_info=True,
            )
            continue

    _logger.debug(
        "gap_fill_token: token=%s ticks=%d", token, len(ticks),
    )
    return ticks

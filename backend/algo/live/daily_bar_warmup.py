"""Daily-bar warmup for LiveRuntime (ASETPLTFRM-383).

Bridges the cadence mismatch between Backtest and Live:
- Backtest evaluates strategies on 400+ daily bars from
  ``stocks.ohlcv`` — indicators (RSI, SMA, ...) are stable from
  the first user-period bar.
- Live (pre-383) accumulates only session-local 1-minute bars in
  ``_bars_by_ticker``. RSI(14) is None for the first 14 minutes,
  then computed on minute-tick velocity — wrong cadence for any
  strategy designed against a daily-bar series.

This module preloads 250 closed daily bars per ticker at
``LiveRuntime.__init__`` so the very first per-minute eval sees the
same indicator landscape the backtest saw. Today's still-running
bar is the caller's responsibility — see ``initial_running_bar`` /
``update_running_bar`` for the helpers ``runtime.py`` uses to keep
it in sync with the live LTP each minute.

Source priority for closed bars:
  1. ``stocks.ohlcv`` Iceberg table via the existing
     ``load_ohlcv_window`` bulk reader (one DuckDB query for the
     whole universe, NaN sanitisation already wired — CLAUDE.md
     §4.1 #1 + §6.1).
  2. ``KiteClient.fetch_daily_historical`` per-ticker fallback for
     any ticker whose Iceberg history looks stale (last bar more
     than ``MAX_STALENESS_DAYS`` calendar days old). Rate-limited
     inside the KiteClient method itself (3 req/sec).

Out of scope here: today's running bar (see runtime), eval-time
gate (see runtime), strategy-level ``bar_interval`` selection
(deferred — Option C).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from backend.algo.backtest.data_source import load_ohlcv_window
from backend.algo.backtest.types import BarData

_logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Closed-bar count we preload per ticker. 250 trading days ≈ 1
# calendar year and matches the backtest's SMA200 stability profile
# (indicator stable from bar ~200, leaving 50 settled bars before
# the running bar at index 249).
DEFAULT_WARMUP_BARS = 250

# Tolerated lag between today and the most recent Iceberg bar
# before we declare the ticker stale. 5 days covers normal Fri→Mon
# weekends and 1-2 day NSE holiday blocks without false-positive
# Kite fallbacks. Anything older indicates either a multi-day
# holiday or a broken nightly pipeline; the Kite fallback handles
# both correctly.
MAX_STALENESS_DAYS = 5


def _today_ist() -> date:
    return datetime.now(IST).date()


def _iceberg_window_start(n_bars: int, end: date) -> date:
    """Calendar window wide enough to cover ``n_bars`` trading days.

    Trading days ≈ 5/7 of calendar days; we add a 1.6× safety factor
    so a stretch of NSE holidays inside the window still leaves N
    bars after the read. Width is bounded below by 30 to keep small
    ``n_bars`` callers (e.g. tests) from underfilling.
    """
    width = max(int(n_bars * 1.6) + 5, 30)
    return end - timedelta(days=width)


def preload_daily_bars(
    tickers: list[str],
    *,
    n_bars: int = DEFAULT_WARMUP_BARS,
    kite_client: Any | None = None,
    ticker_to_token: dict[str, int] | None = None,
    today: date | None = None,
) -> dict[str, list[BarData]]:
    """Preload closed daily bars for ``tickers`` from Iceberg + Kite.

    Returns ``{ticker: list[BarData]}`` with up to ``n_bars`` bars
    per ticker, ascending by date. Closed bars only — today's
    running bar is appended later by the caller.

    Tickers absent from both sources fall through to an empty list
    in the returned dict; callers should treat that as "indicators
    silent-skip until backfill catches up" rather than as an error.

    Parameters
    ----------
    tickers : list[str]
        Universe to preload. Empty list → empty dict.
    n_bars : int
        Bars per ticker. Default 250.
    kite_client : KiteClient | None
        Used only for the per-ticker stale-fallback path. ``None``
        disables fallback (warning logged for stale tickers).
    ticker_to_token : dict[str, int] | None
        Map from our ticker → Kite instrument_token. Required for
        the Kite fallback path (Kite's ``historical_data`` keys by
        token, not symbol). ``None`` disables fallback regardless
        of ``kite_client``.
    today : date | None
        Inject for testing; defaults to today in IST.
    """
    if not tickers:
        return {}
    today = today or _today_ist()
    window_start = _iceberg_window_start(n_bars, today)
    stale_cutoff = today - timedelta(days=MAX_STALENESS_DAYS)

    iceberg: dict[str, list[BarData]] = {}
    try:
        iceberg = load_ohlcv_window(
            tickers=tickers,
            period_start=window_start,
            period_end=today,
            warmup_days=0,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "preload_daily_bars: Iceberg read failed: %s — every "
            "ticker falls back to Kite (if available)", exc,
        )

    out: dict[str, list[BarData]] = {}
    stale_tickers: list[str] = []
    for t in tickers:
        bars = iceberg.get(t, [])
        if not bars or bars[-1].date < stale_cutoff:
            stale_tickers.append(t)
            out[t] = list(bars[-n_bars:])
            continue
        out[t] = list(bars[-n_bars:])

    fallback_ready = (
        stale_tickers
        and kite_client is not None
        and ticker_to_token is not None
    )
    if fallback_ready:
        _logger.info(
            "preload_daily_bars: %d ticker(s) stale, falling back "
            "to Kite historical: %s",
            len(stale_tickers), stale_tickers[:5],
        )
        fallback = _kite_fallback(
            kite_client, ticker_to_token, stale_tickers, n_bars, today,
        )
        for t, bars in fallback.items():
            if bars:
                out[t] = list(bars[-n_bars:])
    elif stale_tickers:
        _logger.warning(
            "preload_daily_bars: %d ticker(s) stale and no Kite "
            "fallback available — strategies on stale tickers will "
            "use whatever partial history Iceberg has",
            len(stale_tickers),
        )

    avg = sum(len(b) for b in out.values()) // max(len(out), 1)
    _logger.info(
        "preload_daily_bars: %d ticker(s) loaded, avg %d bars/ticker",
        len(out), avg,
    )
    return out


def _kite_fallback(
    kite_client: Any,
    ticker_to_token: dict[str, int],
    tickers: list[str],
    n_bars: int,
    today: date,
) -> dict[str, list[BarData]]:
    """Per-ticker historical_data fetch via KiteClient.

    Tickers missing from ``ticker_to_token`` are skipped (instruments
    master never resolved → no token to query Kite with). The 3 req/sec
    rate-limit guard lives inside
    :py:meth:`KiteClient.fetch_daily_historical` so this loop is
    sequential and safe.
    """
    out: dict[str, list[BarData]] = {}
    for t in tickers:
        token = ticker_to_token.get(t)
        if token is None:
            _logger.warning(
                "Kite fallback skipped for %s: no instrument_token "
                "in ticker_to_token map (instruments master may "
                "need refresh)", t,
            )
            continue
        try:
            bars = kite_client.fetch_daily_historical(
                ticker=t,
                instrument_token=int(token),
                n_bars=n_bars,
                end=today,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Kite historical fetch failed for %s: %s — ticker "
                "stays with whatever Iceberg returned", t, exc,
            )
            continue
        out[t] = bars
    return out


def initial_running_bar(
    ticker: str, today: date, ltp: Decimal, volume: int = 0,
) -> BarData:
    """Build today's running daily bar from the first observed tick.

    open / high / low / close all start at ``ltp`` (single-tick
    degenerate candle); subsequent ticks broaden h/l and update
    close via :func:`update_running_bar`.
    """
    return BarData(
        ticker=ticker,
        date=today,
        open=ltp,
        high=ltp,
        low=ltp,
        close=ltp,
        volume=max(volume, 0),
    )


def update_running_bar(
    running: BarData, *, ltp: Decimal, volume_delta: int = 0,
) -> BarData:
    """Return today's running bar updated with the latest LTP.

    Returns a fresh ``BarData`` (immutable update) — never mutate
    the input; the caller's list aliases the same object.
    """
    return running.model_copy(update={
        "high": max(running.high, ltp),
        "low": min(running.low, ltp),
        "close": ltp,
        "volume": running.volume + max(volume_delta, 0),
    })

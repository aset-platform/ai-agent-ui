"""Intraday bar warmup for LiveRuntime (ASETPLTFRM-392).

Sibling of ``daily_bar_warmup`` — preloads closed intraday bars
(15m / 5m / 1m) per ticker at runtime spawn so the very first
in-session evaluation sees a stable indicator series instead of
building RSI / MACD from a handful of session-local minutes.

Source priority
---------------
1. ``algo.intraday_bars`` Iceberg table — bars stamped by the live
   tick stream's resampler. Schema documented in
   ``backend/algo/iceberg_init.py::_intraday_bars_schema``: keyed
   by ``(ticker, bar_date, interval_sec, bar_open_ts_ns)``.
2. ``KiteClient.fetch_intraday_historical`` per-ticker fallback for
   any ticker whose Iceberg history is empty or doesn't reach back
   the requested window.

Out of scope here: today's running bar, eval-time gate, cadence
selection in the runtime — those live in ``runtime.py``.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from backend.algo.backtest.types import BarData

_logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Default bar count: RSI(14) + MACD(26 + 9) need ~35 bars to settle.
# 100 leaves ~65 bars of headroom for any future longer-lookback
# indicator added without re-tuning the warmup.
DEFAULT_INTRADAY_WARMUP_BARS = 100

# Trading-day calendar widening: same idea as the daily warmup but
# stretched to absorb intraday quirks (half-day sessions on Diwali
# / Budget day reduce the per-day bar count without breaking the
# pull). 30 calendar days covers ~22 trading days = ~1650 5-min
# bars — plenty for a 100-bar warmup even with a holiday-heavy
# window.
MAX_CALENDAR_DAYS = 30

# Supported cadences. Kept in sync with
# ``ScheduleBarClose.interval`` literals and the
# ``_INTRADAY_INTERVAL_MAP`` in ``KiteClient``.
INTERVAL_SEC_BY_LABEL: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
}


def _today_ist() -> date:
    return datetime.now(IST).date()


def _iceberg_window_start(today: date) -> date:
    return today - timedelta(days=MAX_CALENDAR_DAYS)


def _label_for_interval(interval_sec: int) -> str:
    for label, secs in INTERVAL_SEC_BY_LABEL.items():
        if secs == interval_sec:
            return label
    return f"{interval_sec}s"


def preload_intraday_bars(
    tickers: list[str],
    *,
    interval_sec: int,
    n_bars: int = DEFAULT_INTRADAY_WARMUP_BARS,
    kite_client: Any | None = None,
    ticker_to_token: dict[str, int] | None = None,
    today: date | None = None,
    iceberg_reader: Any | None = None,
) -> dict[str, list[BarData]]:
    """Preload closed intraday bars for ``tickers``.

    Returns ``{ticker: list[BarData]}`` with up to ``n_bars`` per
    ticker, ascending by ``bar_open_ts_ns``. Closed bars only.

    Parameters
    ----------
    tickers : list[str]
        Universe to preload. Empty list → empty dict.
    interval_sec : int
        Bar window in seconds. One of 60 / 300 / 900.
    n_bars : int
        Bars per ticker. Default 100.
    kite_client : KiteClient | None
        For the per-ticker fallback when Iceberg is empty / sparse.
        ``None`` disables fallback (warning logged).
    ticker_to_token : dict[str, int] | None
        Required for Kite fallback. ``None`` disables fallback
        regardless of ``kite_client``.
    today : date | None
        Inject for testing; defaults to today in IST.
    iceberg_reader : Callable | None
        Inject for testing — receives (tickers, interval_sec,
        window_start, today) and returns
        ``{ticker: list[BarData]}``. Defaults to the real DuckDB
        query path.
    """
    if not tickers:
        return {}
    if interval_sec not in INTERVAL_SEC_BY_LABEL.values():
        raise ValueError(
            f"interval_sec={interval_sec} not supported. Valid: "
            f"{sorted(INTERVAL_SEC_BY_LABEL.values())}.",
        )
    today = today or _today_ist()
    window_start = _iceberg_window_start(today)
    reader = iceberg_reader or _default_iceberg_reader

    iceberg: dict[str, list[BarData]] = {}
    try:
        iceberg = reader(
            tickers, interval_sec, window_start, today,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "preload_intraday_bars: Iceberg read failed: %s — every "
            "ticker falls back to Kite (if available)",
            exc, exc_info=True,
        )

    out: dict[str, list[BarData]] = {}
    underfilled: list[str] = []
    for t in tickers:
        bars = iceberg.get(t, [])
        if len(bars) < n_bars:
            underfilled.append(t)
        out[t] = list(bars[-n_bars:])

    fallback_ready = (
        underfilled
        and kite_client is not None
        and ticker_to_token is not None
    )
    if fallback_ready:
        _logger.info(
            "preload_intraday_bars: %d ticker(s) underfilled at "
            "interval=%s — falling back to Kite historical: %s",
            len(underfilled),
            _label_for_interval(interval_sec),
            underfilled[:5],
        )
        fallback = _kite_fallback(
            kite_client,
            ticker_to_token,
            underfilled,
            interval_sec,
            n_bars,
            today,
        )
        for t, bars in fallback.items():
            if bars:
                # Merge: keep whichever source returned more bars so
                # we get the densest history possible.
                existing = out.get(t, [])
                if len(bars) > len(existing):
                    out[t] = list(bars[-n_bars:])
    elif underfilled:
        _logger.warning(
            "preload_intraday_bars: %d ticker(s) underfilled at "
            "interval=%s and no Kite fallback available — RSI / "
            "MACD will be silent on those tickers until bars "
            "accumulate in-session",
            len(underfilled),
            _label_for_interval(interval_sec),
        )

    avg = sum(len(b) for b in out.values()) // max(len(out), 1)
    _logger.info(
        "preload_intraday_bars: %d ticker(s) loaded at interval=%s,"
        " avg %d bars/ticker",
        len(out), _label_for_interval(interval_sec), avg,
    )
    return out


def _default_iceberg_reader(
    tickers: list[str],
    interval_sec: int,
    window_start: date,
    today: date,
) -> dict[str, list[BarData]]:
    """Read ``algo.intraday_bars`` via DuckDB.

    Filters by ``ticker IN (...)`` + ``interval_sec`` + bar_date
    window. Returns one list per ticker, ascending by
    ``bar_open_ts_ns``.
    """
    from backend.db.duckdb_engine import query_iceberg_table

    if not tickers:
        return {}
    placeholders = ", ".join(["?"] * len(tickers))
    sql = (
        "SELECT ticker, bar_date, bar_open_ts_ns, "
        "       open, high, low, close, volume "
        "FROM intraday_bars "
        f"WHERE ticker IN ({placeholders}) "
        "  AND interval_sec = ? "
        "  AND bar_date >= ? "
        "  AND bar_date <= ? "
        "ORDER BY ticker, bar_open_ts_ns"
    )
    params = (
        list(tickers)
        + [interval_sec, window_start.isoformat(), today.isoformat()]
    )
    rows = query_iceberg_table("algo.intraday_bars", sql, params)

    out: dict[str, list[BarData]] = {t: [] for t in tickers}
    from datetime import date as _date
    for r in rows:
        t = r["ticker"]
        try:
            o = Decimal(str(r["open"]))
            h = Decimal(str(r["high"]))
            lo = Decimal(str(r["low"]))
            c = Decimal(str(r["close"]))
        except Exception:  # noqa: BLE001
            continue
        if any(x.is_nan() for x in (o, h, lo, c)):
            continue
        d_raw = r["bar_date"]
        d_obj = (
            d_raw if isinstance(d_raw, _date)
            else _date.fromisoformat(str(d_raw)[:10])
        )
        out.setdefault(t, []).append(BarData(
            ticker=t,
            date=d_obj,
            open=o,
            high=h,
            low=lo,
            close=c,
            volume=int(r.get("volume") or 0),
            bar_open_ts_ns=int(r["bar_open_ts_ns"]),
        ))
    return out


def _kite_fallback(
    kite_client: Any,
    ticker_to_token: dict[str, int],
    tickers: list[str],
    interval_sec: int,
    n_bars: int,
    today: date,
) -> dict[str, list[BarData]]:
    """Per-ticker ``fetch_intraday_historical`` loop, rate-limited
    inside KiteClient. Mirrors ``daily_bar_warmup._kite_fallback``.
    """
    out: dict[str, list[BarData]] = {}
    for t in tickers:
        token = ticker_to_token.get(t)
        if token is None:
            _logger.warning(
                "Kite intraday fallback skipped for %s: no "
                "instrument_token in ticker_to_token (instruments "
                "master may need refresh)",
                t,
            )
            continue
        try:
            bars = kite_client.fetch_intraday_historical(
                ticker=t,
                instrument_token=int(token),
                interval_sec=interval_sec,
                n_bars=n_bars,
                end=today,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Kite intraday fetch failed for %s at %s: %s — "
                "ticker stays with whatever Iceberg returned",
                t, _label_for_interval(interval_sec), exc,
                exc_info=True,
            )
            continue
        out[t] = bars
    return out

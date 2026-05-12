"""Tests for backend.algo.live.daily_bar_warmup (ASETPLTFRM-383).

Covers:
  1. Empty universe → empty dict, no I/O.
  2. Happy path: every ticker fresh in Iceberg → no Kite call.
  3. Stale path: one ticker stale → Kite fallback used.
  4. Mixed fresh/stale → Kite called only for the stale ones.
  5. Iceberg returns empty for a ticker → Kite fallback used.
  6. No Kite client + stale Iceberg → warning logged, partial bars
     returned (no crash).
  7. n_bars slicing: more bars in Iceberg than requested → output
     truncated to the most recent ``n_bars``.
  8. ``initial_running_bar`` builds a degenerate single-tick candle.
  9. ``update_running_bar`` broadens h/l, advances close, accumulates
     volume — never mutates the input.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

from backend.algo.backtest.types import BarData
from backend.algo.live.daily_bar_warmup import (
    MAX_STALENESS_DAYS,
    initial_running_bar,
    preload_daily_bars,
    update_running_bar,
)


_TODAY = date(2026, 5, 12)


def _bar(ticker: str, d: date, close: float = 100.0) -> BarData:
    """Build a deterministic test bar."""
    c = Decimal(str(close))
    return BarData(
        ticker=ticker,
        date=d,
        open=c,
        high=c + Decimal("1"),
        low=c - Decimal("1"),
        close=c,
        volume=1_000,
    )


def _series(ticker: str, end: date, n: int) -> list[BarData]:
    """N consecutive daily bars ending at ``end``, ascending."""
    return [
        _bar(ticker, end - timedelta(days=(n - 1 - i)), 100.0 + i)
        for i in range(n)
    ]


class _StubKite:
    """Records every fetch_daily_historical call so the test can
    assert which tickers the fallback fired for."""

    def __init__(
        self, payload: dict[str, list[BarData]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self._payload = payload or {}

    def fetch_daily_historical(
        self,
        *,
        ticker: str,
        instrument_token: int,
        n_bars: int,
        end: Any,
    ) -> list[BarData]:
        self.calls.append((ticker, instrument_token, n_bars))
        return self._payload.get(ticker, [])


# Trivial token map shared by stale-path tests.
_TOK = {"ITC": 1, "RELIANCE": 2, "STALE": 3, "NEW": 4}


# ---------------------------------------------------------------
# 1. Empty universe → empty dict, no I/O.
# ---------------------------------------------------------------


def test_empty_universe_returns_empty_dict() -> None:
    out = preload_daily_bars([], today=_TODAY)
    assert out == {}


# ---------------------------------------------------------------
# 2. Happy path: every ticker fresh → no Kite call.
# ---------------------------------------------------------------


def test_happy_path_fresh_iceberg_no_kite_call() -> None:
    iceberg = {
        "ITC": _series("ITC", _TODAY - timedelta(days=1), 250),
        "RELIANCE": _series(
            "RELIANCE", _TODAY - timedelta(days=1), 250,
        ),
    }
    kite = _StubKite()
    with patch(
        "backend.algo.live.daily_bar_warmup.load_ohlcv_window",
        return_value=iceberg,
    ):
        out = preload_daily_bars(
            ["ITC", "RELIANCE"],
            n_bars=250,
            kite_client=kite,
            ticker_to_token=_TOK,
            today=_TODAY,
        )
    assert set(out.keys()) == {"ITC", "RELIANCE"}
    assert len(out["ITC"]) == 250
    assert len(out["RELIANCE"]) == 250
    assert kite.calls == []  # no fallback needed


# ---------------------------------------------------------------
# 3. Stale path: one ticker stale → Kite fallback used.
# ---------------------------------------------------------------


def test_stale_ticker_triggers_kite_fallback() -> None:
    stale_end = _TODAY - timedelta(days=MAX_STALENESS_DAYS + 2)
    iceberg = {"ITC": _series("ITC", stale_end, 250)}
    kite_payload = {
        "ITC": _series("ITC", _TODAY - timedelta(days=1), 250),
    }
    kite = _StubKite(payload=kite_payload)
    with patch(
        "backend.algo.live.daily_bar_warmup.load_ohlcv_window",
        return_value=iceberg,
    ):
        out = preload_daily_bars(
            ["ITC"],
            n_bars=250,
            kite_client=kite,
            ticker_to_token=_TOK,
            today=_TODAY,
        )
    assert kite.calls == [("ITC", 1, 250)]
    # Returned bars are from Kite (newer), not Iceberg.
    assert out["ITC"][-1].date == _TODAY - timedelta(days=1)


# ---------------------------------------------------------------
# 4. Mixed fresh / stale → Kite called only for the stale ones.
# ---------------------------------------------------------------


def test_mixed_freshness_only_stale_hits_kite() -> None:
    fresh_end = _TODAY - timedelta(days=1)
    stale_end = _TODAY - timedelta(days=MAX_STALENESS_DAYS + 3)
    iceberg = {
        "ITC": _series("ITC", fresh_end, 250),
        "STALE": _series("STALE", stale_end, 50),
    }
    kite_payload = {
        "STALE": _series("STALE", fresh_end, 250),
    }
    kite = _StubKite(payload=kite_payload)
    with patch(
        "backend.algo.live.daily_bar_warmup.load_ohlcv_window",
        return_value=iceberg,
    ):
        out = preload_daily_bars(
            ["ITC", "STALE"],
            n_bars=250,
            kite_client=kite,
            ticker_to_token=_TOK,
            today=_TODAY,
        )
    assert kite.calls == [("STALE", 3, 250)]
    assert len(out["ITC"]) == 250
    assert len(out["STALE"]) == 250
    assert out["STALE"][-1].date == fresh_end


# ---------------------------------------------------------------
# 5. Iceberg returns empty for a ticker → Kite fallback used.
# ---------------------------------------------------------------


def test_iceberg_empty_for_ticker_falls_back_to_kite() -> None:
    iceberg: dict[str, list[BarData]] = {}
    kite = _StubKite(payload={
        "NEW": _series("NEW", _TODAY - timedelta(days=1), 250),
    })
    with patch(
        "backend.algo.live.daily_bar_warmup.load_ohlcv_window",
        return_value=iceberg,
    ):
        out = preload_daily_bars(
            ["NEW"], n_bars=250, kite_client=kite,
            ticker_to_token=_TOK, today=_TODAY,
        )
    assert kite.calls == [("NEW", 4, 250)]
    assert len(out["NEW"]) == 250


# ---------------------------------------------------------------
# 6. No Kite client + stale Iceberg → partial bars returned.
# ---------------------------------------------------------------


def test_no_kite_with_stale_returns_partial_no_crash() -> None:
    stale_end = _TODAY - timedelta(days=MAX_STALENESS_DAYS + 1)
    iceberg = {"ITC": _series("ITC", stale_end, 50)}
    with patch(
        "backend.algo.live.daily_bar_warmup.load_ohlcv_window",
        return_value=iceberg,
    ):
        out = preload_daily_bars(
            ["ITC"], n_bars=250, kite_client=None, today=_TODAY,
        )
    # 50 partial bars survive — caller silent-skips indicators
    # until the next backfill.
    assert len(out["ITC"]) == 50
    assert out["ITC"][-1].date == stale_end


# ---------------------------------------------------------------
# 6b. Kite client present but ticker_to_token=None → fallback
#     still skipped (warning logged).
# ---------------------------------------------------------------


def test_no_token_map_disables_fallback() -> None:
    stale_end = _TODAY - timedelta(days=MAX_STALENESS_DAYS + 1)
    iceberg = {"ITC": _series("ITC", stale_end, 50)}
    kite = _StubKite(payload={
        "ITC": _series("ITC", _TODAY - timedelta(days=1), 250),
    })
    with patch(
        "backend.algo.live.daily_bar_warmup.load_ohlcv_window",
        return_value=iceberg,
    ):
        out = preload_daily_bars(
            ["ITC"],
            n_bars=250,
            kite_client=kite,
            ticker_to_token=None,
            today=_TODAY,
        )
    # Fallback didn't fire — Kite was usable but token map missing.
    assert kite.calls == []
    assert len(out["ITC"]) == 50


def test_token_map_missing_ticker_skips_fallback_for_it() -> None:
    """One stale ticker has no token entry → that ticker stays
    with partial Iceberg; others fall back normally."""
    stale_end = _TODAY - timedelta(days=MAX_STALENESS_DAYS + 1)
    iceberg = {
        "STALE": _series("STALE", stale_end, 30),
        "UNKNOWN": _series("UNKNOWN", stale_end, 20),
    }
    kite = _StubKite(payload={
        "STALE": _series(
            "STALE", _TODAY - timedelta(days=1), 250,
        ),
    })
    partial_tok = {"STALE": 3}  # UNKNOWN intentionally absent
    with patch(
        "backend.algo.live.daily_bar_warmup.load_ohlcv_window",
        return_value=iceberg,
    ):
        out = preload_daily_bars(
            ["STALE", "UNKNOWN"],
            n_bars=250,
            kite_client=kite,
            ticker_to_token=partial_tok,
            today=_TODAY,
        )
    assert kite.calls == [("STALE", 3, 250)]
    assert len(out["STALE"]) == 250
    assert len(out["UNKNOWN"]) == 20  # only iceberg partial


# ---------------------------------------------------------------
# 7. n_bars slicing → only the most recent N kept.
# ---------------------------------------------------------------


def test_n_bars_slicing_keeps_most_recent() -> None:
    iceberg = {
        "ITC": _series("ITC", _TODAY - timedelta(days=1), 500),
    }
    with patch(
        "backend.algo.live.daily_bar_warmup.load_ohlcv_window",
        return_value=iceberg,
    ):
        out = preload_daily_bars(
            ["ITC"], n_bars=100, today=_TODAY,
        )
    assert len(out["ITC"]) == 100
    assert out["ITC"][-1].date == _TODAY - timedelta(days=1)
    # First bar is 99 trading days before the last (no holidays
    # in the synthetic series — calendar days == trading days).
    assert out["ITC"][0].date == _TODAY - timedelta(days=100)


# ---------------------------------------------------------------
# 8. initial_running_bar → degenerate single-tick candle.
# ---------------------------------------------------------------


def test_initial_running_bar_single_tick() -> None:
    bar = initial_running_bar(
        "ITC", _TODAY, Decimal("100.50"), volume=200,
    )
    assert bar.ticker == "ITC"
    assert bar.date == _TODAY
    assert bar.open == bar.high == bar.low == bar.close == (
        Decimal("100.50")
    )
    assert bar.volume == 200


def test_initial_running_bar_zero_volume_default() -> None:
    bar = initial_running_bar("ITC", _TODAY, Decimal("100"))
    assert bar.volume == 0


# ---------------------------------------------------------------
# 9. update_running_bar → broadens h/l, advances close, never
#    mutates the input.
# ---------------------------------------------------------------


def test_update_running_bar_broadens_and_advances() -> None:
    seed = initial_running_bar(
        "ITC", _TODAY, Decimal("100"), volume=10,
    )
    higher = update_running_bar(
        seed, ltp=Decimal("102"), volume_delta=5,
    )
    assert higher.high == Decimal("102")
    assert higher.low == Decimal("100")
    assert higher.close == Decimal("102")
    assert higher.volume == 15
    # Input unchanged (immutability contract).
    assert seed.high == Decimal("100")
    assert seed.close == Decimal("100")
    assert seed.volume == 10

    lower = update_running_bar(
        higher, ltp=Decimal("98"), volume_delta=3,
    )
    assert lower.high == Decimal("102")    # unchanged from prior
    assert lower.low == Decimal("98")      # broadened down
    assert lower.close == Decimal("98")
    assert lower.volume == 18


def test_update_running_bar_negative_volume_delta_clamped() -> None:
    """A drop in cumulative volume (Kite quirk on session rollover)
    should never decrement our running tally."""
    seed = initial_running_bar(
        "ITC", _TODAY, Decimal("100"), volume=50,
    )
    out = update_running_bar(
        seed, ltp=Decimal("101"), volume_delta=-10,
    )
    assert out.volume == 50  # unchanged
